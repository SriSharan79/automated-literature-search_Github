from alr.common.System_prompts import General_Sys_Prompt
from alr.common.general_utils import caluculate_time_taken, print_with_separator
from alr.common.LLM_Config import BLABLADOR_BASE_URL, PREFERRED_BLABLADOR_MODELS, check_api_key, get_stored_api_key,local_model_dir,model_repo_id, OLLAMA_BASE_URL, DEFAULT_BLABLADOR_MODEL, DEFAULT_OLLAMA_MODEL
from alr.common.file_manager import ALR_main_folder

from collections import deque
# NOTE: transformers/torch are heavyweight and only needed for the local
# Hugging Face model path; they are imported lazily inside hf_pipeline_with_Lamma().
from colorama import Fore, Style, init
import pandas as pd
from datetime import datetime
import traceback                  
import sys
import textwrap
import requests
import json
import os
import tiktoken
import re # Import regex
import threading
from typing import List,Dict,Any
init(autoreset=True)
import time


# ---------------------------------------------------------------------------
# Shared rate limiter (one budget for every remote chat + embedding request)
# ---------------------------------------------------------------------------
# Sliding window: at most RATE_MAX_REQUESTS requests per RATE_WINDOW_SECONDS,
# shared across blabla_ask_llm / Ollama_ask_llm / get_embedding so the limits
# don't stack per-call-site the way the old flat sleeps did.
RATE_MAX_REQUESTS = 10
RATE_WINDOW_SECONDS = 60
REQUEST_TIMES = deque()
_RATE_LOCK = threading.Lock()


def _respect_rate_limit():
    """Block until a request slot is free, then claim it (thread-safe)."""
    while True:
        with _RATE_LOCK:
            now = time.time()
            while REQUEST_TIMES and now - REQUEST_TIMES[0] >= RATE_WINDOW_SECONDS:
                REQUEST_TIMES.popleft()
            if len(REQUEST_TIMES) < RATE_MAX_REQUESTS:
                REQUEST_TIMES.append(now)
                return
            wait = RATE_WINDOW_SECONDS - (now - REQUEST_TIMES[0])
        print(Fore.YELLOW
              + f"⚠️ Rate limit reached ({RATE_MAX_REQUESTS} requests/{RATE_WINDOW_SECONDS}s). "
              + f"Waiting {wait:.1f}s..." + Style.RESET_ALL)
        time.sleep(max(wait, 0.1))


# ---------------------------------------------------------------------------
# HTTP with native timeouts + bounded retry (replaces the thread-based
# timeout_function: a hung socket now raises cleanly instead of leaking an
# abandoned worker thread that keeps running in the background)
# ---------------------------------------------------------------------------
RETRYABLE_STATUS = (429, 500, 502, 503, 504)
CONNECT_TIMEOUT_SECONDS = 10


def _post_with_retries(url, headers, payload, timeout, service, max_retries=3):
    """
    POST with a native requests timeout and retry-with-backoff on transient
    failures (429/5xx, connection errors, timeouts). Retry-After is honoured.
    Non-retryable HTTP errors (401, 404, ...) raise immediately; after
    max_retries attempts the last error raises to the caller.
    """
    delay = 2.0
    last_exc = None
    for attempt in range(1, max_retries + 1):
        _respect_rate_limit()
        try:
            resp = requests.post(url, headers=headers, json=payload,
                                 timeout=(CONNECT_TIMEOUT_SECONDS, timeout))
            if resp.status_code in RETRYABLE_STATUS:
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        pass
                last_exc = requests.HTTPError(
                    f"{service}: HTTP {resp.status_code}", response=resp)
            else:
                resp.raise_for_status()
                return resp
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
        if attempt < max_retries:
            print(Fore.YELLOW
                  + f"⚠️ {service} request failed ({last_exc}); "
                  + f"retrying in {delay:.0f}s (attempt {attempt}/{max_retries})..."
                  + Style.RESET_ALL)
            time.sleep(delay)
            delay *= 2
    raise last_exc


# ---------------------------------------------------------------------------
# Record of what actually served the most recent llm_call / embedding_call -
# callers can persist this next to their outputs (e.g. a model_used column).
# ---------------------------------------------------------------------------
LAST_CALL_INFO = {}


def _record_call_info(**kw):
    global LAST_CALL_INFO
    LAST_CALL_INFO = {"timestamp": datetime.now().isoformat(), **kw}


def get_last_call_info() -> dict:
    """
    Return details of the most recent llm_call()/embedding_call():
    requested_service, service_used, model_used, fallback_used, error.
    When fallback_used is True the answer came from a different service
    than requested - record this wherever the response is stored.
    """
    return dict(LAST_CALL_INFO)


# ---------------------------------------------------------------------------
# Runtime-configurable model selection
# ---------------------------------------------------------------------------
# The models used for each remote service. These start at the configured
# defaults (preserving previous behaviour) but can be changed at runtime, e.g.
# via select_model_interactive() from the CLI or a dropdown in the desktop UI.
SELECTED_MODELS = {
    "BlaBla": DEFAULT_BLABLADOR_MODEL,
    "DLR Ollama": DEFAULT_OLLAMA_MODEL,
}


def get_selected_model(service: str) -> str:
    """Return the currently selected model for a service ('BlaBla' or 'DLR Ollama')."""
    return SELECTED_MODELS.get(service)


def set_selected_model(service: str, model: str) -> None:
    """Set the model to use for a service for the rest of the session."""
    if service not in SELECTED_MODELS:
        raise ValueError(f"Unknown service '{service}'. Expected one of {list(SELECTED_MODELS)}.")
    SELECTED_MODELS[service] = model
    print(Fore.GREEN + f"✅ {service} model set to: {model}" + Style.RESET_ALL)


def list_blablador_models(blablador_key: str = None) -> list:
    """Fetch the list of currently available Blablador model ids (live call)."""
    # Non-prompting: this is called from the UI, so never block on console input.
    key = blablador_key or get_stored_api_key('BlaBla Door')
    if not key:
        print(Fore.YELLOW + "⚠️ No Blablador API key - cannot list models." + Style.RESET_ALL)
        return []
    try:
        resp = requests.get(
            f"{BLABLADOR_BASE_URL}/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=30,
        )
        resp.raise_for_status()
        return [m['id'] for m in resp.json().get('data', [])]
    except Exception as e:
        print(Fore.RED + f"❌ Failed to list Blablador models: {e}" + Style.RESET_ALL)
        return []


def list_ollama_models(ollama_key: str = None) -> list:
    """Fetch the list of currently available DLR Ollama model ids (live call)."""
    # Non-prompting: this is called from the UI, so never block on console input.
    key = ollama_key or get_stored_api_key('DLR Ollama')
    headers = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        resp = requests.get(f"{OLLAMA_BASE_URL}/models", headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        # with open("models_cache.json", 'w') as f:
        #     json.dump(data, f, indent=2)
        # OpenAI-compatible ({"data": [{"id": ...}]}) or native Ollama ({"models": [{"name": ...}]})
        if isinstance(data, dict) and data.get('data'):
            return [m.get('id') for m in data['data'] if m.get('id')]
        if isinstance(data, dict) and data.get('models'):
            return [m.get('name') or m.get('model') for m in data['models'] if (m.get('name') or m.get('model'))]
        return []
    except Exception as e:
        print(Fore.RED + f"❌ Failed to list DLR Ollama models: {e}" + Style.RESET_ALL)
        return []


def list_available_models(service: str) -> list:
    """Return live available model ids for 'BlaBla' or 'DLR Ollama'."""
    if service == "BlaBla":
        return list_blablador_models()
    if service == "DLR Ollama":
        return list_ollama_models()
    raise ValueError(f"Unknown service '{service}'.")


def select_model_interactive(service: str) -> str:
    """
    Fetch the live list of available models for a service, show it to the user,
    and let them pick one. The chosen model is stored for the rest of the session
    and returned. Pressing Enter keeps the current selection.
    """
    current = get_selected_model(service)
    models = list_available_models(service)

    if not models:
        print(Fore.YELLOW + f"⚠️ No models returned for {service}; keeping current: {current}" + Style.RESET_ALL)
        return current

    print(Fore.CYAN + f"\nAvailable {service} models:" + Style.RESET_ALL)
    for i, m in enumerate(models, 1):
        marker = "  (current)" if m == current else ""
        print(f"  {i}. {m}{marker}")

    while True:
        choice = input(f"Select a {service} model [1-{len(models)}], or Enter to keep '{current}': ").strip()
        if choice == "":
            print(Fore.GREEN + f"Keeping current {service} model: {current}" + Style.RESET_ALL)
            return current
        if choice.isdigit() and 1 <= int(choice) <= len(models):
            chosen = models[int(choice) - 1]
            set_selected_model(service, chosen)
            return chosen
        print(Fore.RED + "Invalid selection, please try again." + Style.RESET_ALL)


# ---------------------------------------------------------------------------
# Embedding model discovery & selection
# ---------------------------------------------------------------------------
# Preferred default embedding model. If it shows up in the live model list for
# a service, it is used; otherwise we fall back to the first embedding model
# found (or to this name anyway, in case listing fails).
DEFAULT_EMBEDDING_MODEL = "qwen3-8b-embeddings"

SELECTED_EMBEDDING_MODELS = {
    "BlaBla": None,
    "DLR Ollama": None,
}


def _filter_embedding_models(models: list) -> list:
    """Return only the models whose id/name contains 'embed' (case-insensitive)."""
    return [m for m in (models or []) if m and "embed" in m.lower()]


def list_embedding_models(service: str) -> list:
    """
    Fetch the live model list for a service ('BlaBla' or 'DLR Ollama') via the
    existing /models call, and return only the ones that look like embedding
    models (id/name contains 'embed').
    """
    all_models = list_available_models(service)
    embedding_models = _filter_embedding_models(all_models)
    return embedding_models


def get_default_embedding_model(service: str, preferred: str = DEFAULT_EMBEDDING_MODEL) -> str:
    """
    Resolve which embedding model to use for `service`.

    Preference order:
      1. Embedding model already selected earlier this session.
      2. `preferred` ('qwen3-8b-embeddings' by default), if it is present in
         the live embedding-model list.
      3. First embedding model found in the live list.
      4. `preferred` as a last-resort fallback if the live list is empty
         (e.g. the /models call failed).
    """
    if SELECTED_EMBEDDING_MODELS.get(service):
        return SELECTED_EMBEDDING_MODELS[service]

    embedding_models = list_embedding_models(service)

    if not embedding_models:
        print(Fore.YELLOW + f"⚠️ No embedding models found for {service}; falling back to '{preferred}'." + Style.RESET_ALL)
        SELECTED_EMBEDDING_MODELS[service] = preferred
        return preferred

    # Case-insensitive match against the preferred model name: exact match,
    # or the preferred name appearing as a substring (e.g. preferred
    # 'qwen3-8b-embeddings' matching a served id like 'qwen3-8b-embeddings-Q4').
    for m in embedding_models:
        if m.lower() == preferred.lower() or preferred.lower() in m.lower():
            SELECTED_EMBEDDING_MODELS[service] = m
            print(Fore.GREEN + f"✅ {service} default embedding model: {m}" + Style.RESET_ALL)
            return m

    # Preferred model not available for this service; use the first one found.
    chosen = embedding_models[0]
    SELECTED_EMBEDDING_MODELS[service] = chosen
    print(Fore.YELLOW + f"⚠️ '{preferred}' not available for {service}; using '{chosen}' instead." + Style.RESET_ALL)
    return chosen


def set_selected_embedding_model(service: str, model: str) -> None:
    """Set the embedding model to use for a service for the rest of the session."""
    if service not in SELECTED_EMBEDDING_MODELS:
        raise ValueError(f"Unknown service '{service}'. Expected one of {list(SELECTED_EMBEDDING_MODELS)}.")
    SELECTED_EMBEDDING_MODELS[service] = model
    print(Fore.GREEN + f"✅ {service} embedding model set to: {model}" + Style.RESET_ALL)


# Session-wide embedding backend selection (set from the desktop UI).
#   method:  'local' (GPU/CPU HuggingFace model) or 'api' (remote /embeddings).
#            None means "not chosen yet" -> vector_db_updater keeps using its
#            deployment-aware default (local when the weights exist on disk).
#   service: 'BlaBla' or 'DLR Ollama' (only relevant when method == 'api').
SELECTED_EMBEDDING_BACKEND = {"method": None, "service": None}


def set_embedding_backend(method: str = None, service: str = None) -> None:
    """
    Set the session-wide embedding backend used by vector_db_updater's
    vectorize_strings()/search_similar() defaults (cosine evaluation, RAG
    vector-DB builds, vector queries). Pass only what you want to change.
    """
    if method is not None:
        method = str(method).strip().lower()
        if method not in ("local", "api"):
            raise ValueError(f"Unknown embedding method '{method}'. Expected 'local' or 'api'.")
        SELECTED_EMBEDDING_BACKEND["method"] = method
    if service is not None:
        if service not in ("BlaBla", "DLR Ollama"):
            raise ValueError(f"Unknown service '{service}'. Expected 'BlaBla' or 'DLR Ollama'.")
        SELECTED_EMBEDDING_BACKEND["service"] = service
    if SELECTED_EMBEDDING_BACKEND["method"] == "local":
        # 'service' is irrelevant for local embeddings; it is kept only as the
        # emergency API fallback if the local model fails to load. Printing it
        # as the active backend was misleading.
        print(Fore.GREEN
              + f"✅ Embedding backend set to: method=local "
              + f"(local model: {embedding_model_repo_id}; "
              + f"API service '{SELECTED_EMBEDDING_BACKEND['service']}' used only as fallback)"
              + Style.RESET_ALL)
    else:
        print(Fore.GREEN
              + f"✅ Embedding backend set to: method={SELECTED_EMBEDDING_BACKEND['method']}, "
              + f"service={SELECTED_EMBEDDING_BACKEND['service']}"
              + Style.RESET_ALL)


def get_embedding_backend() -> dict:
    """Return the current session embedding-backend selection (copy)."""
    return dict(SELECTED_EMBEDDING_BACKEND)


def get_embedding(
    text,
    service: str,
    model: str = None,
    api_key: str = None,
    timeout: int = 60,
) -> dict:
    """
    Get embedding vector(s) for `text` from the given service's OpenAI-compatible
    /embeddings endpoint.

    Args:
        text: a single string, or a list of strings (batch embedding).
        service: 'BlaBla' or 'DLR Ollama'.
        model: explicit model id to use; otherwise the resolved default
               embedding model for the service is used (qwen3-8b-embeddings
               if available).
        api_key: optional explicit API key override.
        timeout: request timeout in seconds.

    Returns:
        {
            "model": <model actually used>,
            "service": <service>,
            "embeddings": [[...], [...], ...],   # one vector per input, in order
            "raw": <raw JSON response from the API>,
        }
    """
    if service not in ("BlaBla", "DLR Ollama"):
        raise ValueError(f"Unknown service '{service}'. Expected 'BlaBla' or 'DLR Ollama'.")

    inputs = text if isinstance(text, list) else [text]
    resolved_model = model or get_default_embedding_model(service)

    if service == "BlaBla":
        base_url = BLABLADOR_BASE_URL
        # Non-prompting key lookup: get_embedding may be called from the desktop
        # UI, so it must never block on console input() the way check_api_key
        # does. If no key is stored the request goes out unauthenticated and the
        # server returns a clear 401 rather than the app hanging.
        key = api_key or get_stored_api_key('BlaBla Door')
    else:
        base_url = OLLAMA_BASE_URL
        key = api_key or get_stored_api_key('DLR Ollama')

    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    payload = {
        "model": resolved_model,
        "input": inputs,
    }

    url = f"{base_url}/embeddings"

    start_time = time.time()
    resp = _post_with_retries(url, headers, payload, timeout, f"{service} embeddings")
    result = resp.json()
    end_time = time.time()

    try:
        embeddings = None
        if isinstance(result, dict) and result.get("data"):
            # OpenAI-compatible: {"data": [{"embedding": [...], "index": 0}, ...]}
            data_sorted = sorted(result["data"], key=lambda d: d.get("index", 0))
            embeddings = [d["embedding"] for d in data_sorted if "embedding" in d]
        elif isinstance(result, dict) and result.get("embeddings"):
            # Native Ollama batch (/api/embed): {"embeddings": [[...], [...]]}
            embeddings = result["embeddings"]
        elif isinstance(result, dict) and result.get("embedding"):
            # Native Ollama single (/api/embeddings): {"embedding": [...]}
            embeddings = [result["embedding"]]

        if not embeddings:
            raise ValueError("No embedding vectors returned")
    except (KeyError, TypeError, ValueError) as exc:
        print(f"❌ {service} embedding call failed. Full response: {result}")
        raise ValueError(f"Unexpected embedding response format from {service}: {exc}") from exc

    print(
        Fore.GREEN
        + f"✅ {service} embeddings received ({len(embeddings)} vector(s), model={resolved_model}, "
        + f"{caluculate_time_taken(start_time, end_time)})"
        + Style.RESET_ALL
    )

    return {
        "model": resolved_model,
        "service": service,
        "embeddings": embeddings,
        "raw": result,
    }


def save_embedding_result(result: dict, out_dir: str = None, prefix: str = "embedding") -> str:
    """
    Save an embedding result (as returned by get_embedding) to a timestamped
    JSON file. Returns the path to the saved file.
    """
    if out_dir is None:
        out_dir = os.path.join(str(ALR_main_folder), "00_LLM_Log_Data", "embeddings")
    os.makedirs(out_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
    service_tag = str(result.get("service", "unknown")).replace(" ", "_")
    filename = f"{prefix}_{service_tag}_{timestamp}.json"
    path = os.path.join(out_dir, filename)

    embeddings = result.get("embeddings") or []
    payload = {
        "timestamp": datetime.now().isoformat(),
        "service": result.get("service"),
        "model": result.get("model"),
        "num_vectors": len(embeddings),
        "vector_dim": len(embeddings[0]) if embeddings else 0,
        "embeddings": embeddings,
        "raw_response": result.get("raw"),
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(Fore.GREEN + f"💾 Saved embedding result to: {path}" + Style.RESET_ALL)
    return path


# ---------------------------------------------------------------------------
# Local embedding model (GPU/CPU) - HuggingFace
# ---------------------------------------------------------------------------
# NOTE: torch/transformers are heavyweight and only imported lazily inside the
# functions below - this mirrors hf_pipeline_with_Lamma() / Local_Model_call()
# further down in this file, which follow the same lazy-load pattern for the
# local chat model.
embedding_model_repo_id = "Qwen/Qwen3-Embedding-8B"
# base path where the models were stored
embedding_base_path = "/localdata/user/kata_du/LLM Models"
# Adjust the path to your required embedding model directory
local_embedding_model_dir = os.path.join(embedding_base_path, "00_LLM_model", embedding_model_repo_id)

# Lazily-initialised local embedding model/tokenizer (loaded on first use).
_embedding_tokenizer = None
_embedding_model = None


def last_token_pool(last_hidden_states: "Tensor", attention_mask: "Tensor") -> "Tensor":
    """Pool the last non-padding hidden state per sequence (Qwen3-embedding style pooling)."""
    import torch

    left_padding = (attention_mask[:, -1].sum() == attention_mask.shape[0])
    if left_padding:
        return last_hidden_states[:, -1]
    else:
        sequence_lengths = attention_mask.sum(dim=1) - 1
        batch_size = last_hidden_states.shape[0]
        return last_hidden_states[torch.arange(batch_size, device=last_hidden_states.device), sequence_lengths]


def load_embedding_model_and_tokenizer(local_dir: str = None):
    """
    Load the local HuggingFace embedding model + tokenizer from disk
    (GPU if available, else CPU fallback). Mirrors the local-loading pattern
    used by hf_pipeline_with_Lamma() for the chat model.
    """
    import torch
    from transformers import AutoTokenizer, AutoModel

    local_dir = local_dir or local_embedding_model_dir
    if not local_dir:
        raise ValueError("local_embedding_model_dir is empty/None")

    print_with_separator("DebugLog", '/')
    try:
        print(f"\nLoading embedding tokenizer from local path: {local_dir}")
        tokenizer = AutoTokenizer.from_pretrained(
            local_dir,
            padding_side="left",
            local_files_only=True,
            trust_remote_code=True,
        )

        print(f"Loading embedding model from local path: {local_dir}")
        use_cuda = torch.cuda.is_available()
        dtype = torch.float16 if use_cuda else torch.float32
        device_map = "auto" if use_cuda else "cpu"

        model = AutoModel.from_pretrained(
            local_dir,
            local_files_only=True,
            trust_remote_code=True,
            torch_dtype=dtype,
            device_map=device_map,
        )
        model.eval()
    except Exception as e:
        print(Fore.RED + f"Error loading local embedding model: {e}" + Fore.RESET)
        print(Fore.RED + "Please ensure the model is correctly downloaded at the specified path and all required libraries (transformers, torch, accelerate) are installed." + Fore.RESET)
        raise

    return tokenizer, model


def _get_embedding_model_and_tokenizer():
    """Load and cache the local embedding model/tokenizer on first use."""
    global _embedding_tokenizer, _embedding_model
    if _embedding_model is None:
        _embedding_tokenizer, _embedding_model = load_embedding_model_and_tokenizer(local_embedding_model_dir)
    return _embedding_tokenizer, _embedding_model


def vectorize_strings_local(input_strings: list, max_length: int = 512, batch_size: int = 10):
    """
    Embed a list of strings using the local GPU/CPU HuggingFace model
    (Qwen/Qwen3-Embedding-8B by default, same weights as before).

    Inputs are pooled in chunks of `batch_size` (default 10) per forward
    pass so large lists (e.g. thousands of section rows) cannot OOM the
    GPU by being tokenised/embedded in a single giant tensor.

    Returns an (N, dim) float32 numpy array, L2-normalised so cosine
    similarity == inner product (matches an IndexFlatIP FAISS index).
    """
    import numpy as np
    import torch
    import torch.nn.functional as F

    tokenizer, model = _get_embedding_model_and_tokenizer()

    if batch_size is None or batch_size < 1:
        batch_size = 10

    chunks = []
    total = len(input_strings)
    with torch.inference_mode():
        for i in range(0, total, batch_size):
            pool = input_strings[i:i + batch_size]
            batch = tokenizer(
                pool,
                padding=True,
                truncation=True,
                max_length=max_length,
                return_tensors="pt",
            )
            batch = {k: v.to(model.device) for k, v in batch.items()}

            outputs = model(**batch)
            emb = last_token_pool(outputs.last_hidden_state, batch["attention_mask"])

            # Normalise => cosine similarity works with inner product / L2
            emb = F.normalize(emb, p=2, dim=1)
            chunks.append(emb.detach().cpu().numpy().astype(np.float32))
            if total > batch_size:
                print(f"   \U0001f9ee Local embeddings: {min(i + batch_size, total)}/{total}")

    return np.concatenate(chunks, axis=0) if chunks else np.zeros((0, 0), dtype=np.float32)


def count_tokens(messages, response_text, model):
    """
    Calculates input and output token counts.
    If tokenisation fails for any reason, returns ("NA", "NA").
    """
    try:
        # Try model-specific encoding first
        try:
            encoding = tiktoken.encoding_for_model(model)
        except Exception:
            # Fallbacks commonly used by OpenAI models
            for name in ("o200k_base", "cl100k_base", "p50k_base", "r50k_base"):
                try:
                    encoding = tiktoken.get_encoding(name)
                    break
                except Exception:
                    encoding = None

        if encoding is None:
            return "NA", "NA"

        # ---- Input tokens ----
        tokens_per_message = 3
        tokens_per_name = 1
        input_tokens = 0

        for message in messages or []:
            input_tokens += tokens_per_message
            for key, value in (message or {}).items():
                if value is None:
                    continue
                input_tokens += len(encoding.encode(str(value)))
                if key == "name":
                    input_tokens += tokens_per_name

        input_tokens += 3  # assistant priming

        # ---- Output tokens ----
        output_tokens = len(
            encoding.encode(str(response_text)) if response_text else []
        )

        return input_tokens, output_tokens

    except Exception:
        # Absolute last-resort safety net
        return "NA", "NA"


def log_llm_interaction(model, service, messages, response_text,time_taken):
    
    # print_with_separator("DebugLog",'/')
    # 1. Format Messages and Calculate Tokens
   
    in_tokens, out_tokens = count_tokens(messages, response_text, model)

    # 2. Setup Directory Structure
    base_log_dir = ALR_main_folder/"00_LLM_Log_Data"
    current_date = datetime.now().strftime("%Y-%m-%d")
    date_folder = os.path.join(base_log_dir, current_date)
    
    if not os.path.exists(date_folder):
        os.makedirs(date_folder)

    # 3. File Names and Timestamps
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_id = datetime.now().strftime("%H-%M-%S_%f")
    json_filename = f"{service}_resp_{file_id}.json"
    json_path = os.path.join(date_folder, json_filename)


    # 4. Save JSON Log
    log_payload = {
        "model": model,
        "service": service,
        "timestamp": timestamp,
        "time_taken": time_taken,
        "messages": messages,
        "response": response_text,
        "usage": {"input": in_tokens, "output": out_tokens}
    }
    
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(log_payload, f, indent=4)

    # 5. Update/Create Excel Log
    new_entry = {
        "Date Time": timestamp,
        "Model": model,
        "Service": service,
        "time_taken": time_taken,
        "File Name": json_filename,
        "Input Tokens": in_tokens,
        "Output Tokens": out_tokens
    }
    try:
        excel_path = os.path.join(base_log_dir, f"{current_date}_master_log.xlsx")    
        df_new = pd.DataFrame([new_entry])

        if os.path.exists(excel_path):
            # Using openpyxl engine to append/update
            df_existing = pd.read_excel(excel_path)
            df_final = pd.concat([df_existing, df_new], ignore_index=True)
        else:
            df_final = df_new

        df_final.to_excel(excel_path, index=False)
        # print(f"Logged interaction to {json_filename} and updated Excel.")
    except Exception as e:
        print('failed to log LLM Interaction in excel')


#DLR_Ollama_Models_Usage
def Ollama_ask_llm(
    prompt: str,
    sys_prompt: str,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    model: str = None,
    timeout: int = 120,
) -> str:

    start_time = time.time()

    # print_with_separator("DebugLog",'/')

    # Resolve the model: explicit arg > session selection > configured default.
    model = model or get_selected_model("DLR Ollama") or DEFAULT_OLLAMA_MODEL

    messages = []
    messages.append({'role': 'system', 'content': sys_prompt})
    messages.append({'role': 'user', 'content': prompt})
    payload = {
        "model": model,
        "messages":messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    headers = {"Content-Type": "application/json"}
    Ollama_DLR_API_Key=check_api_key('DLR Ollama')
    if Ollama_DLR_API_Key:
        headers["Authorization"] = f"Bearer {Ollama_DLR_API_Key}"

    url = f'{OLLAMA_BASE_URL}/chat/completions'
    resp = _post_with_retries(url, headers, payload, timeout, "DLR Ollama")

    result = resp.json()
    content=None 
    try:
        content = result["choices"][0]["message"]["content"]

        # print(Fore.CYAN + "\n--- RAW LLM RESPONSE START (simplified extraction) ---" + Style.RESET_ALL)
        # print(Fore.CYAN + content + Style.RESET_ALL)
        # print(Fore.CYAN + "--- RAW LLM RESPONSE END ---\n" + Style.RESET_ALL)

    except (KeyError, IndexError) as exc:

        print(f"❌ DLR Ollama failed. Full response: {result}")
        content=f"❌ DLR Ollama . Full response: {result}\n Unexpected response format from Blablador: {exc}"
        raise ValueError("Unexpected response format from Ollama") from exc

    end_time = time.time()
    try:
        log_llm_interaction(model,"DLR_Ollama",messages,content.strip(),caluculate_time_taken(start_time,end_time))
    except Exception as e:
        print('failed to log LLM Interaction')

    return content.strip()

#BlaBla Models Usage

def cache_blablador_models(blablador_key: str = None, cache_file: str = "blablador_models_cache.json") -> list:
    """
    Fetch Blablador models and cache RAW response to JSON file.
    """
      
    BlaBla_API_Key = check_api_key('BlaBla Door')
    key = blablador_key or BlaBla_API_Key
    
    if not key:
        print("❌ No API key - skipping cache")
        return []
    
    headers = {"Authorization": f"Bearer {key}"}
    
    try:
        print("🔄 Fetching models from Blablador...")
        resp = requests.get(
            f"{BLABLADOR_BASE_URL}/models", 
            headers=headers, 
            timeout=60
        )
        resp.raise_for_status()
        
        # Cache RAW response directly
        raw_resp = resp.json()
        
        # Save to file
        cache_data = {
            "timestamp": datetime.now().isoformat(),
            "raw_response": raw_resp,
            "status_code": resp.status_code
        }
        
        with open(cache_file, 'w') as f:
            json.dump(cache_data, f, indent=2)
        
        model_ids = [m['id'] for m in raw_resp.get('data', [])]
        print(f"✅ Cached response to {cache_file} ({len(model_ids)} models)")
        
        return model_ids
        
    except Exception as e:
        print(f"❌ Cache failed: {e}")
        return []



def find_best_blablador_model(blablador_key: str = None) -> str:
    """Dynamic model selection from Blablador API."""
      
    BlaBla_API_Key = check_api_key('BlaBla Door')
    key = blablador_key or BlaBla_API_Key
    
    if not key:
        model = PREFERRED_BLABLADOR_MODELS[0]
        print(f"🤖 Using model (no key): {model}")
        return model
    
    headers = {"Authorization": f"Bearer {key}"}
    try:
        resp = requests.get(
            f"{BLABLADOR_BASE_URL}/models", 
            headers=headers, 
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            avail = [m['id'] for m in data.get('data', [])]
            
            # Priority 1: Preferred models
            for p in PREFERRED_BLABLADOR_MODELS:
                if p in avail: 
                    print(f"🤖 Using model (preferred): {p}")
                    return p
            
            # Priority 2: Largest/best models
            for m in avail:
                if "120b" in m.lower() or "gpt-oss" in m.lower():
                    print(f"🤖 Using model (large): {m}")
                    return m
            
            # Priority 3: First available
            if avail:
                model = avail[0]
                print(f"🤖 Using model (first available): {model}")
                return model
    except Exception:
        pass  # Silent fallback
    
    model = "GPT-OSS-120b"
    print(f"🤖 Using model (fallback): {model}")
    return model


def blabla_ask_llm(
    prompt: str,
    sys_prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 8192,
    blablador_key: str = None,
    model: str = None,
    timeout: int = 120,
) -> str:
    """Query Blablador LLM with the selected (or default) model."""

    start_time = time.time()
    # print_with_separator("DebugLog",'/')

    # Resolve the model: explicit arg > session selection > configured default.
    model = model or get_selected_model("BlaBla") or DEFAULT_BLABLADOR_MODEL
    # print(f"🤖 Using model: {model}")
    
    messages = [
        {'role': 'system', 'content': sys_prompt},
        {'role': 'user', 'content': prompt}
    ]
    
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    headers = {"Content-Type": "application/json"}    
    BlaBla_API_Key = check_api_key('BlaBla Door')
    key = blablador_key or BlaBla_API_Key
    if key:
        headers["Authorization"] = f"Bearer {key}"

    url = f"{BLABLADOR_BASE_URL}/chat/completions"

    resp = _post_with_retries(url, headers, payload, timeout, "Blablador")

    result = resp.json()
    content=None 
    try:
        content = result["choices"][0]["message"]["content"]

        # print(Fore.GREEN + f" Blablador sucess. Full response: {result}"+ Style.RESET_ALL)
        
        # ADD THIS NULL CHECK
        if content is None:
            raise ValueError("Empty content received from Blablador")
            
        # print(Fore.CYAN + "\n--- RAW LLM RESPONSE START ---" + Style.RESET_ALL)
        # print(Fore.CYAN + content + Style.RESET_ALL)
        # print(Fore.CYAN + "--- RAW LLM RESPONSE END ---\n" + Style.RESET_ALL)
        
    except (KeyError, IndexError, ValueError) as exc:
        # Print full response for debugging
        print(f"❌ Blablador failed. Full response: {result}")
        content=f"❌ Blablador failed. Full response: {result}\n Unexpected response format from Blablador: {exc}"

        raise ValueError(f"Unexpected response format from Blablador: {exc}") from exc
    

    end_time = time.time()    
    try:
        log_llm_interaction(model,"BlaBla",messages,content.strip(),caluculate_time_taken(start_time,end_time))
    except Exception as e:
        print('failed to log LLM Interaction')

    return content.strip() if content else ""


# hugging face pipline
def hf_pipeline_with_Lamma():

    from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline

    if local_model_dir:
        print_with_separator("DebugLog",'/')
        try:
            print(f"\nLoading tokenizer from local path: {local_model_dir}")
            tokenizer = AutoTokenizer.from_pretrained(local_model_dir, local_files_only=True)

            print(f"Loading model from local path: {local_model_dir}")
            model = AutoModelForCausalLM.from_pretrained(
                local_model_dir,
                local_files_only=True,
                device_map='auto',
            )
            model.eval()
        except Exception as e:
            print(Fore.RED + f"Error loading local Hugging Face model: {e}" + Fore.RESET)
            print(Fore.RED + "Please ensure the model is correctly downloaded at the specified path and all required libraries (transformers, torch, accelerate) are installed." + Fore.RESET)
            exit()

        Hpipeline = pipeline(
            "text-generation",
            model=model,
            tokenizer=tokenizer,
            max_new_tokens=512,
            pad_token_id=tokenizer.eos_token_id,
            do_sample=False, # For more deterministic output, good for format adherence
            return_full_text=False # Crucial for getting only the model's new generation
        )

        return Hpipeline
    else:
        return None



def Local_Model_call(prompt: str, sys_prompt: str) :
    hf_pipeline_default=hf_pipeline_with_Lamma()  
    hf_pipeline=hf_pipeline_default

    if hf_pipeline:
        print_with_separator("DebugLog",'/')
        raw_llm_response_text=None
        try:
            # Format the prompt using the LangChain PromptTemplate for the user message
            # formatted_user_prompt = qa_prompt_template.format(data=Phrase)
            start_time=time.time()
            # Create messages list for the LLM call, including the new strong system prompt

            messages_for_llm_qa = [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": prompt},
            ]

            # Generate chat prompt using the tokenizer's apply_chat_template
            chat_prompt_for_qa = hf_pipeline.tokenizer.apply_chat_template(
                messages_for_llm_qa,
                tokenize=False,
                add_generation_prompt=True # Add assistant's turn
            )

            # Call the local model (hf_pipeline)
            response_from_llm = hf_pipeline(chat_prompt_for_qa)
            llm_response_only = response_from_llm[0]['generated_text']

            # Clean up any special tokens that the tokenizer might add to the response
            llm_response_only = llm_response_only.replace("<|eot_id|>", "").strip()
            llm_response_only = llm_response_only.replace("<|start_header_id|>assistant<|end_header_id|>", "").strip()

            # No longer trying to force JSON braces; the new format is text-based.

            if not llm_response_only.strip():
                print(Fore.RED + "Warning: LLM response is empty after cleaning. This might indicate poor generation." + Style.RESET_ALL)
                raw_llm_response_text = ""
            else:
                raw_llm_response_text = llm_response_only

            print(Fore.CYAN + "\n--- RAW LLM RESPONSE START (simplified extraction) ---" + Style.RESET_ALL)
            print(Fore.CYAN + raw_llm_response_text + Style.RESET_ALL)
            print(Fore.CYAN + "--- RAW LLM RESPONSE END ---\n" + Style.RESET_ALL)
            content=raw_llm_response_text            

            end_time = time.time()
            try:
                log_llm_interaction(model_repo_id,"DLR_Ollama",messages_for_llm_qa,content.strip(),caluculate_time_taken(start_time,end_time))
            except Exception as e:
                print('failed to log LLM Interaction')
            return raw_llm_response_text

        except Exception as e:
            print(Fore.RED + f"An unexpected error occurred during LLM call or pre-parsing: {e}" + Style.RESET_ALL)
            raw_llm_response_text=f"An unexpected error occurred during LLM call or pre-parsing: {e}"
            print(Fore.RED + "Returning empty response due to error." + Style.RESET_ALL)
            traceback.print_exc()              
            end_time = time.time()            
            try:
                log_llm_interaction(model_repo_id,"DLR_Ollama",messages_for_llm_qa,content.strip(),caluculate_time_taken(start_time,end_time))
            except Exception as e:
                print('failed to log LLM Interaction')
            return ""
    else:
        return None


# Main LLM call method
def llm_call(prompt: str, system_prompt: str, service: str, model: str = None,
             allow_fallback: bool = True, timeout: int = 120):
    """
    Chat call with native timeouts, bounded retries, and a *recorded* fallback.

      'B' -> Blablador; 'O' -> DLR Ollama; 'L' -> local HuggingFace model.

    Each remote attempt retries transient failures (429/5xx, timeouts) with
    backoff inside _post_with_retries. Only after the requested service has
    exhausted its retries does the other service get one chance - and only if
    ``allow_fallback`` is True. Pass ``allow_fallback=False`` for calls whose
    results must not mix models mid-batch.

    Whatever happens is recorded in get_last_call_info() (requested_service,
    service_used, model_used, fallback_used, error) so callers can persist
    which model actually produced each output. Returns the response text, or
    None when every allowed service failed.
    """
    # Use provided prompt if valid; otherwise fallback
    if system_prompt:
        sys_prompt = system_prompt
    else:
        sys_prompt = General_Sys_Prompt

    # Normalize service input
    s = service.lower()

    # Optional per-call model override: record it as the session selection for
    # the targeted service so the ask_* helpers pick it up.
    if model:
        if s == 'b':
            set_selected_model("BlaBla", model)
        elif s == 'o':
            set_selected_model("DLR Ollama", model)

    if s == 'l':
        response = Local_Model_call(prompt, sys_prompt)
        _record_call_info(kind="chat", requested_service="local", service_used="local",
                          model_used=model_repo_id, fallback_used=False, error=None)
        return response

    if s not in ('b', 'o'):
        error_msg = "Error: Invalid service. Use 'B', 'O', or 'L'."
        print(error_msg)
        return error_msg  # Return the error so the app doesn't crash downstream

    services = {'b': ("BlaBla", blabla_ask_llm), 'o': ("DLR Ollama", Ollama_ask_llm)}
    primary_name = services[s][0]
    attempt_order = [services[s]]
    if allow_fallback:
        attempt_order.append(services['o' if s == 'b' else 'b'])

    errors = []
    for name, ask in attempt_order:
        try:
            response = ask(prompt, sys_prompt, timeout=timeout)
            fallback_used = name != primary_name
            if fallback_used:
                print(Fore.YELLOW
                      + f"⚠️ Chat fallback used: '{primary_name}' failed "
                      + f"({errors[-1]}); this answer came from '{name}' "
                      + f"(model={get_selected_model(name)})."
                      + Style.RESET_ALL)
            _record_call_info(kind="chat", requested_service=primary_name,
                              service_used=name, model_used=get_selected_model(name),
                              fallback_used=fallback_used,
                              error="; ".join(errors) or None)
            return response
        except Exception as e:
            errors.append(f"{name}: {e}")
            print(Fore.RED + f"❌ {name} chat call failed after retries: {e}" + Style.RESET_ALL)

    _record_call_info(kind="chat", requested_service=primary_name, service_used=None,
                      model_used=None, fallback_used=False, error="; ".join(errors))
    print(Fore.RED
          + f"❌ llm_call failed on every allowed service ({'; '.join(errors)}); returning None."
          + Style.RESET_ALL)
    return None


# ---------------------------------------------------------------------------
# Main embedding call (timeout + cross-service fallback, mirrors llm_call)
# ---------------------------------------------------------------------------
def blabla_ask_embedding(texts):
    """Embed via Blablador using the session-selected embedding model."""
    return get_embedding(texts, service="BlaBla")


def Ollama_ask_embedding(texts):
    """Embed via DLR Ollama using the session-selected embedding model."""
    return get_embedding(texts, service="DLR Ollama")


def local_ask_embedding(texts):
    """Embed via the local HuggingFace model; same result shape as get_embedding()."""
    inputs = texts if isinstance(texts, list) else [texts]
    vecs = vectorize_strings_local(inputs)
    return {
        "model": f"local:{embedding_model_repo_id}",
        "service": "local",
        "embeddings": vecs.tolist(),
        "raw": None,
    }


def _normalize_embedding_service_code(service: str) -> str:
    """Map 'B'/'BlaBla'/'O'/'DLR Ollama'/'L'/'local' (any case) to 'b'/'o'/'l'."""
    s = str(service or "").strip().lower()
    if s in ("b", "blabla", "blablador", "blabla door"):
        return "b"
    if s in ("o", "ollama", "dlr ollama"):
        return "o"
    if s in ("l", "local"):
        return "l"
    return s


# Main embedding call method (built the same way as llm_call above)
def embedding_call(texts, service: str, model: str = None, timeout: int = 120,
                   allow_fallback: bool = False):
    """
    Get embeddings with native timeouts + bounded retries (inside
    get_embedding via _post_with_retries), mirroring llm_call():

      'B' -> Blablador /embeddings (never cross-service falls back).
      'O' -> DLR Ollama /embeddings; may fall back to Blablador, but ONLY
             when ``allow_fallback=True``.
      'L' -> local HuggingFace embedding model (no remote fallback).

    Long service names ("BlaBla", "DLR Ollama", "local") are accepted too.

    Cross-service fallback is OPT-IN here (default off), unlike chat: vectors
    from a different service/model live in a DIFFERENT embedding space and
    must never be mixed into the same FAISS index. Transient failures are
    instead retried on the SAME service; persistent failures return None and
    the caller (vector_db_updater.vectorize_strings max_retries loop) decides.

    Returns the get_embedding()-style dict, or None when every allowed
    attempt failed. If a fallback was allowed and used, result["service"] /
    result["model"] differ from the request - check them (also recorded in
    get_last_call_info()).
    """
    s = _normalize_embedding_service_code(service)

    # Optional per-call model override: record it as the session selection for
    # the targeted service (same pattern as llm_call + set_selected_model).
    if model:
        if s == 'b':
            set_selected_embedding_model("BlaBla", model)
        elif s == 'o':
            set_selected_embedding_model("DLR Ollama", model)

    if s == 'l':
        # Local embedding model call (no remote fallback, matching llm_call 'L')
        response = local_ask_embedding(texts)
        _record_call_info(kind="embedding", requested_service="local",
                          service_used="local", model_used=response.get("model"),
                          fallback_used=False, error=None)
        return response

    if s not in ('b', 'o'):
        error_msg = "Error: Invalid embedding service. Use 'B', 'O', or 'L'."
        print(error_msg)
        return None

    services = {'b': ("BlaBla", blabla_ask_embedding), 'o': ("DLR Ollama", Ollama_ask_embedding)}
    requested = services[s][0]
    attempt_order = [services[s]]
    # Policy: the embedding fallback service is always Blablador, never
    # DLR Ollama - so only 'o' has a cross-service fallback to offer.
    if allow_fallback and s == 'o':
        attempt_order.append(services['b'])

    errors = []
    response = None
    for name, ask in attempt_order:
        try:
            response = ask(texts)
            _record_call_info(kind="embedding", requested_service=requested,
                              service_used=name, model_used=response.get("model"),
                              fallback_used=name != requested,
                              error="; ".join(errors) or None)
            break
        except Exception as e:
            errors.append(f"{name}: {e}")
            print(Fore.RED + f"❌ {name} embedding call failed after retries: {e}" + Style.RESET_ALL)

    if response is None:
        _record_call_info(kind="embedding", requested_service=requested,
                          service_used=None, model_used=None, fallback_used=False,
                          error="; ".join(errors))
        return None

    if response.get("service") and response["service"] != requested:
        print(Fore.YELLOW
              + f"⚠️ Embedding fallback used: requested '{requested}' but vectors came from "
              + f"'{response['service']}' (model={response.get('model')}). These vectors are in a "
              + "different embedding space - rebuild/query FAISS indexes with a consistent backend."
              + Style.RESET_ALL)
    return response


# print(llm_call('hi','','o'))
if __name__ == "__main__":
    list_ollama_models()
    print("DLR Ollama embedding models:", list_embedding_models("DLR Ollama"))
    print("BlaBla embedding models:", list_embedding_models("BlaBla"))
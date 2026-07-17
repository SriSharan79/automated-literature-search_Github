import os
import json
import time
from pathlib import Path
import numpy as np
from colorama import Fore, Style, init

from alr.collection.search_phrase_generator_utils import rank_search_phrases
from alr.common.file_manager import Vec_DB_Manager
from alr.common.json_utils import get_key_from_file
from alr.common.excel_utils import get_values_from_sorted_numbers_and_save, sum_columns_ending_with_to_target

# Embedding model loading/calling now lives in llm_utils.py:
#   - vectorize_strings_local(): local GPU/CPU HuggingFace model
#     (Qwen/Qwen3-Embedding-8B by default), loaded lazily the same way
#     hf_pipeline_with_Lamma() / Local_Model_call() load the local chat model.
#   - get_embedding() / get_default_embedding_model(): remote API call against
#     DLR Ollama or Blablador's /embeddings endpoint.
from alr.common.llm_utils import (
    vectorize_strings_local,
    get_embedding,
    get_default_embedding_model,
    DEFAULT_EMBEDDING_MODEL,
    local_embedding_model_dir,
    embedding_model_repo_id,
    embedding_call,
    get_embedding_backend,
)

# Which embedding method to use by default:
#   'local' -> local GPU/CPU HuggingFace model (vectorize_strings_local in llm_utils.py)
#   'api'   -> remote DLR Ollama / Blablador /embeddings call (get_embedding in llm_utils.py)
#
# The default is deployment-aware so the packaged Windows .exe does not try to
# load the multi-GB local HuggingFace model from a Linux-only path that does not
# exist there. It can always be forced with the ALR_EMBEDDING_METHOD env var.


def _resolve_default_embedding_method() -> str:
    override = os.getenv("ALR_EMBEDDING_METHOD")
    if override:
        return override.strip().lower()
    # Use the local model only when its weights are actually present on disk
    # (true on the Linux GPU box, false in the packaged Windows build).
    try:
        if local_embedding_model_dir and os.path.isdir(local_embedding_model_dir):
            return "local"
    except Exception:
        pass
    return "api"


EMBEDDING_METHOD = _resolve_default_embedding_method()
EMBEDDING_SERVICE = os.getenv("ALR_EMBEDDING_SERVICE", "DLR Ollama")  # only used when method == "api"


def _current_backend() -> tuple:
    """
    Resolve the effective (method, service) at call time. A session selection
    made in the desktop UI (llm_utils.set_embedding_backend) wins over the
    deployment-aware module defaults above. Resolving at call time (instead of
    baking EMBEDDING_METHOD into def-time defaults) is what makes the UI
    dropdowns take effect for all downstream callers.
    """
    sel = get_embedding_backend()
    return (sel.get("method") or EMBEDDING_METHOD, sel.get("service") or EMBEDDING_SERVICE)


# Backend that actually produced the most recent vectors from
# vectorize_strings(). This can differ from the *requested* backend when
# embedding_call's timeout/fallback switched services, so index metadata is
# written from here (the truth) rather than from the request parameters.
LAST_EMBEDDING_BACKEND = {"method": None, "service": None, "model": None, "dim": None}


def _record_last_backend(method, service, model, dim) -> None:
    LAST_EMBEDDING_BACKEND.update(
        {"method": method, "service": service, "model": model, "dim": int(dim) if dim else None})


# ---------------------------------------------------------------------------
# Index <-> embedding-backend metadata
# ---------------------------------------------------------------------------
# A FAISS index is only meaningful when queried with the SAME embedding model
# that built it (different models -> different dimensionality / vector space).
# We persist a small sidecar file next to each index recording how it was built,
# and validate it at query time so a mismatched backend fails loudly instead of
# silently returning garbage similarity scores.

def _meta_path(index_file) -> str:
    return str(index_file) + ".meta.json"


def _recorded_model(method: str, service: str, model: str = None) -> str:
    """Best-effort identifier of the embedding model an index was built with."""
    if method == "local":
        return f"local:{embedding_model_repo_id}"
    if model:
        return model
    try:
        return get_default_embedding_model(service)
    except Exception:
        return DEFAULT_EMBEDDING_MODEL


def write_index_metadata(index_file, method, service, model, dim) -> None:
    # Prefer the backend that actually produced the most recent vectors: with
    # embedding_call's timeout/fallback the vectors may come from a different
    # service/model than the one requested, and the metadata must record what
    # really built the index.
    last = LAST_EMBEDDING_BACKEND
    if last.get("model") and last.get("dim") == int(dim):
        meta = {
            "method": last.get("method") or method,
            "service": last.get("service") if (last.get("method") or method) == "api" else None,
            "model": last["model"],
            "dim": int(dim),
        }
    else:
        meta = {
            "method": method,
            "service": service if method == "api" else None,
            "model": _recorded_model(method, service, model),
            "dim": int(dim),
        }
    try:
        with open(_meta_path(index_file), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
    except OSError as e:
        print(Fore.YELLOW + f"⚠️ Could not write index metadata for {index_file}: {e}" + Style.RESET_ALL)


def read_index_metadata(index_file):
    path = _meta_path(index_file)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
    return None


def _validate_query_against_index(index_file, method, service, model, query_dim) -> None:
    """Warn/raise when the query embedding backend differs from what built the index."""
    meta = read_index_metadata(index_file)
    if not meta:
        return  # legacy index built before metadata existed - nothing to check

    if meta.get("dim") is not None and int(meta["dim"]) != int(query_dim):
        raise ValueError(
            f"Embedding dimension mismatch for '{index_file}': index was built with "
            f"dim={meta['dim']} but the query produced dim={query_dim}. Rebuild the index "
            f"with the same embedding method/model, or query with the matching backend."
        )

    # The query vector was just produced by vectorize_strings(), so the
    # last-used record identifies the real query backend (including fallbacks).
    last = LAST_EMBEDDING_BACKEND
    if last.get("model") and last.get("dim") == int(query_dim):
        query_model = last["model"]
    else:
        query_model = _recorded_model(method, service, model)
    if meta.get("model") and query_model and meta["model"] != query_model:
        print(
            Fore.RED
            + f"⚠️ Embedding backend mismatch for '{index_file}': index built with "
            + f"model='{meta['model']}' (method='{meta.get('method')}') but querying with "
            + f"model='{query_model}' (method='{method}'). Similarity scores may be invalid."
            + Style.RESET_ALL
        )


def vectorize_strings(
    input_strings: list[str],
    method: str = None,
    service: str = None,
    model: str = None,
    max_length: int = 512,
    batch_size: int = 32,
    max_retries: int = 3,
    retry_wait: float = 10.0,
) -> np.ndarray:
    """
    Get embedding vectors for a list of strings.

    method/service default to None and are resolved AT CALL TIME against the
    session selection made in the UI (llm_utils.set_embedding_backend), falling
    back to the deployment-aware module defaults - so the embedding-engine
    dropdowns in main_window take effect everywhere without touching callers.

    method='local': local GPU/CPU HuggingFace model via
        llm_utils.vectorize_strings_local() - same behaviour/weights as before.
        If loading/inference fails (e.g. weights missing in the packaged
        Windows build), it falls back to the API path below with a warning.
    method='api': remote embedding call via llm_utils.embedding_call(), which
        wraps get_embedding() with a timeout and cross-service fallback the
        same way llm_call() does for chat (B <-> O). Results are batched and
        L2-normalised client-side so cosine similarity == inner product
        (matches the FAISS IndexFlatIP index built below).
    """
    default_method, default_service = _current_backend()
    method = (method or default_method).strip().lower()
    service = service or default_service

    if not input_strings:
        return np.zeros((0, 0), dtype=np.float32)

    if method == "local":
        try:
            # Local inputs are pooled 10 strings per forward pass inside
            # vectorize_strings_local (its batch_size default) to avoid GPU OOM.
            vectors = vectorize_strings_local(input_strings, max_length=max_length)
            _record_last_backend("local", "local", f"local:{embedding_model_repo_id}", vectors.shape[1])
            return vectors
        except Exception as e:
            # Policy: the embedding fallback service is always Blablador,
            # never DLR Ollama - regardless of the session-selected service.
            service = "BlaBla"
            print(Fore.YELLOW
                  + f"⚠️ Local embedding model failed ({e}); falling back to API service 'BlaBla'."
                  + Style.RESET_ALL)
            method = "api"

    if method == "api":
        service_code = "B" if service == "BlaBla" else "O"
        all_embeddings = []
        actual_service, actual_model = service, model
        # (service, model) of the FIRST successful batch. Every later batch
        # must come from the SAME backend: vectors from a cross-service
        # fallback live in a different embedding space and must never be
        # mixed into the same array / FAISS index.
        pinned = None
        for i in range(0, len(input_strings), batch_size):
            batch = input_strings[i:i + batch_size]
            batch_label = f"batch {i}-{i + len(batch)} of {len(input_strings)} strings"
            result = None
            for attempt in range(1, max_retries + 1):
                # Timeout + cross-service fallback (mirrors llm_call); returns
                # None only when the requested service AND its fallback failed.
                candidate = embedding_call(batch, service_code, model=model)
                if candidate and candidate.get("embeddings"):
                    got = (candidate.get("service", service), candidate.get("model", model))
                    if pinned is not None and got != pinned:
                        # A fallback answered with a different backend than the
                        # batches already collected -> reject and retry rather
                        # than silently mixing embedding spaces.
                        print(Fore.YELLOW
                              + f"\u26a0\ufe0f {batch_label}: got vectors from {got} but this run is "
                              + f"pinned to {pinned}; discarding batch and retrying "
                              + f"(attempt {attempt}/{max_retries})."
                              + Style.RESET_ALL)
                    else:
                        result = candidate
                        break
                else:
                    print(Fore.YELLOW
                          + f"\u26a0\ufe0f Embedding call failed for {batch_label} "
                          + f"(attempt {attempt}/{max_retries}); see errors above."
                          + Style.RESET_ALL)
                if attempt < max_retries:
                    wait = retry_wait * attempt  # 10s, 20s, ... linear backoff
                    print(f"   \u23f3 Waiting {wait:.0f}s before retrying...")
                    time.sleep(wait)
            if result is None:
                raise RuntimeError(
                    f"Embedding call failed for service '{service}' and its fallback "
                    f"({batch_label}) after {max_retries} attempts. The FAISS index "
                    f"was NOT modified; re-run the sync once the service is reachable "
                    f"and it will resume from the current index count.")
            actual_service = result.get("service", service)
            actual_model = result.get("model", model)
            if pinned is None:
                pinned = (actual_service, actual_model)
            all_embeddings.extend(result["embeddings"])

        vectors = np.array(all_embeddings, dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        vectors = vectors / norms
        _record_last_backend("api", actual_service, actual_model, vectors.shape[1])
        return vectors

    raise ValueError(f"Unknown embedding method '{method}'. Expected 'local' or 'api'.")


def create_faiss_index_cosine(vectors: np.ndarray) -> "faiss.Index":
    import faiss

    # With normalised vectors: inner product == cosine similarity
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)
    return index


def save_index_file(index, file_path, method: str = None, service: str = None, model: str = None):
    import faiss

    default_method, default_service = _current_backend()
    method = method or default_method
    service = service or default_service

    # Validate index
    if index is None or not hasattr(index, "ntotal"):
        raise TypeError(f"index is not a faiss Index. Got type={type(index)}")

    # Normalise file_path to a real string
    if file_path is None:
        raise ValueError("file_path is None")

    file_path = str(Path(file_path))  # handles str/Path safely
    Path(file_path).parent.mkdir(parents=True, exist_ok=True)

    faiss.write_index(index, file_path)
    # Record how this index was built so queries can be validated against it.
    write_index_metadata(file_path, method, service, model, index.d)
    print(f"FAISS index saved to: {file_path} (vectors stored: {index.ntotal})")


def load_index_file(file_path):
    import faiss

    if file_path is None:
        return None

    file_path = str(Path(file_path))  # force Path/other path-like -> str

    if not os.path.exists(file_path):
        return None

    return faiss.read_index(file_path)


def add_new_strings_to_index(
    index_file: str,
    new_strings: list[str],
    method: str = None,
    service: str = None,
    model: str = None,
    max_length: int = 512,
):
    import faiss

    default_method, default_service = _current_backend()
    method = method or default_method
    service = service or default_service

    # Normalise file_path to a real string
    if index_file is None:
        raise ValueError("index_file is None")

    index_file = str(Path(index_file))  # handles str/Path safely
    Path(index_file).parent.mkdir(parents=True, exist_ok=True)

    # Vectorize the new strings first, since a freshly-created index needs to
    # know the embedding dimensionality up front.
    new_vecs = vectorize_strings(new_strings, method=method, service=service, model=model, max_length=max_length)

    # Ensure the index file path is correct and exists
    try:
        if isinstance(index_file, str):
            index = faiss.read_index(index_file)
        else:
            raise ValueError("The index file path must be a string.")

    except RuntimeError:
        # If index file doesn't exist or is corrupted, create a new index
        print(f"Index file not found or invalid. Creating a new index at {index_file}.")
        d = new_vecs.shape[1]  # Dimensionality of the embedding vectors actually returned
        index = faiss.IndexFlatIP(d)  # Inner product on normalised vectors == cosine similarity

    # Add the vectors to the index
    index.add(new_vecs)

    # Save the updated index
    faiss.write_index(index, index_file)
    # Record/refresh the embedding-backend metadata for this index.
    write_index_metadata(index_file, method, service, model, index.d)
    print(f"Added {len(new_strings)} vectors and saved updated index: {index_file}")


def search_similar(
    index_file: str,
    query: str,
    top_k: int = 5,
    method: str = None,
    service: str = None,
    model: str = None,
    max_length: int = 512,
):
    default_method, default_service = _current_backend()
    method = method or default_method
    service = service or default_service

    index = load_index_file(index_file)
    qvec = vectorize_strings([query], method=method, service=service, model=model, max_length=max_length)
    # Fail loudly if the query backend does not match the one that built the index.
    _validate_query_against_index(index_file, method, service, model, qvec.shape[1])
    scores, ids = index.search(qvec, top_k)  # scores are cosine (since IndexFlatIP + normalised)
    return scores[0].tolist(), ids[0].tolist()


# Example usage
if __name__ == "__main__":
    storage_path = '/remotedata/U/DLR+kata_du/ALR DATA/SLR_Process_Main/SLR_Process_results'
    VDB = Vec_DB_Manager(storage_path)
    strings = get_key_from_file(VDB.Research_Areas_DB_json, "Content")
    # print(RAs)
    embeds = vectorize_strings(strings)  # defaults to method='local' (GPU HF model)
    index_in = create_faiss_index_cosine(embeds)
    save_index_file(index_in, VDB.Research_Areas_DB_bin)
    print(f'strings: {len(strings)}')
    index = load_index_file(VDB.Research_Areas_DB_bin)
    print("index type:", type(index))
    print("index ntotal:", getattr(index, "ntotal", None))
    print("bin path type:", type(VDB.Research_Areas_DB_bin), "value:", VDB.Research_Areas_DB_bin)
    scores, ids = search_similar(VDB.Research_Areas_DB_bin, "search phrase", top_k=3)
    print("Top matches:")
    for s, i in zip(scores, ids):
        print(f"idx={i}  cosine={s:.4f}  text={strings[i] if i < len(strings) else '(newly added item)'}")
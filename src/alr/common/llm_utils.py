import sys
sys.path.extend([
    r'src',
    r'src/COLLECTION',
    r'Working_Code',
    r'src/DATA_ANALYSIS',
    r'src/COMMON',
    r'src/Command_Line_UI'
])
from COMMON.General_Utils import caluculate_time_taken, print_with_separator
from COMMON.LLM_Config import BLABLADOR_BASE_URL, PREFERRED_BLABLADOR_MODELS, check_api_key,local_model_dir,model_repo_id
from COMMON.System_prompts import General_Sys_Prompt
from COMMON.File_Manager import ALR_main_folder

from collections import deque
from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
import torch
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
from typing import List,Dict,Any
# LangChain Imports
from langchain_core.prompts import PromptTemplate
from transformers import Mistral3ForConditionalGeneration, FineGrainedFP8Config, AutoTokenizer, pipeline
from colorama import Fore, init
init(autoreset=True)
import time
from typing import List,Dict,Any


REQUEST_TIMES = deque(maxlen=10)


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



# def count_tokens(messages, response_text, model):
#     """Calculates tokens for both the input message list and the output string."""
#     try:
#         encoding = tiktoken.encoding_for_model(model)
#     except KeyError:
#         encoding = tiktoken.get_encoding("cl100k_base")

#     # Calculate Input Tokens (Messages)
#     # Each message has overhead: <|start|>role<|per_msg_extra|>content<|end|>
#     tokens_per_message = 3 
#     tokens_per_name = 1
#     input_tokens = 0
#     for message in messages:
#         input_tokens += tokens_per_message
#         for key, value in message.items():
#             input_tokens += len(encoding.encode(value))
#             if key == "name":
#                 input_tokens += tokens_per_name
#     input_tokens += 3  # every reply is primed with <|start|>assistant<|message|>

#     # Calculate Output Tokens
#     output_tokens = len(encoding.encode(response_text))
    
#     return input_tokens, output_tokens


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
    model: str = "gpt-oss:20b",
) -> str:

    # print_with_separator("DebugLog",'/')

    start_time = time.time()

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

    url = 'http://ollama.nimbus.dlr.de/api/chat/completions'
    resp = requests.post(url, headers=headers, json=payload)

    # Raise an informative error if something went wrong
    resp.raise_for_status()

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
    blablador_key: str = None
) -> str:
    """Query Blablador LLM with dynamic model selection."""
    
    time.sleep(3)
        
    # --- RATE LIMITER LOGIC ---
    current_time = time.time()
    
    # If we have already hit our 20 request capacity, check the oldest request
    if len(REQUEST_TIMES) == 10:
        oldest_request_time = REQUEST_TIMES[0]
        elapsed_since_oldest = current_time - oldest_request_time
        
        # If the oldest request happened less than 60 seconds ago, we must wait
        if elapsed_since_oldest < 60:
            sleep_time = 60 - elapsed_since_oldest
            print(Fore.YELLOW + f"⚠️ Rate limit approaching. Sleeping for {sleep_time:.2f} seconds..." + Style.RESET_ALL)
            time.sleep(sleep_time)
            
    # Record the current timestamp for this request execution
    REQUEST_TIMES.append(time.time())
    # --------------------------

    start_time = time.time()
    # print_with_separator("DebugLog",'/')
    
    # Dynamically select best model
    model = "01 - GPT-OSS-120b - an open model released by OpenAI in August 2025"
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

    resp = requests.post(url, headers=headers, json=payload)
    resp.raise_for_status()

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


# hf_pipeline_default=hf_pipeline_with_Lamma()  

def Local_Model_call(prompt: str, sys_prompt: str) :
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


import time
import threading

# Timeout wrapper function
def timeout_function(func, args=(), timeout=120, fallback=None):
    """
    Executes a function with a timeout. If it exceeds the timeout, it will attempt a fallback function.
    """
    result = None
    error = None
    
    def worker():
        nonlocal result, error
        try:
            result = func(*args)  # Call the function with its arguments
        except Exception as e:
            error = e  # If an error occurs, capture it
            traceback.print_exc()              
    
    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout)
    
    if thread.is_alive():
        # Timeout, so return the fallback if it's provided
        print("Timeout occurred. Switching service...")
        if fallback:
            return fallback(*args)
        else:
            return None  # If no fallback, return None or handle accordingly
    elif error:
        # If there's an error, call fallback
        print(f"Error occurred: {error}. Switching service...")
        if fallback:
            return fallback(*args)
        else:
            return None
    else:
        return result

# Main LLM call method
def llm_call(prompt: str, system_prompt: str, service: str):
    # Use provided prompt if valid; otherwise fallback
    if system_prompt:
        sys_prompt = system_prompt
    else:
        sys_prompt = General_Sys_Prompt

    # print_with_separator("DebugLog",'/')
    
    # print(f"System Prompt: {sys_prompt}")
    # print(f"User Prompt: {prompt}")

    # Normalize service input
    s = service.lower()

    # Service calling logic with timeout and fallback
    if s == 'b':
        # If service B (blabla) fails or times out, fallback to Ollama (o)
        response = timeout_function(blabla_ask_llm, (prompt, sys_prompt), timeout=60, fallback=lambda prompt, sys_prompt: timeout_function(Ollama_ask_llm, (prompt, sys_prompt), timeout=60, fallback=None))
    elif s == 'o':
        # If service O (Ollama) fails or times out, fallback to Blabla (b)
        response = timeout_function(Ollama_ask_llm, (prompt, sys_prompt), timeout=60, fallback=lambda prompt, sys_prompt: timeout_function(blabla_ask_llm, (prompt, sys_prompt), timeout=60, fallback=None))
    elif s == 'l':
        # Local model call
        # hf_pipeline=hf_pipeline_with_Lamma()  
        response = Local_Model_call(prompt, sys_prompt)
    else:
        error_msg = "Error: Invalid service. Use 'B', 'O', or 'L'."
        print(error_msg)
        return error_msg  # Return the error so the app doesn't crash downstream

    return response

# print(llm_call('hi','','o'))
if __name__ == "__main__":
    cache_blablador_models()
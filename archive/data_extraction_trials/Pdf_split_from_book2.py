
from COMMON.File_Manager import DataAnalyzeManager, Vec_DB_Manager
from LLM_Config import BLABLADOR_BASE_URL, check_api_key
from COMMON.Excel_Utils import*
from PyPDF2 import PdfReader
from docling.document_converter import DocumentConverter
import pdfplumber
import fitz  # PyMuPDF

from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
import torch
from colorama import Fore, Style, init
import pandas as pd
from datetime import datetime
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
import pandas as pd
import requests
import json
import os
import unicodedata
from collections import Counter
import xml.etree.ElementTree as ET
from COMMON.Excel_Utils import extract_column


system_prompt = """
### Role
You are a high-precision Data Extraction Assistant. You will receive raw text from a PDF Table of Contents. Your goal is to identify each individual paper or article listed.

### Extraction Rules
For each entry found in the text, extract:
1. **category**: The name of the Workshop or Session the paper belongs to (e.g., "15th International Workshop on...").
2. **publication_name**: The full title of the specific paper or article.
3. **authors**: All listed authors as a comma-separated string.
4. **page_num**: The starting page number for that specific paper.

### Constraints
- **Format**: Return a JSON object containing a list named "entries".
- **Hierarchy**: Apply the most recent "Workshop" or "Session" title as the 'category' for all papers following it until a new workshop title appears.
- **Cleaning**: Ignore the dots (........) used for visual spacing in the ToC.
- **Return ONLY valid JSON.**

### Expected Output Schema
{
    "category": "string",
    "publication_name": "string",
    "authors": "string",
    "page_num": "string"
}
"""

def blabla_ask_llm_test(
    prompt: str,
    sys_prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 8192,
    blablador_key: str = None
) -> str:
    """Query Blablador LLM with dynamic model selection."""


    start_time = time.time()
    # print_with_separator("DebugLog",'/')
    
    # Dynamically select best model
    # model = "1 - GPT-OSS-120b - an open model released by OpenAI in August 2025"
    model = "15 - Apertus-8B-Instruct-2509 - A new swiss model from September 2025"
    print(f"🤖 Using model: {model}")

    
    print(Fore.GREEN + f" Prompt : \n {prompt}"+ Style.RESET_ALL)
    
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
    # content=None 
    try:
        content = result["choices"][0]["message"]["content"]

        # print(Fore.GREEN + f" Blablador sucess. Full response: {result}"+ Style.RESET_ALL)
        
        # ADD THIS NULL CHECK
        if content is None:
            raise ValueError("Empty content received from Blablador")

        # Clean the content (remove markdown backticks if the LLM adds them)
        content = content.replace("```json", "").replace("```", "").strip()
            
        print(Fore.CYAN + "\n--- RAW LLM RESPONSE START ---" + Style.RESET_ALL)
        print(Fore.CYAN + content + Style.RESET_ALL)
        print(Fore.CYAN + "--- RAW LLM RESPONSE END ---\n" + Style.RESET_ALL)
        return content # This returns a STRING
        
    except (KeyError, IndexError, ValueError) as exc:
        # Print full response for debugging
        print(f"❌ Blablador failed. Full response: {result}")
        content=f"❌ Blablador failed. Full response: {result}\n Unexpected response format from Blablador: {exc}"

        raise ValueError(f"Unexpected response format from Blablador: {exc}") from exc

def process_pdf_by_page(file_path, start_page, end_page, excel_path):
    all_entries = []
    current_category = "Unknown" # Keeps track of the workshop across pages
    
    try:
        doc = fitz.open(file_path)
        num_pages = len(doc)
        actual_end = min(end_page, num_pages)

        for i in range(start_page, actual_end):
                
            # Small sleep to be kind to the API rate limits
            print(f"--- Processing Page {i+1} ---")
            page = doc[i]
            raw_text = page.get_text("text")
            clean_page_text = _clean_text(raw_text)

            if clean_page_text:
                # We pass the last known category to the LLM so it has context
                page_data = get_metadata_from_llm(clean_page_text, current_category)
                
                if page_data:
                    all_entries.extend(page_data)
                    # Update current_category from the last entry found to carry to next page
                    current_category = page_data[-1].get("category", current_category)
            
                    # Save all collected entries at once
            if all_entries:
                save_to_excel(all_entries, excel_path)
                print(f"Total entries saved: {len(all_entries)}")
            else:
                print("No data extracted.")

        doc.close()
        
        # Save all collected entries at once
        if all_entries:
            save_to_excel(all_entries, excel_path)
            print(f"Total entries saved: {len(all_entries)}")
        else:
            print("No data extracted.")

    except Exception as e:
        print(f"Error: {e}")
import re
import json

def robust_json_load(response_string):
    if not response_string:
        return []

    # 1. Strip Markdown and whitespace
    cleaned = re.sub(r'```json\s*| ```', '', response_string).strip()
    
    # 2. Try standard load
    try:
        data = json.loads(cleaned)
        return data.get("entries", []) if isinstance(data, dict) else data
    except json.JSONDecodeError:
        pass 

    # 3. Fix Single Quotes (Common 'Expecting property name' cause)
    # This replaces 'key': 'value' with "key": "value"
    try:
        # Dangerous but often effective for LLM hallucinations
        fixed_quotes = re.sub(r"\'(\w+)\'\s*:", r'"\1":', cleaned) # fix keys
        fixed_quotes = re.sub(r":\s*\'(.*?)\'", r': "\1"', fixed_quotes) # fix values
        data = json.loads(fixed_quotes)
        return data.get("entries", []) if isinstance(data, dict) else data
    except:
        pass

    # 4. FINAL FALLBACK: Regex Pattern Matching
    # If the JSON is "broken" but the text is there, this will pull it out.
    print("JSON structure failed. Falling back to Regex extraction...")
    entries = []
    
    # This pattern looks for "key": "value" pairs regardless of overall JSON validity
    # It extracts the four fields you need
    pattern = re.compile(
        r'"category":\s*"(.*?)".*?'
        r'"name":\s*"(.*?)".*?'
        r'"authors":\s*"(.*?)".*?'
        r'"page_number":\s*"(.*?)"', 
        re.DOTALL | re.IGNORECASE
    )
    
    matches = pattern.findall(cleaned)
    for m in matches:
        entries.append({
            "category": m[0].strip(),
            "name": m[1].strip(),
            "authors": m[2].strip(),
            "page_number": m[3].strip()
        })
    
    return entries


def get_metadata_from_llm(extracted_text, last_category):
    # Added context about the previous category to maintain continuity
    
    time.sleep(1.5)
    prompt = f"\nRaw text from current page:\n{extracted_text}"
    data=[]
    
    print(Fore.MAGENTA + f"LLM PROMPT (Page Segment): {prompt}" + Style.RESET_ALL)
    response_string = blabla_ask_llm_test(prompt, system_prompt)
    # response_string = llm_call(prompt, system_prompt,'l')
    data = repair_and_load_json(response_string)
    # data = robust_json_load(response_string)

            # # log response
    print(Fore.BLUE + "LLM RESPONSE:" + Style.RESET_ALL)
    print(Fore.BLUE + str(data) + Style.RESET_ALL)
    
    if isinstance(data, dict):
        return data.get("entries", [])
    elif isinstance(data, list):
        return data
    return data

# --- Keep your save_to_excel and _clean_text functions as they were ---


def _clean_text( text):
    if not text: return ""
    text = unicodedata.normalize('NFKD', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

import re
import json

def repair_and_load_json(response_string):
    """
    Attempts to repair common LLM JSON errors like unescaped quotes,
    trailing commas, or markdown blocks.
    """
    if not response_string:
        return None

    # 1. Strip Markdown code blocks if present (```json ... ```)
    cleaned = re.sub(r'```json\s*|```', '', response_string).strip()

    try:
        # Try standard load first
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # 2. Fix unescaped double quotes inside strings
        # This looks for quotes that aren't preceded by a backslash and 
        # aren't part of the JSON structure (e.g., :"Value "with" quotes")
        # Note: This is a heuristic and might need adjustment based on specific errors
        repaired = re.sub(r'(?<!\\)"', r'\"', cleaned)
        # Restore the structural quotes
        repaired = repaired.replace('\"category\"', '"category"') \
                           .replace('\"name\"', '"name"') \
                           .replace('\"authors\"', '"authors"') \
                           .replace('\"page_number\"', '"page_number"') \
                           .replace('\"entries\"', '"entries"') \
                           .replace(': \"', ': "') \
                           .replace('\",', '",') \
                           .replace('\"}', '"}') \
                           .replace('{\"', '{"') \
                           .replace('\"[', '"[') \
                           .replace(']\"', ']"')

        try:
            return json.loads(repaired)
        except json.JSONDecodeError as e:
            print(f"Hard failure on JSON repair: {e}")
            # 3. Last resort: Partial extraction using regex if structure is totally broken
            return _last_resort_regex_extraction(cleaned)

def _last_resort_regex_extraction(text):
    """
    Manually finds patterns if the JSON structure is unsalvageable.
    """
    entries = []
    # Regex to find objects like {"category": "...", "name": "...", ...}
    pattern = re.compile(r'\{"category":\s*"(.*?)",\s*"name":\s*"(.*?)",\s*"authors":\s*"(.*?)",\s*"page_number":\s*"(.*?)"\}', re.DOTALL)
    matches = pattern.findall(text)
    
    for m in matches:
        entries.append({
            "category": m[0].strip(),
            "name": m[1].strip(),
            "authors": m[2].strip(),
            "page_number": m[3].strip()
        })
    return entries


def save_to_excel(json_data, filename):
    # FIX: Check if json_data is already a list or a dict containing a list
    if isinstance(json_data, list):
        entries = json_data
    else:
        entries = json_data.get("entries", [])
    
    # Create DataFrame
    df = pd.DataFrame(entries)
    
    # Define the columns you want
    column_mapping = {
        "category": "category",
        "publication_name": "name", 
        "name": "name",
        "authors": "authors",
        "page_num": "page_number",
        "page_number": "page_number"
    }
    
    df = df.rename(columns=column_mapping)
    
    # Ensure exact column order and existence
    final_columns = ["category", "name", "authors", "page_number"]
    for col in final_columns:
        if col not in df.columns:
            df[col] = ""
            
    df = df[final_columns]
    
    # Save to Excel
    df.to_excel(filename, index=False)
    return filename


import os
import re
import shutil
import tempfile
import fitz
import pandas as pd
def split_pdf_by_excel(excel_path: str, pdf_path: str, output_root: str):

    def sanitize(name: str) -> str:
        return re.sub(r'[<>:"/\\|?*\n\r]', '', name).strip()

    def safe_makedirs(path: str):
        try:
            os.makedirs(path, exist_ok=True)
        except FileExistsError:
            os.remove(path)
            os.makedirs(path, exist_ok=True)

    # ── Copy source PDF locally ───────────────────────────────────────────────
    local_tmp = tempfile.mkdtemp()
    local_pdf = os.path.join(local_tmp, "source.pdf")
    print(f"⏳ Copying PDF to local temp ...")
    shutil.copy2(pdf_path, local_pdf)
    print(f"✓ Copy done — {local_pdf}")

    # ── Load Excel ────────────────────────────────────────────────────────────
    df = pd.read_excel(excel_path)
    print(f"✓ Loaded {len(df)} papers")

    # ── Open PDF locally ──────────────────────────────────────────────────────
    doc = fitz.open(local_pdf)
    total_pages = len(doc)
    print(f"✓ PDF opened — {total_pages} pages\n")

    # ── Create output root on network ─────────────────────────────────────────
    safe_makedirs(output_root)

    # ── Split papers ──────────────────────────────────────────────────────────
    for idx, row in df.iterrows():
        category   = sanitize(str(row["category"]))
        paper_name = sanitize(str(row["name"]))
        start_page = int(row["page_number"])
        end_page   = int(df.iloc[idx + 1]["page_number"]) - 1 if idx < len(df) - 1 else total_pages

        # Extract pages into new doc
        new_doc = fitz.open()
        for page_num in range(start_page - 1, end_page):
            if page_num < total_pages:
                new_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)

        # ── Save locally first, then copy to network ──────────────────────────
        local_out = os.path.join(local_tmp, f"{paper_name}.pdf")
        new_doc.save(local_out)          # fast — local disk
        new_doc.close()

        # Create network category folder and copy file over
        network_folder = os.path.join(output_root, category)
        safe_makedirs(network_folder)
        network_out = os.path.join(network_folder, f"{paper_name}.pdf")
        shutil.copy2(local_out, network_out)   # OS-level block copy to network
        os.remove(local_out)                   # clean up local temp immediately

        print(f"  ✓ [{idx+1}/{len(df)}] {category}/{paper_name}.pdf  (pages {start_page}–{end_page})")

    doc.close()
    shutil.rmtree(local_tmp)
    print(f"\n✓ Temp files cleaned up")
    print(f"✓ Done — all papers saved to: {output_root}")

if __name__ == "__main__":
    excel_path  = '/remotedata/U/DLR+kata_du/ALR DATA/Only_MBSA/Only_MBSA_pdfs/Conference_Book/Model-Based Safety and Assessment (IMBSA 2025); 2025.xlsx'
    pdf_path    = '/remotedata/U/DLR+kata_du/ALR DATA/Only_MBSA/Only_MBSA_pdfs/Conference_Book/Model-Based Safety and Assessment (IMBSA 2025); 2025.pdf'
    output_root = '/remotedata/U/DLR+kata_du/ALR DATA/Only_MBSA/Conference_Book/Papers_split'


    # New orchestration function
    # process_pdf_by_page(pdf_path, 10, 15, excel_path)
    split_pdf_by_excel(excel_path, pdf_path, output_root)


import sys
from time import time

from DATA_ANALYSIS.Data_analysis_system_prompts import PRE_CHECK_SYSTEM_PROMPT, SYSTEM_PROMPT_Reference_extraction
from DATA_ANALYSIS.Data_sorting_utils import extract_chunk_heading
from COMMON.LLM_Utils import llm_call
from COMMON.File_Manager import DataAnalyzeManager
from DATA_ANALYSIS.Refrences_log_utils import log_Ref_data_extracted, save_references_to_json
sys.path.extend([
    r'src',
    r'src/COLLECTION',
    r'Working_Code',
    r'src/DATA_ANALYSIS',
    r'src/COMMON',
    r'src/Command_Line_UI'
])

import re
from docling.document_converter import DocumentConverter
from docling.chunking import HybridChunker
from colorama import Fore,Style

from pathlib import Path
import os
import json

converter = DocumentConverter()
chunker = HybridChunker()

def has_dash_in_last_n(text: str, n: int = 5) -> bool:
    """Check if '-' exists in last N chars (after stripping trailing whitespace)."""
    cleaned = text.rstrip()
    return len(cleaned) >= n and '-' in cleaned[-n:]

def validate_input_data(chunks_list, output_path):
    """Checks if doc or chunks are missing and initializes empty file if needed."""
    
    if len(chunks_list) == 0:
        print(Fore.RED + "!!! DIAGNOSTIC: 'chunks' is EMPTY." + Style.RESET_ALL)
    else:
        print(Fore.GREEN + f"--- DIAGNOSTIC: 'chunks' VALID ({len(chunks_list)} blocks). ---" + Style.RESET_ALL)

    if len(chunks_list) == 0:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump([], f)
        return False
    return True

def is_reference_heading(heading):
    """Checks if a heading matches reference keywords."""
    Ref_Keywords = ["references", "bibliography", "reference list", 
                    "literature cited", "works cited", "sources", 
                    "literatur", "références"]
    return any(key in heading.lower() for key in Ref_Keywords)

def get_smart_split_index(text, preferred_delim="\n-", fallback_delim="\n"):
    """
    Identifies the best point to split long text.
    1. Tries middle occurrence of '\n-'.
    2. Tries a '\n' near the midpoint.
    3. Forces a split at the mathematical midpoint.

    SAFETY: Never returns 0, -1, or len(text).
    """
    text_len = len(text)
    if text_len <= 1:
        # Nothing meaningful to split; caller shouldn't split anyway.
        return 1 if text_len == 1 else 0

    def _clamp_valid(idx: int) -> int:
        """Clamp to a valid split index strictly inside (0, len(text))."""
        if idx <= 0:
            return 1
        if idx >= text_len:
            return text_len - 1
        return idx

    count = text.count(preferred_delim)

    # --- CASE 1: Preferred delimiter frequent enough ---
    if count >= 2:
        middle_match = (count // 2) + (count % 2)
        split_idx = -1
        found_count = 0
        current_search_pos = 0

        while found_count < middle_match:
            current_search_pos = text.find(preferred_delim, current_search_pos)
            if current_search_pos == -1:
                break
            split_idx = current_search_pos
            current_search_pos += len(preferred_delim)
            found_count += 1

        # SAFETY: If delimiter search failed or landed at a boundary, fall through to safer options.
        if 0 < split_idx < text_len:
            return split_idx

    # --- CASE 2: preferred_delim is rare (0 or 1), try fallback '\n' ---
    midpoint = text_len // 2
    # Look for '\n' in a 200-char window around the midpoint
    search_start = max(0, midpoint - 100)
    search_end = min(text_len, midpoint + 100)
    split_idx = text.find(fallback_delim, search_start, search_end)

    if split_idx != -1:
        # print(Fore.CYAN + f"DEBUG: '{preferred_delim}' not viable. Splitting at fallback '{fallback_delim}'." + Style.RESET_ALL)
        return _clamp_valid(split_idx)

    # --- CASE 3: No delimiters found at all, force a midpoint split ---
    # print(Fore.RED + "DEBUG: No delimiters found (\n- or \n). Forcing hard split at midpoint." + Style.RESET_ALL)
    return _clamp_valid(midpoint)


def perform_core_llm_extraction(raw_text, output_path,llm_service):
    """Base Case: Standard LLM Logic for text <= 800 chars."""
    # print(Fore.YELLOW + f"\n --- Sending to Pre-Check LLM ({len(raw_text)} chars) ---\n {raw_text}" + Style.RESET_ALL)

    pre_check = llm_call(f"Raw text:\n {raw_text}", PRE_CHECK_SYSTEM_PROMPT,llm_service)

    if "None" in str(pre_check):
        # print(Fore.RED + f"DEBUG: LLM Pre-check REJECTED. Response: {pre_check}" + Style.RESET_ALL)
        return False

    # print(Fore.GREEN + "DEBUG: LLM Pre-check PASSED. Proceeding to extraction..." + Style.RESET_ALL)
    res_data = llm_call(f"Reference data:\n {pre_check}", SYSTEM_PROMPT_Reference_extraction,llm_service)

    if res_data and "None" not in str(res_data):
        save_references_to_json(res_data, output_path)
        print(Fore.GREEN + f"SUCCESS: References saved to {output_path}" + Style.RESET_ALL)
        return True

    print(Fore.RED + "DEBUG: Final Extraction LLM returned None or Empty." + Style.RESET_ALL)
    return False


def handle_llm_extraction(raw_text, output_path,llm_service):
    """
    Recursive Orchestrator. Ensures no text block > 800 chars reaches the LLM.
    """
    if len(raw_text) > 800:
        split_idx = get_smart_split_index(raw_text)

        # SAFETY: absolutely guarantee a valid, non-boundary split
        if split_idx <= 0 or split_idx >= len(raw_text):
            # print(Fore.RED + f"DEBUG: Invalid split_idx={split_idx}. Forcing safe midpoint split." + Style.RESET_ALL)
            split_idx = len(raw_text) // 2
            if split_idx <= 0:
                split_idx = 1
            elif split_idx >= len(raw_text):
                split_idx = len(raw_text) - 1

        part1 = raw_text[:split_idx]
        part2 = raw_text[split_idx:]

        # Extra safety (should never trigger now, but keeps recursion safe)
        if not part1 or not part2:
            # print(Fore.RED + f"DEBUG: Empty split detected (part1={len(part1)}, part2={len(part2)}). Forcing safe midpoint split." + Style.RESET_ALL)
            split_idx = len(raw_text) // 2
            if split_idx <= 0:
                split_idx = 1
            elif split_idx >= len(raw_text):
                split_idx = len(raw_text) - 1
            part1 = raw_text[:split_idx]
            part2 = raw_text[split_idx:]

        # print(Fore.BLUE + f"DEBUG: Split into Part 1 ({len(part1)} chars) and Part 2 ({len(part2)} chars)." + Style.RESET_ALL)

        # Recursively process both parts
        res1 = handle_llm_extraction(part1, output_path,llm_service)
        res2 = handle_llm_extraction(part2, output_path,llm_service)
        return res1 or res2

    # BASE CASE: text is small enough for the LLM
    return perform_core_llm_extraction(raw_text, output_path,llm_service)


def process_references_from_chunks(chunks, output_path,llm_service):
    """Main pipeline for reference extraction."""
    # Convert iterator to list immediately
    chunks_list = list(chunks) if not isinstance(chunks, list) else chunks
    
    if not validate_input_data(chunks_list, output_path):
        return False

    trailing_storage = ""
    references_extracted = False
    print(Fore.CYAN + f"DEBUG: Starting processing for {len(chunks_list)} chunks..." + Style.RESET_ALL)

    for i, chunk in enumerate(chunks_list):
        heading = extract_chunk_heading(chunk).strip()
        if heading:
            print(Fore.MAGENTA + f"DEBUG: Chunk {i} Heading found: '{heading}'" + Style.RESET_ALL)

        if is_reference_heading(heading):
            print(Fore.CYAN + f"MATCH: Found Reference Heading: {heading}" + Style.RESET_ALL)
            
            raw_text = chunk.text
            if trailing_storage:
                print(Fore.MAGENTA + "DEBUG: Prepended Trailing_Storage." + Style.RESET_ALL)
                raw_text = trailing_storage + raw_text
                trailing_storage = ""

            # Logic for fragmented chunks
            if has_dash_in_last_n(raw_text) or len(raw_text) <= 200:
                print(Fore.YELLOW + f"DEBUG: Skipping chunk {i} (Length: {len(raw_text)}) - storing." + Style.RESET_ALL)
                dash_idx = raw_text.rfind('-')
                trailing_storage = raw_text[:dash_idx] if dash_idx != -1 else raw_text
                continue

            # LLM Logic
            if handle_llm_extraction(raw_text, output_path,llm_service):
                references_extracted = True

    # Final Fallback
    if not references_extracted:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump([], f)
        print(Fore.CYAN + "No references found; created empty JSON file." + Style.RESET_ALL)

    return references_extracted

def process_references_from_chunks_from_Sec_JSON(chunks, output_path,llm_service):
    """Main pipeline for reference extraction."""
    # Convert iterator to list immediately
    chunks_list = list(chunks) if not isinstance(chunks, list) else chunks
    
    if not validate_input_data(chunks_list, output_path):
        return False

    trailing_storage = ""
    references_extracted = False
    print(Fore.CYAN + f"DEBUG: Starting processing for {len(chunks_list)} chunks..." + Style.RESET_ALL)

    for i, chunk in enumerate(chunks_list):
        raw_text = chunk
        if trailing_storage:
            print(Fore.MAGENTA + "DEBUG: Prepended Trailing_Storage." + Style.RESET_ALL)
            raw_text = trailing_storage + raw_text
            trailing_storage = ""

        # Logic for fragmented chunks
        if has_dash_in_last_n(raw_text) or len(raw_text) <= 200:
            print(Fore.YELLOW + f"DEBUG: Skipping chunk {i} (Length: {len(raw_text)}) - storing." + Style.RESET_ALL)
            dash_idx = raw_text.rfind('-')
            trailing_storage = raw_text[:dash_idx] if dash_idx != -1 else raw_text
            continue

        # LLM Logic
        if handle_llm_extraction(raw_text, output_path,llm_service):
            references_extracted = True

    # Final Fallback
    if not references_extracted:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump([], f)
        print(Fore.CYAN + "No references found; created empty JSON file." + Style.RESET_ALL)

    return references_extracted

def docling_process_references_file(file_path, ID, Main_Folder):

    pdf_name = Path(file_path).name
    # Paths and Setup
    try:
        # # 1. Extraction
        doc = converter.convert(file_path).document
        chunks = list(chunker.chunk(dl_doc=doc))

        process_references_from_chunks(chunks,Main_Folder.ref_json_path)

        # 2. Safety Buffer: Wait up to 5 seconds for the file to actually appear on disk
        attempts = 0
        while not os.path.exists(Main_Folder.ref_json_path) and attempts < 5:
            time.sleep(1)
            attempts += 1

        # 3. Logging
        log_Ref_data_extracted(Main_Folder.refrences_excel_log_path, Main_Folder.ref_json_path, pdf_name, ID,time="test time")

    except Exception as e:
        print(Fore.RED + f"Process Error: {e}" + Style.RESET_ALL)
        import traceback
        traceback.print_exc()




if __name__ == "__main__":

    folder_path="/localdata/user/kata_du/Automated Literature Survey/02_Test_Storage"

    Main_Folder = DataAnalyzeManager(folder_path)

    Main_Folder.update_id_files("test_id")

    file_path="/localdata/user/kata_du/Automated Literature Survey/downloads/MBSE and AI/2022-Review Model-based Systems Engineering and Artific.pdf"
    docling_process_references_file(file_path,Main_Folder.current_id,Main_Folder)
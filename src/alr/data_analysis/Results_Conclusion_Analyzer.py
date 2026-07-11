
from alr.data_analysis.Data_analysis_system_prompts import ResCon_RP_KEYs_SP, Results_Conclusion_identification_SP
from alr.common.general_utils import caluculate_time_taken, clean_response_json_text
from alr.common.json_utils import get_key_from_file, store_to_json_with_text, print_json_file
from alr.common.llm_utils import llm_call

import re
import json
import time
from datetime import datetime
from colorama import Fore,Style
import os
import pandas as pd
import traceback
from pathlib import Path


# Common heading words that carry results/conclusion content in publications.
# A section whose name contains ANY of these (case-insensitive) is analyzed.
RESULTS_CONCLUSION_KEYWORDS = [
    "result",
    "conclusion",
    "summary",
    "discussion",
    "findings",
    "future work",
    "future research",
    "outlook",
    "overview",
    "closing remarks",
    "final remarks",
    "lessons learned",
]

# The five attributes extracted by the LLM (keys of ResCon_RP_KEYs_SP output).
RESULTS_CONCLUSION_KEYS = [
    "Results Mentioned",
    "Limitations or Boundary Conditions",
    "Summary of the Content",
    "Future Work",
    "Outlook",
]


def check_and_log_data(json_file_path, excel_file_path, ID, time_taken, analyzed_sections=None):
    """
    Behaviour:
    - Exactly ONE row per UUID.
    - If UUID exists: overwrite/update ALL columns for that UUID (even if not empty/NA).
    - If UUID does not exist: append a new row.
    - Also records WHICH sections were analyzed (Sections_Analyzed column).
    """

    try:
        # Load data from the given JSON file
        with open(json_file_path, "r", encoding="utf-8") as json_file:
            data = json.load(json_file)

        keys_to_check = list(RESULTS_CONCLUSION_KEYS)

        # Prepare the data to log
        row_data = {
            "UUID": ID,
            "time_taken": time_taken,
            "file_path": json_file_path,
            "Sections_Analyzed": ", ".join(analyzed_sections) if analyzed_sections else "",
        }

        for key in keys_to_check:
            if key in data:
                value = data[key]
                if isinstance(value, list):
                    if (not value) or any(
                        isinstance(item, str) and item.lower() == "no information available"
                        for item in value
                    ):
                        row_data[key] = "NA"
                    else:
                        row_data[key] = "A"
                elif value == "" or (isinstance(value, str) and value.lower() == "no information available"):
                    row_data[key] = "NA"
                else:
                    row_data[key] = "A"
            else:
                row_data[key] = "NA"

        # If the file doesn't exist, create it
        if not os.path.exists(excel_file_path):
            df = pd.DataFrame([row_data])
            df.to_excel(excel_file_path, index=False)
            return

        # Load existing data
        df = pd.read_excel(excel_file_path)

        # Ensure UUID column exists
        if "UUID" not in df.columns:
            df["UUID"] = ""

        # Ensure all row_data columns exist in df
        for col in row_data.keys():
            if col not in df.columns:
                df[col] = ""

        # Find matching UUID rows (string-safe)
        matches = df.index[df["UUID"].astype(str) == str(ID)].tolist()

        if matches:
            # Keep only ONE row for this UUID: use the first, drop any duplicates
            keep_idx = matches[0]
            dup_idxs = matches[1:]
            if dup_idxs:
                df = df.drop(index=dup_idxs).reset_index(drop=True)
                keep_idx = df.index[df["UUID"].astype(str) == str(ID)].tolist()[0]

            # OVERWRITE all entries for this UUID row (irrespective of empty/NA)
            for col, val in row_data.items():
                df.at[keep_idx, col] = val

        else:
            # UUID not found -> append
            new_row_df = pd.DataFrame([row_data])
            df = pd.concat([df, new_row_df], ignore_index=True)

        # Save back to excel
        df.to_excel(excel_file_path, index=False)
        print(f"Data successfully logged to {excel_file_path}")

    except json.JSONDecodeError as e:
        print(f"Invalid JSON input: {e}")
    except FileNotFoundError:
        print(f"File not found: {json_file_path}")
        traceback.print_exc()
    except Exception as e:
        print(f"An error occurred: {e}")
        traceback.print_exc()


def get_results_conclusion_sections(MF):
    """
    Scan the raw sections JSON for every section whose name contains a
    results/conclusion related keyword (Results, Conclusion, Summary, Overview,
    Future Work, Outlook, ...). Returns a list of (section_name, text) tuples in
    document order; sections with empty text are skipped.
    """
    matched = []
    try:
        with open(MF.raw_sec_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error: {e}")
        return matched

    def walk(obj):
        if isinstance(obj, dict):
            name = obj.get('Section Name')
            if isinstance(name, str) and any(kw in name.lower() for kw in RESULTS_CONCLUSION_KEYWORDS):
                text = obj.get('Text_Content')
                if isinstance(text, str) and text.strip():
                    matched.append((name.strip(), text.strip()))
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return matched


def get_results_conclusion_text(MF):
    """
    Identify the results/conclusion content of the current document.

    Same logic ladder as get_abstract_text / get_Introduction_text:
    1. Matching sections in the raw sections JSON (keyword-based, may be several
       - e.g. only a Conclusion, only Results, or Summary + Outlook).
    2. Fallback: chunks whose first characters look like such a heading.
    3. Fallback: LLM identification over the last chunks of the document.

    Returns (combined_text, analyzed_sections) where analyzed_sections names
    which sections were found and analyzed.
    """
    analyzed_sections = []
    sections = get_results_conclusion_sections(MF)
    if sections:
        analyzed_sections = [name for name, _ in sections]
        combined = "\n\n".join(f"{name}\n{text}" for name, text in sections)
        return combined, analyzed_sections

    Chunks = get_key_from_file(MF.raw_sec_json_path, 'Chunks')

    # Heading-like match: keyword within the first characters of a chunk
    # (results/conclusion sections live near the END of the document).
    for i, chunk in enumerate(Chunks[-20:]):
        head = str(chunk)[:30].lower()
        for kw in RESULTS_CONCLUSION_KEYWORDS:
            if kw in head:
                idx = len(Chunks) - len(Chunks[-20:]) + i
                next_chunk = Chunks[idx + 1] if idx + 1 < len(Chunks) else ''
                analyzed_sections = [f"chunk heading match: {kw}"]
                return str(chunk) + str(next_chunk), analyzed_sections

    if Chunks:
        User_prompt = f"Text to be assessed:\n {Chunks[-20:]}"
        llm_service = MF.llm_service
        rescon_text = llm_call(User_prompt, Results_Conclusion_identification_SP, llm_service)

        if rescon_text and 'ERROR_NO_RESULTS_CONCLUSION_FOUND' not in str(rescon_text):
            print(Fore.BLUE + f"\nIdentified Results & Conclusion Text:{rescon_text}")
            analyzed_sections = ["LLM identified content"]
            return rescon_text, analyzed_sections

    return '', analyzed_sections


def _record_analyzed_sections(json_file_path, analyzed_sections):
    """Add the list of analyzed section names into the stored analysis JSON."""
    try:
        with open(json_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        data["Sections Analyzed:"] = analyzed_sections
        with open(json_file_path, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Could not record analyzed sections: {e}")


def analyze_results_conclusion(ID, MF):

    MF.update_id_files(ID)

    rescon_text, analyzed_sections = get_results_conclusion_text(MF)

    if rescon_text:
        print(Fore.CYAN + f"\nSections analyzed for Results & Conclusion: {', '.join(analyzed_sections)}" + Style.RESET_ALL)
        start_time = time.time()
        User_prompt = f"Results and Conclusion text:\n {rescon_text}"
        llm_service = MF.llm_service
        system_output = clean_response_json_text(llm_call(User_prompt, ResCon_RP_KEYs_SP, llm_service))
        end_time = time.time()
        print(Fore.GREEN + "\n Analyzed Results & Conclusion Info: \n" + Style.RESET_ALL)
        time_taken = caluculate_time_taken(start_time, end_time)

        store_to_json_with_text(system_output, MF.rescon_json_path, time_taken, rescon_text, 'Results & Conclusion')

        if not Path(MF.rescon_json_path).exists():
            # The LLM response was not valid JSON, so nothing was stored.
            return 'F'

        _record_analyzed_sections(MF.rescon_json_path, analyzed_sections)

        print(Fore.MAGENTA + "\nIdentified Results & Conclusion Data:")
        print_json_file(MF.rescon_json_path)
        check_and_log_data(MF.rescon_json_path, MF.AD_ResCon_log_path, ID, time_taken, analyzed_sections)
        return 'P'
    else:
        return 'F'

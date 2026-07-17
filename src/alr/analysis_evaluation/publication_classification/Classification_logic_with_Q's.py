import sys


import traceback
import time as time_module
import os
import requests
from colorama import Fore, Style, init
import pandas as pd
from typing import Optional, List, Dict, Tuple
from collections import deque

from alr.analysis_evaluation.publication_classification.classification_questions import FIELD_AGNOSTIC_CLASSIFICATION_SECTIONS, Sys_prompt_cla
from alr.common.llm_utils import Local_Model_call

# Initialize colorama
init(autoreset=True)



def Classification_of_Phrase(title, question, max_retries: int = 3) -> bool:
    print(f"\n🔍 Evaluating:")
    print(f"   📄 Title: '{title}'")
    print(f"   ❓ Question: '{question}'")
    print(f"   {'-'*80}")

    Prompt=f"""
    You will now evaluate one title and one classification question.

    Title:
    "{title}"

    Question:
    "{question}"

    Please determine if the title clearly satisfies the condition stated in the question, 
    following the evaluation rules from the system instructions. 
    Respond with exactly one word — "True" or "False".

    """

    for attempt in range(max_retries):
        try:

            response_from_llm=Local_Model_call(Prompt, Sys_prompt_cla)

            print(Fore.CYAN + f"\n--- ATTEMPT {attempt + 1}/{max_retries} ---" + Style.RESET_ALL)
            print(Fore.YELLOW + f"📄 Title: {title}" + Style.RESET_ALL)
            print(Fore.YELLOW + f"❓ Question: {question}" + Style.RESET_ALL)
            print(Fore.CYAN + "--- RAW LLM RESPONSE ---" + Style.RESET_ALL)
            print(Fore.WHITE + response_from_llm + Style.RESET_ALL)
            print(Fore.CYAN + "---------------------" + Style.RESET_ALL)

            if "true" in response_from_llm.lower():
                print(Fore.GREEN + f"✅ RESULT: True (matched 'true' in response)" + Style.RESET_ALL)
                return True
            elif "false" in response_from_llm.lower():
                print(Fore.RED + f"❌ RESULT: False (matched 'false' in response)" + Style.RESET_ALL)
                return False
            else:
                print(Fore.YELLOW + f"⚠️  Unclear response: '{response_from_llm}'. Retrying... (Attempt {attempt + 2}/{max_retries})" + Style.RESET_ALL)
                continue
        
        except Exception as e:
            print(Fore.RED + f"❌ Error during LLM call (Attempt {attempt + 1}): {e}" + Style.RESET_ALL)
            traceback.print_exc()    
            if attempt == max_retries - 1:
                print(Fore.RED + "     Max retries exceeded. Defaulting to False." + Style.RESET_ALL)
                return False
    
    print(Fore.YELLOW + f"⚠️  Max retries exceeded. Final RESULT: False" + Style.RESET_ALL)
    return False

def save_all_sheets(summary_df: pd.DataFrame, sheets_dict: Dict[str, pd.DataFrame], output_file_path: str) -> bool:
    """Saves the Main Summary sheet followed by all individual component section sheets."""
    try:
        with pd.ExcelWriter(output_file_path, engine='openpyxl') as writer:
            # 1. Write Master Summary Sheet First
            summary_df.to_excel(writer, sheet_name="Summary_Main", index=False)
            
            # 2. Write individual broken-down section sheets
            for sheet_name, df in sheets_dict.items():
                # Clean up sheet names to stay within Excel's 31-character limit
                safe_sheet_name = sheet_name.replace(":", "").replace("/", "").replace("\\", "")[:31].strip()
                df.to_excel(writer, sheet_name=safe_sheet_name, index=False)
        return True
    except Exception as e:
        print(f"❌ Error saving Excel file: {e}")
        traceback.print_exc()    
        return False


def classify_excel_data_to_sheets(
    file_path: str,
    column_name: str,
    output_file_path: Optional[str] = None,
    progress_callback=None
) -> Optional[Dict[str, pd.DataFrame]]:
    """
    Evaluates each title in an Excel column against all classification sections.
    Generates a master Summary sheet along with individual breakdown sheets per section 
    using full question texts as column headers.
    Saves the workbook immediately after EVERY single question evaluation.
    """
    try:
        print(f"\n📂 Reading Excel file: {file_path}")
        input_df = pd.read_excel(file_path)
        print(f"✅ Loaded {len(input_df)} rows, {len(input_df.columns)} columns")
    except FileNotFoundError:
        print(f"❌ Error: File not found at path: {file_path}")
        traceback.print_exc()    
        return None
    except Exception as e:
        print(f"❌ Error reading Excel file: {e}")
        traceback.print_exc()    
        return None
    
    if column_name not in input_df.columns:
        print(f"❌ Error: Column '{column_name}' not found in the Excel file.")
        return None

    if output_file_path is None:
        output_file_path = file_path.replace(".xlsx", "Loacal_3.1-8bI_Classification.xlsx")
    
    # --- 1. Initialize Main Summary DataFrame ---
    print(f"🔧 Initializing Main Summary Sheet Layout...")
    summary_headers = [column_name] + list(FIELD_AGNOSTIC_CLASSIFICATION_SECTIONS.keys())
    init_summary_data = {col: [None] * len(input_df) for col in summary_headers}
    init_summary_data[column_name] = input_df[column_name].tolist()
    summary_df = pd.DataFrame(init_summary_data)

    # --- 2. Initialize Section Breakdown DataFrames using full Question strings as Columns ---
    sheets_data: Dict[str, pd.DataFrame] = {}
    print(f"🔧 Initializing individual section sheets layout...")
    for section, questions in FIELD_AGNOSTIC_CLASSIFICATION_SECTIONS.items():
        # Column names are now the actual question strings from your configuration list
        headers = [column_name] + list(questions) + ["Result"]
        init_data = {col: [None] * len(input_df) for col in headers}
        init_data[column_name] = input_df[column_name].tolist()
        sheets_data[section] = pd.DataFrame(init_data)
    
    # Pre-save workspace skeleton setup
    save_all_sheets(summary_df, sheets_data, output_file_path)
    
    print(f"{'='*90}")
    print(f"Starting Multi-Sheet Process (Live Summary + Question Text Autosave Active)...")
    print(f"{'='*90}\n")
    
    total_rows = len(input_df)
    
    for row_idx, title in enumerate(input_df[column_name]):
        if progress_callback:
            progress_callback(row_idx + 1, total_rows, str(title)[:60])
        print(f"\n🔍 Row {row_idx + 1}/{total_rows}")
        print(f"   Title: {str(title)[:85]}{'...' if len(str(title)) > 85 else ''}")
        print(f"   {'-'*85}")
        
        # Fallback handling for blank records
        if pd.isna(title) or str(title).strip() == "":
            print(f"   ⚠️  Empty title, defaulting all spaces to False/0.0.")
            for section, questions in FIELD_AGNOSTIC_CLASSIFICATION_SECTIONS.items():
                summary_df.at[row_idx, section] = 0.0
                for question in questions:
                    sheets_data[section].at[row_idx, question] = False
                sheets_data[section].at[row_idx, "Result"] = False
            save_all_sheets(summary_df, sheets_data, output_file_path)
            continue
        
        title_str = str(title)
        
        # Process every section for the current paper title row
        for section, questions in FIELD_AGNOSTIC_CLASSIFICATION_SECTIONS.items():
            print(f"\n   📌 Section Sheet: {section}")
            
            row_all_true = True
            true_count = 0
            
            for idx, question in enumerate(questions):
                is_match = Classification_of_Phrase(title_str, question)
                
                # Assign to individual breakdown sheet using the specific question text as key
                sheets_data[section].at[row_idx, question] = is_match
                
                if is_match:
                    true_count += 1
                else:
                    row_all_true = False
                
                # Dynamic Score Calculation for Summary Sheet (live-updating metric)
                current_score = (true_count / len(questions)) * 100
                summary_df.at[row_idx, section] = round(current_score, 1)
                
                # Save everything instantly upon collecting any single response item
                print(f"   💾 [Immediate Save] Updating column for Q{idx+1} and live summary tracking...")
                save_all_sheets(summary_df, sheets_data, output_file_path)
                    
            # Complete the row sequence by establishing final logic markers 
            sheets_data[section].at[row_idx, "Result"] = round(current_score, 1)
            save_all_sheets(summary_df, sheets_data, output_file_path)
            print(f"    ↳ Section Completed. Overall Sheet Result -> {row_all_true} | Score -> {round(current_score, 1)}; {summary_df.at[row_idx, section]}%")

    print(f"\n{'='*90}")
    print(f"✅ CLASSIFICATION COMPLETE!")
    print(f"   Master Summary and Sections compiled to: {output_file_path}")
    print(f"{'='*90}\n")
    
    return sheets_data


# --- Execution ---
if __name__ == "__main__":
    excel_file = "/remotedata/U/DLR+kata_du/ALR DATA/Only_MBSA/Title_Assessment.xlsx"
    target_column = 'Publication Name'

    modified_sheets = classify_excel_data_to_sheets(
        file_path=excel_file,
        column_name=target_column
    )
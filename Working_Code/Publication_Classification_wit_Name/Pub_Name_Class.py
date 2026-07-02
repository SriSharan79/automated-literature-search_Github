import sys
import time as time_module

import requests

import sys


sys.path.extend([
    r'src',
    r'src/COLLECTION',
    r'Working_Code',
    r'src/DATA_ANALYSIS',
    r'src/COMMON',
    r'src/Command_Line_UI'
])

from LLM_Config import BLABLADOR_BASE_URL, check_api_key
from LLM_Utils import*
from colorama import Fore, Style, init
import pandas as pd
from typing import Optional, List, Dict, Tuple

from Pub_Clas_Q_list import*


def blabla_ask_llm_msg(title, question,
    temperature: float = 0.3,
    max_tokens: int = 8192,
    blablador_key: str = None
) -> str:
    """Query Blablador LLM with dynamic model selection."""
    
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

   # print_with_separator("DebugLog",'/')
    
    # Dynamically select best model
    model = "01 - GPT-OSS-120b - an open model released by OpenAI in August 2025"
    # print(f"🤖 Using model: {model}")
    
    messages = [
        {'role': 'system', 'content': Sys_prompt_cla},
        {'role': 'user', 'content': Prompt}
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
    # before every API request
      
    time_module.sleep(5)   
    resp = requests.post(
                        url,
                        headers=headers,
                        json=payload,
                        timeout=60
                        )
    
    
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
    

    # try:
    #     log_llm_interaction(model,"BlaBla",messages,content.strip(),caluculate_time_taken(start_time,end_time))
    # except Exception as e:
    #     print('failed to log LLM Interaction')
           
    return content.strip() if content else ""


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

            # ENHANCED LOGGING: Show title, question, and LLM result clearly
            print(Fore.CYAN + f"\n--- ATTEMPT {attempt + 1}/{max_retries} ---" + Style.RESET_ALL)
            print(Fore.YELLOW + f"📄 Title: {title}" + Style.RESET_ALL)
            print(Fore.YELLOW + f"❓ Question: {question}" + Style.RESET_ALL)
            print(Fore.CYAN + "--- RAW LLM RESPONSE ---" + Style.RESET_ALL)
            print(Fore.WHITE + response_from_llm + Style.RESET_ALL)
            print(Fore.CYAN + "---------------------" + Style.RESET_ALL)

            # Check if response is clearly True or False
            if "true" in response_from_llm.lower():
                print(Fore.GREEN + f"✅ RESULT: True (matched 'true' in response)" + Style.RESET_ALL)
                return True
            elif "false" in response_from_llm.lower():
                print(Fore.RED + f"❌ RESULT: False (matched 'false' in response)" + Style.RESET_ALL)
                return False
            else:
                # If unclear, retry
                print(Fore.YELLOW + f"⚠️  Unclear response: '{response_from_llm}'. Retrying... (Attempt {attempt + 2}/{max_retries})" + Style.RESET_ALL)
                continue
        
        except Exception as e:
            print(Fore.RED + f"❌ Error during LLM call (Attempt {attempt + 1}): {e}" + Style.RESET_ALL)
            if attempt == max_retries - 1:
                print(Fore.RED + "     Max retries exceeded. Defaulting to False." + Style.RESET_ALL)
                return False
    
    print(Fore.YELLOW + f"⚠️  Max retries exceeded. Final RESULT: False" + Style.RESET_ALL)

    return False

 
def save_all_sheets(sheets_dict: Dict[str, pd.DataFrame], output_file_path: str) -> bool:
    """Saves multiple DataFrames into an Excel workbook with unique sheets."""
    try:
        with pd.ExcelWriter(output_file_path, engine='openpyxl') as writer:
            for sheet_name, df in sheets_dict.items():
                # Excel sheet names have a limit of 31 characters
                safe_sheet_name = sheet_name[:31].strip()
                df.to_excel(writer, sheet_name=safe_sheet_name, index=False)
        return True
    except Exception as e:
        print(f"❌ Error saving Excel file: {e}")
        return False


def classify_excel_data_to_sheets(
    file_path: str,
    column_name: str,
    output_file_path: Optional[str] = None
) -> Optional[Dict[str, pd.DataFrame]]:
    """
    Evaluates each title in an Excel column against all classification sections.
    Generates a master Summary sheet along with individual breakdown sheets per section.
    Saves the workbook immediately after EVERY single question evaluation.
    """
    try:
        print(f"\n📂 Reading Excel file: {file_path}")
        input_df = pd.read_excel(file_path)
        print(f"✅ Loaded {len(input_df)} rows, {len(input_df.columns)} columns")
    except FileNotFoundError:
        print(f"❌ Error: File not found at path: {file_path}")
        return None
    except Exception as e:
        print(f"❌ Error reading Excel file: {e}")
        return None
    
    if column_name not in input_df.columns:
        print(f"❌ Error: Column '{column_name}' not found in the Excel file.")
        return None

    if output_file_path is None:
        output_file_path = file_path.replace(".xlsx", "_Detailed_Classified.xlsx")
    
    # --- 1. Initialize Main Summary DataFrame ---
    print(f"🔧 Initializing Main Summary Sheet Layout...")
    summary_headers = [column_name] + list(FIELD_AGNOSTIC_CLASSIFICATION_SECTIONS.keys())
    init_summary_data = {col: [None] * len(input_df) for col in summary_headers}
    init_summary_data[column_name] = input_df[column_name].tolist()
    summary_df = pd.DataFrame(init_summary_data)

    # --- 2. Initialize Section Breakdown DataFrames ---
    sheets_data: Dict[str, pd.DataFrame] = {}
    print(f"🔧 Initializing individual section sheets layout...")
    for section, questions in FIELD_AGNOSTIC_CLASSIFICATION_SECTIONS.items():
        headers = [column_name] + [f"Question {i+1}" for i in range(len(questions))] + ["Result"]
        init_data = {col: [None] * len(input_df) for col in headers}
        init_data[column_name] = input_df[column_name].tolist()
        sheets_data[section] = pd.DataFrame(init_data)
    
    # Pre-save workspace skeleton setup
    save_all_sheets(summary_df, sheets_data, output_file_path)
    
    print(f"{'='*90}")
    print(f"Starting Multi-Sheet Process (Live Summary + Question Autosave Active)...")
    print(f"{'='*90}\n")
    
    total_rows = len(input_df)
    
    for row_idx, title in enumerate(input_df[column_name]):
        print(f"\n🔍 Row {row_idx + 1}/{total_rows}")
        print(f"   Title: {str(title)[:85]}{'...' if len(str(title)) > 85 else ''}")
        print(f"   {'-'*85}")
        
        # Fallback handling for blank records
        if pd.isna(title) or str(title).strip() == "":
            print(f"   ⚠️  Empty title, defaulting all spaces to False/0.0.")
            for section, questions in FIELD_AGNOSTIC_CLASSIFICATION_SECTIONS.items():
                summary_df.at[row_idx, section] = 0.0
                for i in range(len(questions)):
                    sheets_data[section].at[row_idx, f"Question {i+1}"] = False
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
                
                # Assign to individual breakdown sheet space
                sheets_data[section].at[row_idx, f"Question {idx+1}"] = is_match
                
                if is_match:
                    true_count += 1
                else:
                    row_all_true = False
                
                # Dynamic Score Calculation for Summary Sheet (live-updating metric)
                current_score = (true_count / len(questions)) * 100
                summary_df.at[row_idx, section] = round(current_score, 1)
                
                # CRITICAL: Save everything instantly upon collecting any single response item
                print(f"   💾 [Immediate Save] Updating Q{idx+1} and live summary tracking...")
                save_all_sheets(summary_df, sheets_data, output_file_path)
                    
            # Complete the row sequence by establishing final logic markers 
            sheets_data[section].at[row_idx, "Result"] = row_all_true
            save_all_sheets(summary_df, sheets_data, output_file_path)
            print(f"    ↳ Section Completed. Overall Sheet Result -> {row_all_true} | Score -> {summary_df.at[row_idx, section]}%")

    print(f"\n{'='*90}")
    print(f"✅ CLASSIFICATION COMPLETE!")
    print(f"   Master Summary and Sections compiled to: {output_file_path}")
    print(f"{'='*90}\n")
    
    return sheets_data


def evaluate_title_for_section(
    title: str,
    section: str,
    section_details: list
) -> float:
    """
    Evaluates a single title against all questions in a classification section.
    Returns a percentage score (0-100) based on the proportion of True answers.
    
    Args:
        title: The publication title to evaluate.
        section: The section name (e.g., 'STUDY_ILLUSTRATION').
    
    Returns:
        Float: Percentage score (0.0 to 100.0) for this section.
    """
    if section not in FIELD_AGNOSTIC_CLASSIFICATION_SECTIONS:
        print(f"  ❌ Section '{section}' not found in FIELD_AGNOSTIC_CLASSIFICATION_SECTIONS.")
        return 0.0
    
    questions = FIELD_AGNOSTIC_CLASSIFICATION_SECTIONS[section]
    true_count = 0
    
    
    print(f"\n    Evaluating section: {section}: questions: {len(questions)}")
    for idx, question in enumerate(questions, 1):
        is_match = Classification_of_Phrase(title, question)
        if is_match:
            true_count += 1
            print(f"      Q{idx}: ✅ True")
        else:
            print(f"      Q{idx}: ❌ False")
    
    percentage = (true_count / len(questions)) * 100
    print(f"    Result: {true_count}/{len(questions)} → {percentage:.1f}%")
    
    return percentage


def save_excel_file(df: pd.DataFrame, output_file_path: str) -> bool:
    """
    Saves the DataFrame to an Excel file.
    
    Args:
        df: The DataFrame to save.
        output_file_path: Path where to save the file.
    
    Returns:
        Boolean: True if successful, False otherwise.
    """
    try:
        df.to_excel(output_file_path, index=False)
        return True
    except Exception as e:
        print(f"❌ Error saving Excel file: {e}")
        return False


def classify_excel_data(
    file_path: str,
    column_name: str,
    output_file_path: Optional[str] = None
) -> Optional[pd.DataFrame]:
    """
    Evaluates each title in a specified Excel column against all 7 classification sections.
    For each section, calculates a percentage score based on the proportion of True answers.
    Stores results in new columns (one per section) and saves the enriched file.
    
    Args:
        file_path: Path to the Excel file (e.g., 'publications.xlsx').
        column_name: Name of the column containing publication titles.
        output_file_path: Optional custom output file path. 
                         If not provided, appends '_Classified.xlsx' to the input filename.
    
    Returns:
        The modified pandas DataFrame with classification columns, or None on error.
    """
    try:
        # 1. Read the Excel file
        print(f"\n📂 Reading Excel file: {file_path}")
        df = pd.read_excel(file_path)
        print(f"✅ Loaded {len(df)} rows, {len(df.columns)} columns")
    
    except FileNotFoundError:
        print(f"❌ Error: File not found at path: {file_path}")
        return None
    except Exception as e:
        print(f"❌ Error reading Excel file: {e}")
        return None
    
    # 2. Validate column exists
    if column_name not in df.columns:
        print(f"❌ Error: Column '{column_name}' not found in the Excel file.")
        print(f"   Available columns: {list(df.columns)}")
        return None
    
    print(f"✅ Column '{column_name}' found with {len(df)} entries")

    # 3. Determine output file path
    if output_file_path is None:
        output_file_path = file_path.replace(".xlsx", "_Classified.xlsx")
    
    # 4. Initialize classification columns
    print(f"🔧 Initializing classification columns...")
    for section in FIELD_AGNOSTIC_CLASSIFICATION_SECTIONS.keys():
        df[section] = None
    print(f"✅ Added {len(FIELD_AGNOSTIC_CLASSIFICATION_SECTIONS)} classification columns\n")
    
    # 5. Reorder columns once: place classification sections next to the original column
    print(f"📋 Reorganizing columns...")
    col_index = df.columns.get_loc(column_name)
    cols = df.columns.tolist()
    
    # Remove classification sections from their current position
    for section in FIELD_AGNOSTIC_CLASSIFICATION_SECTIONS.keys():
        if section in cols:
            cols.remove(section)
    
    # Insert them right after the original column
    for i, section in enumerate(FIELD_AGNOSTIC_CLASSIFICATION_SECTIONS.keys()):
        cols.insert(col_index + 1 + i, section)
    
    df = df[cols]
    print(f"✅ Columns reordered. Classification sections now follow '{column_name}'\n")
    
    # 6. Evaluate each title and save after each complete row evaluation
    print(f"{'='*90}")
    print(f"Starting evaluation process...")
    print(f"{'='*90}\n")
    
    total_rows = len(df)
    for row_idx, title in enumerate(df[column_name], 1):
        
        print(f"\n🔍 Row {row_idx}/{total_rows}")
        print(f"   Title: {str(title)[:85]}{'...' if len(str(title)) > 85 else ''}")
        print(f"   {'-'*85}")
        
        # Skip empty titles
        if pd.isna(title) or str(title).strip() == "":
            print(f"   ⚠️  Empty title, skipping all sections.")
            for section in FIELD_AGNOSTIC_CLASSIFICATION_SECTIONS.keys():
                df.at[row_idx - 1, section] = 0.0
        else:
            title_str = str(title)
            
            # Evaluate each section for this title
            for section in FIELD_AGNOSTIC_CLASSIFICATION_SECTIONS.keys():
                print(f"\n   📌 Section: {section}")
                score = evaluate_title_for_section(title_str, section)
                df.at[row_idx - 1, section] = round(score, 1)
        
        # 7. SAVE EXCEL FILE AFTER EACH TITLE IS COMPLETELY EVALUATED
        print(f"   💾 Saving Excel file after row {row_idx}...")
        if save_excel_file(df, output_file_path):
            print(f"   ✅ File saved: {output_file_path}")
        else:
            print(f"   ❌ Failed to save file.")
    
    print(f"\n{'='*90}")
    print(f"✅ CLASSIFICATION COMPLETE!")
    print(f"{'='*90}")
    print(f"\n📊 Summary:")
    print(f"   Total rows processed: {total_rows}")
    print(f"   Classification sections: {len(FIELD_AGNOSTIC_CLASSIFICATION_SECTIONS)}")
    print(f"   Output file: {output_file_path}")
    print(f"\n✨ The Excel file has been updated and saved with all classification scores.\n")
    
    return df

excel_file = "/remotedata/U/DLR+kata_du/ALR DATA/MBSE_MBSA_Aviation/Test_Title_Assessment_2.xlsx" 

# 2. Set the column to search in
target_column = 'Publication Name' 

modified_df = classify_excel_data_to_sheets(
    file_path=excel_file,
    column_name=target_column
)
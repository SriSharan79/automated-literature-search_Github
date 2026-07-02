import sys
sys.path.extend([
    r'src',
    r'src/COLLECTION',
    r'Working_Code',
    r'src/DATA_ANALYSIS',
    r'src/COMMON',
    r'src/Command_Line_UI'
])

from DATA_ANALYSIS.Data_analysis_system_prompts import Abstract_RP_KEYs_SP, Abstrat_identification_SP
from COMMON.General_Utils import caluculate_time_taken, clean_response_json_text,find_first_match_in_first_n_chars
from COMMON.JSON_file_Utils import get_value_by_pair, pretty_print_json_from_file, store_to_json, get_key_from_file, store_to_json_with_text
from COMMON.LLM_Utils import  llm_call

import re
import json
import time
from datetime import datetime
from colorama import Fore,Style
import os
import json
import pandas as pd
from datetime import datetime
import traceback


def check_and_log_data(json_file_path, excel_file_path, ID, time_taken):
    """
    Behaviour:
    - Exactly ONE row per UUID.
    - If UUID exists: overwrite/update ALL columns for that UUID (even if not empty/NA).
    - If UUID does not exist: append a new row.
    """

    try:
        # Load data from the given JSON file
        with open(json_file_path, "r", encoding="utf-8") as json_file:
            data = json.load(json_file)

        keys_to_check = [
            "Research Areas",
            "Research Problem",
            "Key Concepts",
            "Objective",
            "Methodology",
            "Results",
            "Conclusion",
        ]

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # kept (not used)

        # Prepare the data to log
        row_data = {
            "UUID": ID,
            "time_taken": time_taken,
            "file_path": json_file_path,
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
            # print(f"Data successfully logged to {excel_file_path}")
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
                # After reset_index, keep_idx may have shifted if we dropped rows above it.
                # Re-find the UUID row robustly:
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


def get_abstract_text(MF): 
    abstract_text=''   
    
    abstract_text = get_value_by_pair(MF.raw_sec_json_path, 'Section Name','ABSTRACT','Text_Content')

    Abstract_list=['ABSTRACT','\nABSTRACT']

    if not abstract_text:
        Chunks=get_key_from_file(MF.raw_sec_json_path,'Chunks')
        # print (Chunks)
        abstract_text=find_first_match_in_first_n_chars(Chunks[:8],Abstract_list,10)
        
        if not abstract_text:
            User_prompt=f"Text to be assessed:\n {Chunks[:8]}"
            llm_service=MF.llm_service
            abstract_text=llm_call(User_prompt, Abstrat_identification_SP,llm_service)

            print(Fore.BLUE + f"\nIdentifid Abstract Text:{abstract_text}")
    
    return abstract_text
    

def analyze_abstract(ID,MF):

    # MF.update_id_files(ID)
    MF.update_id_files(ID)
    
    abstract_text=get_abstract_text(MF)

    if abstract_text:
        # print(Fore.YELLOW + f"\n --- Abstarct Sending to LLM ({len(abstract_text)} chars) ---\n {abstract_text}" + Style.RESET_ALL)
        start_time = time.time()
        User_prompt=f"Abstract text:\n {abstract_text}"
        llm_service=MF.llm_service
        system_output=clean_response_json_text(llm_call(User_prompt, Abstract_RP_KEYs_SP,llm_service))
        end_time=time.time()
        print(Fore.GREEN+"\n Analyzed Abstract Info: \n"+ Style.RESET_ALL)
        time_taken=caluculate_time_taken(start_time,end_time)

        store_to_json_with_text(system_output, MF.abstract_json_path,time_taken,abstract_text,'Abstract')

        print(Fore.MAGENTA + "\nIdentifid Abstract Text:")
        print(Fore.LIGHTYELLOW_EX + f"  {abstract_text}")
        pretty_print_json_from_file(MF.abstract_json_path)
        check_and_log_data(MF.abstract_json_path,MF.AD_Abstract_log_path,ID,time_taken)
        return 'P'
    else:
        return 'F'

# analyze_abstract('8a6b8ab3')

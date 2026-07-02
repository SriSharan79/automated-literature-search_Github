import sys
import traceback

from alr.common.general_utils import is_similar
from alr.collection.search_phrase_generator_config import COLUMNS, COLUMNS_Keyphrase
import difflib
import json
import os
import re # Import regex
from typing import List,Dict,Any, Optional
from colorama import Fore, Style, init
import pandas as pd
from datetime import datetime
# Initialize colorama
init(autoreset=True)

def Log_keyPhrases(Key_Phrases, EXCEL_FILE_PATH):
    """
    Handles the core logic: loading existing data, performing deduplication,
    updating occurrence counts, appending new search phrases, and saving the file.
    """
    
    if os.path.exists(EXCEL_FILE_PATH):
        print(f"Loading existing data from {EXCEL_FILE_PATH}...")
        try:
            existing_df = pd.read_excel(EXCEL_FILE_PATH, engine='openpyxl')
        except Exception as e:
            print(f"Error reading Excel file: {e}. Starting with an empty DataFrame.")
            existing_df = pd.DataFrame(columns=COLUMNS_Keyphrase)
    else:
        print(f"File not found. Creating new data structure at {EXCEL_FILE_PATH}...")
        existing_df = pd.DataFrame(columns=COLUMNS_Keyphrase)

    # Convert existing DataFrame to a list of dicts for easier indexing/updates
    existing_records = existing_df.to_dict('records')
    
    # Process the new search results
    for new_pub in Key_Phrases:
        Phrase_name = new_pub['Phrase']
        
        found = False
        for existing_rec in existing_records:
            
            # Safely get Phrase as string, handling NaN/float/None
            existing_phrase = existing_rec.get('Phrase', '')
            if pd.isna(existing_phrase) or isinstance(existing_phrase, (float, type(None))):
                existing_phrase = ''
            else:
                existing_phrase = str(existing_phrase).strip()
            
            if existing_phrase == Phrase_name or is_similar(existing_phrase, Phrase_name):
                found = True  
                 # Increment the Occurrence count for the found phrase
                existing_rec['Occurrence'] += 1            
                print(f"UPDATED: '{Phrase_name}'")
                break
        
        # If not found, add the new publication as a new record
        if not found:
            new_pub['Occurrence'] = 1
            existing_records.append(new_pub)
            print(f"ADDED NEW: '{Phrase_name}'")

    # Convert the updated list of records back to a DataFrame
    final_df = pd.DataFrame(existing_records, columns=COLUMNS_Keyphrase)
    
    # Save the final DataFrame to the Excel file
    try:
        final_df.to_excel(EXCEL_FILE_PATH, index=False, engine='openpyxl')
        print(f"\nSuccessfully saved/updated data to {EXCEL_FILE_PATH}")
    except Exception as e:
        print(f"\nFATAL ERROR: Could not write to Excel file. Check permissions or if the file is open. Error: {e}")            
        traceback.print_exc()       


def log_Keyword_Json(CM):
    """
    Logs research parameters and results into a JSON file with a timestamp.
    Appends to existing data if the file already exists.
    """
    Log_EXCEL_FILE_PATH= CM.keywords_list_log_path

    filename=CM.keywords_list_json

    new_entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "research_area": CM.Research_Area,
        "research_questions": CM.Research_Question,
        "refined_scope": CM.Research_Scope,
        "generated_keywords": CM.Keyword_list
    }
    
    data = []
    
    target_dir = os.path.dirname(filename)

    # 2. Create the directory if it doesn't exist
    if target_dir and not os.path.exists(target_dir):
        os.makedirs(target_dir)

    # Check if file exists and load existing data
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                if not isinstance(data, list):
                    data = [data]
            except json.JSONDecodeError:
                data = []

    # Append the new result
    data.append(new_entry)
    
    # Save back to file with formatting
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    num_of_keywords=len(CM.Keyword_list)
    
    log_generated_list_file(filename, num_of_keywords, Log_EXCEL_FILE_PATH,CM)

    print(f"Logged successfully at {new_entry['timestamp']}\n")
    return f"Logged successfully at {new_entry['timestamp']}\n"



def log_generated_list_file(filename, Count, EXCEL_FILE_PATH, CM):

    COLUMNS = [
        'Time stamp', 
        'UUID',
        'Research Area'
        'File path',
        'Count'
    ]
    
    # Create the new record
    new_data = {
        'Time stamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'UUID':CM.topic_id,
        'Research Area': CM.Research_Area,
        'File path': filename,
        'Count': Count
    }

    new_df = pd.DataFrame([new_data])

    if os.path.exists(EXCEL_FILE_PATH):
        try:
            # Read existing data
            existing_df = pd.read_excel(EXCEL_FILE_PATH, engine='openpyxl')
            # Append new record
            updated_df = pd.concat([existing_df, new_df], ignore_index=True)
            print(f"Appending data to {EXCEL_FILE_PATH}...")
        except Exception as e:
            print(f"Error reading Excel file: {e}. Creating new file.")
            updated_df = new_df
    else:
        print(f"File not found. Creating new Excel file at {EXCEL_FILE_PATH}...")
        updated_df = new_df

    # Write back to Excel
    updated_df.to_excel(EXCEL_FILE_PATH, index=False, engine='openpyxl')
    return f"Log updated in {EXCEL_FILE_PATH}"



def aggregate_and_update_excel(new_publications,EXCEL_FILE_PATH):
    """
    Handles the core logic: loading existing data, performing deduplication,
    updating occurrence counts, appending new search phrases, and saving the file.
    """
    
    if os.path.exists(EXCEL_FILE_PATH):
        # Read the existing data
        print(f"Loading existing data from {EXCEL_FILE_PATH}...")
        try:
            existing_df = pd.read_excel(EXCEL_FILE_PATH, engine='openpyxl')
        except Exception as e:
            print(f"Error reading Excel file: {e}. Starting with an empty DataFrame.")
            existing_df = pd.DataFrame(columns=COLUMNS)
    else:
        # Initialize a new DataFrame if the file doesn't exist
        print(f"File not found. Creating new data structure at {EXCEL_FILE_PATH}...")
        existing_df = pd.DataFrame(columns=COLUMNS)

    
    # Convert existing DataFrame to a list of dicts for easier indexing/updates
    existing_records = existing_df.to_dict('records')
        
    # Process the new search results
    for new_pub in new_publications:
        pub_name = new_pub['Publication Name']
        pub_link = new_pub['Link']
        
        # Check if this publication already exists in the records
        found = False
        for i, existing_rec in enumerate(existing_records):
            
            # Use Publication Name as the unique identifier for matching
            if existing_rec['Publication Name'] == pub_name and existing_rec['Link']== pub_link :
                found = True
                
                # 1. Increment Occurrence
                existing_records[i]['Occurrence'] += 1
                
                # 2. Update Input Search Phrase Used (new line separated)
                existing_phrase_list = existing_rec['Search Phrase'].split('\n')
                new_phrase = new_pub['Search Phrase']
                
                if new_phrase not in existing_phrase_list:
                    # Append the new phrase on a new line
                    existing_records[i]['Search Phrase'] += '\n' + new_phrase
                
                print(f"UPDATED: '{pub_name}' (Occurrence: {existing_records[i]['Occurrence']})")
                break
        
        # If not found, add the new publication as a new record
        if not found:
            existing_records.append(new_pub)
            print(f"ADDED NEW: '{pub_name}'")

    # Convert the updated list of records back to a DataFrame
    final_df = pd.DataFrame(existing_records, columns=COLUMNS)
    
    # Save the final DataFrame to the Excel file
    try:
        final_df.to_excel(EXCEL_FILE_PATH, index=False, engine='openpyxl')
        print(f"\nSuccessfully saved/updated data to {EXCEL_FILE_PATH}")
    except Exception as e:
        print(f"\nFATAL ERROR: Could not write to Excel file. Check permissions or if the file is open. Error: {e}")        
        traceback.print_exc()       



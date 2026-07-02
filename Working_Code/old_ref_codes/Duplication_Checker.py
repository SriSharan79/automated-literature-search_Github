from colorama import Fore, Style, init
import difflib
import json
import os
import re # Import regex
from typing import List,Dict,Any
from colorama import Fore, Style, init
import pandas as pd
from datetime import datetime

COLUMNS_Keyphrase=COLUMNS = [
    'Time', 
    'Keywords', 
    'Phrase',
]
# Initialize colorama
init(autoreset=True)
def is_similar(str1, str2, threshold=0.8):
    """
    Check if two strings are similar based on a given similarity threshold using word occurrence.

    Args:
        str1 (str): The first string.
        str2 (str): The second string.
        threshold (float): The similarity threshold (default is 0.8).

    Returns:
        bool: True if the strings are similar, False otherwise.
    """
    # Use SequenceMatcher to compare word occurrence
    ratio = difflib.SequenceMatcher(None, str1.split(), str2.split()).ratio()
    return ratio >= threshold

def is_similar_Length(str1, str2, threshold=0.7):
    """
    Check if two strings are similar based on a given similarity threshold.

    Args:
        str1 (str): The first string.
        str2 (str): The second string.
        threshold (float): The similarity threshold (default is 0.8).

    Returns:
        bool: True if the strings are similar, False otherwise.
    """
    # Simple similarity check using length-based comparison
    len_str1 = len(str1)
    len_str2 = len(str2)

    if len_str1 < len_str2:
        return len_str1 / len_str2 >= threshold
    else:
        return len_str2 / len_str1 >= threshold

def merge_lists(initial_list, new_list):
    """
    Merge two lists ensuring no duplicates or near-duplicates (strings matching 80% or more).

    Args:
        initial_list (list): The initial list of strings.
        new_list (list): The new list of strings to be merged.

    Returns:
        list: The merged list with no duplicates or near-duplicates.
    """
    # Use a list to store the final merged result
    merged_list = []

    for item in initial_list + new_list:
        is_duplicate = False

        # Check if the current item should not be added due to similarity or duplication
        for existing_item in merged_list:
            if item == existing_item or is_similar(item, existing_item):
                print(f"{Fore.WHITE}Existing: {existing_item}")
                print(f"{Fore.RED}Skipping duplicate/near-duplicate: {item}")
                is_duplicate = True
                break

        # If the item is not a duplicate or near-duplicate, add it to the merged list
        if not is_duplicate:
            merged_list.append(item)

    return merged_list

def Log_keyPhrases(Key_Phrases,EXCEL_FILE_PATH):
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
    for new_pub in Key_Phrases:
        Phrase_name = new_pub['Phrase']
        
        # Check if this publication already exists in the records
        found = False
        for i, existing_rec in enumerate(existing_records):
            
            # Use Publication Name as the unique identifier for matching
            if existing_rec['Phrase'] == Phrase_name or is_similar(existing_rec['Phrase'],Phrase_name):
                found = True
                
                               
                print(f"UPDATED: '{Phrase_name}'")
                break
        
        # If not found, add the new publication as a new record
        if not found:
            existing_records.append(new_pub)
            print(f"ADDED NEW: '{Phrase_name}'")

    # Convert the updated list of records back to a DataFrame
    final_df = pd.DataFrame(existing_records, columns=COLUMNS)
    
    # Save the final DataFrame to the Excel file
    try:
        final_df.to_excel(EXCEL_FILE_PATH, index=False, engine='openpyxl')
        print(f"\nSuccessfully saved/updated data to {EXCEL_FILE_PATH}")
    except Exception as e:
        print(f"\nFATAL ERROR: Could not write to Excel file. Check permissions or if the file is open. Error: {e}")

# # Example usage
# initial_list = [
#     "artificial intelligence applications in natural language processing",
#     "artificial intelligence and machine learning in healthcare"
# ]

# new_list = [
#     "artificial intelligence for intelligent systems design",
#     "artificial intelligence and robotics engineering applications",
#     "artificial intelligence applications in natural language processing"  # near-duplicate
# ]

# merged_list = merge_lists(initial_list, new_list)
# print("\nMerged List:")
# for item in merged_list:
#     print(item)
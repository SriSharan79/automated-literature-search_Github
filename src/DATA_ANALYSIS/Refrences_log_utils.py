import sys
from typing import Counter
sys.path.extend([
    r'src',
    r'src/COLLECTION',
    r'Working_Code',
    r'src/DATA_ANALYSIS',
    r'src/COMMON',
    r'src/Command_Line_UI'
])

import re

import os
import pandas as pd
import json
from datetime import datetime
import traceback

def check_complex_reference_sequence(json_file_path):
    
    try:
        if not os.path.exists(json_file_path):
            return [], ["File not found yet"] # Return two empty lists
            
        with open(json_file_path, 'r') as f:
            data = json.load(f)
    except Exception as e:
        traceback.print_exc()
        # ALWAYS return two items so the unpack (a, b = func) doesn't fail
        
        return [], [f"Error: {e}"]

    processed_list = []
    
    for entry in data:
        raw_val = str(entry.get("ref_number", ""))
        # Extract only the digits using Regex
        match = re.search(r'\d+', raw_val)
        
        if match:
            num_val = int(match.group())
            processed_list.append({"original": raw_val, "num": num_val})
        else:
            processed_list.append({"original": raw_val, "num": None})

    # 1. Identify "Out of Sync" (Breaks in the N+1 flow)
    out_of_sync = []
    valid_nums = [item['num'] for item in processed_list if item['num'] is not None]
    
    for i in range(len(processed_list)):
        current = processed_list[i]
        
        # Flag entries that have no numbers at all
        if current['num'] is None:
            out_of_sync.append(f"Non-numeric: '{current['original']}'")
            continue
            
        # Check sequence against previous numeric entry
        if i > 0:
            # Find the last valid number before this index
            prev_nums = [item['num'] for item in processed_list[:i] if item['num'] is not None]
            if prev_nums and current['num'] != prev_nums[-1] + 1:
                out_of_sync.append(f"Jump/Duplicate: '{current['original']}' (follows {prev_nums[-1]})")

    # 2. Identify "Missing" (Gaps in the range)
    missing_nums = []
    if valid_nums:
        full_range = set(range(min(valid_nums), max(valid_nums) + 1))
        actual_set = set(valid_nums)
        missing_nums = sorted(list(full_range - actual_set))

    return missing_nums,out_of_sync

def count_entry_types(json_file_path):
    # 1. Always initialize the structure at the very top
    # This prevents 'Variable Not Defined' errors
    res = {
        "breakdown": {},
        "summary": {"Total": 0, "Publication": 0, "Others": 0}
    }
    
    try:
        # 2. Check if file is valid
        if not os.path.exists(json_file_path) or os.path.getsize(json_file_path) == 0:
            return res

        with open(json_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Ensure data is actually a list
        if not isinstance(data, list):
            return res

        # 3. Perform the counting
        # This handles the "type_counts" definition safely
        found_types = [str(entry.get("type", "Unknown")) for entry in data]
        counts = Counter(found_types)
        
        pub_count = counts.get("Publication", 0)
        total = len(data)
        
        # 4. Update our initialized structure
        res["breakdown"] = dict(counts)
        res["summary"]["Total"] = total
        res["summary"]["Publication"] = pub_count
        res["summary"]["Others"] = total - pub_count

    except Exception as e:
        print(f"Counting logic failed: {e}")
        traceback.print_exc()
        # We don't return 'e' as a string anymore; we return the safe 'res' dict
        
    return res


def repair_truncated_json(raw_text):
    """
    Robust repair for potentially truncated JSON arrays.
    """
    # Normalize whitespace and remove BOM
    text = re.sub(r'\uFEFF', '', raw_text.strip())

    # Try parsing as-is
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"Parse failed: {e}")

    # Find potential start of array
    start_idx = text.find('[')
    if start_idx == -1:
        raise ValueError("No JSON array found")
    text = text[start_idx:]

    # Balance braces from end: find matching ] for [
    brace_count = 0
    last_valid_end = -1
    for i, char in enumerate(reversed(text)):
        if char == ']':
            brace_count += 1
        elif char == '[':
            brace_count -= 1
            if brace_count == 0:
                last_valid_end = len(text) - i
                break

    if last_valid_end == -1:
        raise ValueError("Could not balance JSON array")

    # Take up to balanced end, remove trailing comma
    repaired = text[:last_valid_end]
    repaired = re.sub(r',\s*([}\]])', r'\1', repaired)

    try:
        result = json.loads(repaired)
        print(f"Repaired to {len(result)} objects")
        return result
    except json.JSONDecodeError as e:
        raise ValueError(f"Repair failed: {e}")

def save_references_to_json(ai_response_text, file_path):
    """
    Parses the AI response and appends/saves it to a specific file path.
    Handles Markdown code blocks and raw JSON strings.
    """
    try:
        # 1. Extract JSON content using Regex 
        # This finds the content between the first [ and the last ]
        match = re.search(r'\[.*\]', ai_response_text, re.DOTALL)
        if match:
            cleaned_response = match.group(0)
        else:
            # Fallback: just strip whitespace if no brackets found
            cleaned_response = ai_response_text.strip()

        # If you have a repair function, use it here
        # safe_json_str = repair_truncated_json(cleaned_response)
        # For now, we'll use the cleaned_response directly
        new_entries = json.loads(cleaned_response)
        
        # Ensure new_entries is a list for consistency
        if not isinstance(new_entries, list):
            new_entries = [new_entries]

        # 2. Ensure the directory exists
        directory = os.path.dirname(file_path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory)

        # 3. Handle file appending or creation
        final_data = []
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                try:
                    existing_content = json.load(f)
                    if isinstance(existing_content, list):
                        final_data = existing_content
                    else:
                        final_data = [existing_content]
                except json.JSONDecodeError:
                    final_data = []

        # Combine old data with new data
        final_data.extend(new_entries)

        # 4. Save to the specified path
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(final_data, f, indent=4, ensure_ascii=False)
        
        print(f"Successfully updated: {file_path}")

    except json.JSONDecodeError as e:
        print(f"Failed to parse AI response as JSON: {e}")
        print(f"Raw attempted string: {cleaned_response[:100]}...")
        traceback.print_exc()
    except Exception as e:
        traceback.print_exc()
        print(f"An unexpected error occurred: {e}")

def log_Ref_data_extracted(excel_log_path, JSON_path, pdf_name, ID, Time_taken="NA"):
    # 1. Run your existing analysis functions
    missing_nums, out_of_sync = check_complex_reference_sequence(JSON_path)
    #  Check if JSON is valid before proceeding
    if not os.path.exists(JSON_path) or os.path.getsize(JSON_path) == 0:
        print(f"Skipping log: {JSON_path} is not ready.")
        return

    results = count_entry_types(JSON_path)

    # 2. Extract values
    file_name = os.path.basename(JSON_path)
    tot_entries = results['summary']['Total']
    pub_entries = results['summary']['Publication']
    other_entries = results['summary']['Others']
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Define columns in the exact requested order
    columns = [
        "UUID", "Pdf name", "date-time","Time taken", "file name", 
        "Tot entries", "Pub entries", "others", 
        "Missing sequence", "Out of sync"
    ]

    # 3. Create a dictionary for the current run
    new_entry = {
        "UUID": str(ID),
        "Pdf name": pdf_name,
        "date-time": current_time,
        "Time taken": Time_taken,
        "file name": file_name,
        "Tot entries": tot_entries,
        "Pub entries": pub_entries,
        "others": other_entries,
        "Missing sequence": str(missing_nums),
        "Out of sync": str(out_of_sync)
    }

    # 4. Read existing Excel or create a new DataFrame
    if os.path.exists(excel_log_path):
        df = pd.read_excel(excel_log_path)
    else:
        df = pd.DataFrame(columns=columns)

    # 5. Check if entry exists (match by ID and Pdf name)
    mask = (df['UUID'].astype(str) == str(ID)) & (df['Pdf name'] == pdf_name)
    
    if not df[mask].empty:
        # Get the first matching index
        existing_idx = df[mask].index[0]
        
        # Check if values (excluding date-time) vary
        cols_to_compare = ["file name", "Tot entries", "Pub entries", "others", "Missing sequence", "Out of sync"]
        changed = False
        for col in cols_to_compare:
            if str(df.at[existing_idx, col]) != str(new_entry[col]):
                changed = True
                break
        
        if changed:
            print(f"Updates found for {pdf_name}. Updating row...")
            for col, value in new_entry.items():
                df.at[existing_idx, col] = value
        else:
            print(f"No changes detected for {pdf_name}. Skipping update.")
            return # Exit function without saving
    else:
        # If new file, append to the dataframe
        print(f"Adding new entry for {pdf_name}...")
        df = pd.concat([df, pd.DataFrame([new_entry])], ignore_index=True)

    # 6. Save with the correct column order
    df = df[columns]
    df.to_excel(excel_log_path, index=False)
    print("Log saved successfully.")
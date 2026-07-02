
from COMMON.File_Manager import DataAnalyzeManager, Vec_DB_Manager

import pandas as pd
from thefuzz import fuzz
import os

# def update_excel_inplace(file_with_uuid, file_to_update):
#     # 1. Load the files
#     df_source = pd.read_excel(file_with_uuid)
#     df_target = pd.read_excel(file_to_update)

#     # Helper to find the "Title" column regardless of casing (Title vs title)
#     def get_col_name(df, name):
#         for col in df.columns:
#             if col.strip().lower() == name.lower():
#                 return col
#         raise KeyError(f"Could not find a column named '{name}' in the excel file.")

#     source_title_col = get_col_name(df_source, 'filename')
#     target_title_col = get_col_name(df_target, 'File_Name')


#     # Normalize source for matching
#     df_source['match_key'] = df_source[source_title_col].astype(str).str.lower().str.strip()
    
#     memo = {}

#     def find_match(target_val,col_name):
#         target_clean = str(target_val).lower().strip()
        
#         if not target_clean or target_clean == 'nan':
#             return None
#         if target_clean in memo:
#             return memo[target_clean]

#         for _, row in df_source.iterrows():
#             source_clean = row['match_key']
            
#             # Subset check or 95% similarity
#             if (source_clean in target_clean) or (target_clean in source_clean) or (fuzz.ratio(source_clean, target_clean) >= 95):
#                 memo[target_clean] = row['UUID']
#                 return row['UUID']
        
#         memo[target_clean] = None
#         return None

#     # 2. Perform the match using the correct column name
#     print(f"Analyzing titles in {file_to_update}...")
#     new_uuids = df_target[target_title_col].apply(find_match)

#     # 3. Update the UUID column
#     df_target['UUID'] = new_uuids

#     # 4. Clean up and overwrite
#     df_target.to_excel(file_to_update, index=False)
#     print(f"Successfully updated {file_to_update} in place.")


def update_excel_inplace(file_with_uuid, file_to_update):
    # 1. Load the files
    df_source = pd.read_excel(file_with_uuid)
    df_target = pd.read_excel(file_to_update)

    # Columns we want to bring over from source to target
    cols_to_transfer = ['UUID', 'sectioning', 'references', 'abstract']

    # Helper to find column names regardless of casing
    def get_actual_col_name(df, name):
        for col in df.columns:
            if col.strip().lower() == name.lower():
                return col
        return None

    source_title_col = get_actual_col_name(df_source, 'filename')
    target_title_col = get_actual_col_name(df_target, 'File_Name')

    if not source_title_col or not target_title_col:
        print("Error: Could not find 'Title' column in one of the files.")
        return

    # Normalize source for matching
    df_source['match_key'] = df_source[source_title_col].astype(str).str.lower().str.strip()
    
    # Cache to store the full row of data for found matches
    memo = {}

    def find_source_row(target_val):
        target_clean = str(target_val).lower().strip()
        
        if not target_clean or target_clean == 'nan':
            return None
        if target_clean in memo:
            return memo[target_clean]

        for _, row in df_source.iterrows():
            source_clean = row['match_key']
            
            # Match Criteria: Subset or 95% Similarity
            if (source_clean in target_clean) or (target_clean in source_clean) or (fuzz.ratio(source_clean, target_clean) >= 95):
                # Store the required columns in the cache
                match_data = {col: row.get(get_actual_col_name(df_source, col)) for col in cols_to_transfer}
                memo[target_clean] = match_data
                return match_data
        
        memo[target_clean] = None
        return None

    # 2. Perform the matching
    print(f"Processing matches for {file_to_update}...")
    # This creates a series of dictionaries
    results = df_target[target_title_col].apply(find_source_row)

    # 3. Expand the results into the target dataframe
    for col in cols_to_transfer:
        # Extract specific column from the results dictionary series
        df_target[col] = results.apply(lambda x: x[col] if x is not None else None)

    # 4. Save the file in place
    df_target.to_excel(file_to_update, index=False)
    print(f"Successfully updated columns {cols_to_transfer} in {file_to_update}")

def update_pdf_status_recursive(target_file, storage_root_folder):
    # 1. Load the target excel file
    df = pd.read_excel(target_file)

    # Helper to find column name
    def get_col_name(df, name):
        for col in df.columns:
            if col.strip().lower() == name.lower().replace(" ", "_"):
                return col
        return None

    filename_col = get_col_name(df, 'File_Name')
    
    if not filename_col:
        print(f"Error: Could not find 'File_Name' column in {target_file}")
        return

    # 2. Build a set of ALL .pdf filenames in the storage folder RECURSIVELY
    pdf_files_in_storage = set()
    
    if os.path.exists(storage_root_folder):
        print(f"Scanning for PDF files in: {storage_root_folder}...")
        for root, dirs, files in os.walk(storage_root_folder):
            for file in files:
                if file.lower().endswith('.pdf'):
                    # Store lowercase version for more flexible matching
                    pdf_files_in_storage.add(file.strip().lower())
    else:
        print(f"Error: The folder path '{storage_root_folder}' does not exist.")
        return

    # 3. Check existence and update 'Downloaded' column
    print(f"Checking {len(df)} entries against {len(pdf_files_in_storage)} PDFs found...")
    
    def check_pdf(fname):
        if pd.isna(fname) or str(fname).strip() == "":
            return "No"
        
        # Normalize the name from Excel
        target_fname = str(fname).strip().lower()
        
        # If the Excel name doesn't have .pdf, add it for the check
        if not target_fname.endswith('.pdf'):
            target_fname += ".pdf"
            
        return "Yes" if target_fname in pdf_files_in_storage else "No"

    # Update or create the 'Downloaded' column
    df['Downloaded'] = df[filename_col].apply(check_pdf)

    # 4. Save the file in place
    df.to_excel(target_file, index=False)
    print(f"Successfully updated 'Downloaded' status for PDFs in {target_file}")

# Usage
storage_path ="/remotedata/U/DLR+kata_du/ALR DATA"

mf = DataAnalyzeManager('/remotedata/U/DLR+kata_du/ALR DATA/SLR_Process_Main/SLR_Process_results')
source_file= mf.excel_success

target_file='/remotedata/U/DLR+kata_du/ALR DATA/SLR_Process_Main/SLR_Process_download_log.xlsx'

update_pdf_status_recursive(target_file, storage_path)
update_excel_inplace(source_file,target_file)
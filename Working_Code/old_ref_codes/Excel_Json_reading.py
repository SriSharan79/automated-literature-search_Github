import re
from docling.document_converter import DocumentConverter
from docling.chunking import HybridChunker
from colorama import Fore,Style
import os
import pandas as pd
import json

from title_extracter import*

def load_existing_data(file_path, is_excel=True):
    """Safely load Excel or JSON, return empty DataFrame if missing/corrupt."""
    if not os.path.exists(file_path):
        return pd.DataFrame()
    
    try:
        if is_excel:
            return pd.read_excel(file_path)
        return pd.read_json(file_path, orient='records')
    except Exception as e:
        print(f"Warning: Failed to load {file_path}: {e}")
        return pd.DataFrame()

def get_processed_files(df):
    """Safely extract 'File Name' set from DataFrame."""
    if df.empty or 'File Name' not in df.columns:
        return set()
    return set(df['File Name'].tolist())

def sync_histories(df_excel, df_json):
    """Merge missing files across Excel and JSON DataFrames."""
    excel_files = get_processed_files(df_excel)
    json_files = get_processed_files(df_json)
    
    # Sync JSON-only to Excel
    json_only = json_files - excel_files
    if json_only and not df_excel.empty:
        missing_df = df_json[df_json['File Name'].isin(json_only)]
        df_excel = pd.concat([df_excel, missing_df], ignore_index=True)
        print(f"Sync: Added {len(json_only)} JSON-only files to Excel.")
    
    # Sync Excel-only to JSON
    excel_only = excel_files - json_files
    if excel_only and not df_json.empty:
        missing_df = df_excel[df_excel['File Name'].isin(excel_only)]
        df_json = pd.concat([df_json, missing_df], ignore_index=True)
        print(f"Sync: Added {len(excel_only)} Excel-only files to JSON.")
    
    return df_excel, df_json


def should_process_file(file_name, processed_files, skip_files):
    """Check if file needs processing."""
    valid_ext = file_name.lower().endswith('.pdf')
    return valid_ext and file_name not in processed_files and file_name not in skip_files


def save_data(df, file_path, is_excel=True, file_type_name="file"):
    """Save DataFrame to Excel or JSON."""
    try:
        if is_excel:
            df.to_excel(file_path, index=False)
        else:
            df.to_json(file_path, orient='records', indent=4)
        print(f"✓ {file_type_name.capitalize()} saved: {len(df)} total records.")
    except Exception as e:
        print(f"Error saving {file_path}: {e}")
from pathlib import Path
import sys
import os

import PyPDF2
sys.path.extend([
    r'src',
    r'src/COLLECTION',
    r'Working_Code',
    r'src/DATA_ANALYSIS',
    r'src/COMMON',
    r'src/Command_Line_UI'
])
import os
import pandas as pd
from PyPDF2 import PdfReader
from colorama import init, Fore, Style
from DATA_ANALYSIS.title_extracter import *
import traceback

# Initialize colorama
init(autoreset=True)

def get_page_count(file_path):
    """
    Attempts to read the PDF. Returns page count if successful,
    returns None if the file is unreadable.
    """
    try:
        # Using a context manager ensures the file is closed quickly
        with open(file_path, 'rb') as f:
            reader = PdfReader(f)
            return len(reader.pages)
    except Exception:
        # Return None to indicate the file couldn't be parsed
        return None

def index_pdfs_with_skip_logic(root_folder, output_file):
    pdf_data = []
    
    # 1. Gather all files first
    all_paths = []
    for root, _, files in os.walk(root_folder):
        for file in files:
            if file.lower().endswith('.pdf'):
                all_paths.append((root, file))

    if not all_paths:
        print(Fore.RED + "No PDF files found.")
        return

    print(Fore.CYAN + f"Starting analysis of {len(all_paths)} files...\n")

    all_paths=all_paths[158:]

    # 2. Process incrementally
    for index, (root, file) in enumerate(all_paths):
        full_path = os.path.join(root, file)
        
        # Log to console
        print(Fore.YELLOW + f"[{index + 1}/{len(all_paths)}] " + Fore.WHITE + f"Scanning: {file}")
        
        pages = get_page_count(full_path)

        try:
            # title =get_title_in_the_file(full_path, 'b')

            base_data=extract_meta_data_from_doi(full_path)
            title=base_data['title']
        except FileNotFoundError:
            title = "Title Identification Failed"
        except Exception as e:
            title = "Title Identification Failed"
            traceback.print_exc()    
        
        # Determine status message for logging
        status_log = pages if pages is not None else "Not Readable/Skipped"
        if pages is None:
            print(Fore.RED + f"   ! Error reading {file}. Skipping page count.")

        # Append data (using 'pages' which is either a number or None)
        pdf_data.append({
            'File Name': file,
            'title': title,
            'Path': full_path,
            'Page Count': status_log
        })

        # 3. Analyze for duplicates
        df = pd.DataFrame(pdf_data)
        df['Occurrence Count'] = df.groupby('File Name')['File Name'].transform('count')
        df['Is Duplicate'] = df['Occurrence Count'] > 1

        # 4. Save progress
        try:
            df.to_excel(output_file, index=False)
        except PermissionError:
            print(Fore.LIGHTRED_EX + "   [!] Close the Excel file to allow saving!")
        except Exception as e:
            print(Fore.RED + f"   [!] Save failed: {e}")

    print("\n" + Fore.GREEN + Style.BRIGHT + "DONE! Total inventory saved.")

# --- Configuration ---
target_directory = '/remotedata/U/DLR+kata_du/ALR DATA/Only_MBSA/'  # Use 'r' before the string for Windows paths
output_excel = '/remotedata/U/DLR+kata_du/ALR DATA/Only_MBSA/pdf_inventory.xlsx'

index_pdfs_with_skip_logic(target_directory, output_excel)
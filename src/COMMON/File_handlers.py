import sys
sys.path.extend([
    r'src',
    r'src/COLLECTION',
    r'Working_Code',
    r'src/DATA_ANALYSIS',
    r'src/COMMON',
    r'src/Command_Line_UI'
])
import pandas as pd
import os
import shutil
from pathlib import Path
from colorama import Fore, Style, init

# Initialize colorama
init(autoreset=True)


def move_matching_pdfs(filenames_to_find, search_root, destination_folder):
    """
    Recursively searches for PDFs matching the filenames and moves them 
    to the destination_folder.
    """
    search_root = Path(search_root)
    destination_folder = Path(destination_folder)
    
    moved_count = 0
    
    # Ensure filenames end with .pdf for the search if they don't already
    target_files = {f if f.lower().endswith('.pdf') else f"{f}.pdf" for f in filenames_to_find}

    print(f"Searching recursively for {len(target_files)} unique PDF files...")

    # rglob("*") searches recursively
    for file_path in search_root.rglob("*.pdf"):
        if file_path.name in target_files:
            # Avoid moving if the file is already at the destination root
            if file_path.parent == destination_folder:
                continue
                
            try:
                # Move the file to the destination folder
                shutil.move(str(file_path), str(destination_folder / file_path.name))
                moved_count += 1
                print(f"Moved: {file_path.name}")
            except Exception as e:
                print(f"Error moving {file_path.name}: {e}")

    print(f"PDF relocation complete. Total moved: {moved_count}")


# def copy_file(source_path, destination_folder):
#     """
#     Copies a file from source_path to destination_folder.
#     """
#     try:
#         # Use copy2 to preserve metadata (timestamps, etc.)
#         new_path = shutil.copy2(source_path, destination_folder)
#         print(f"File copied successfully to: {new_path}")
#     except Exception as e:
#         print(f"Error copying file: {e}")

def sanitize_path_length(file_path,max_length=250):
    path_obj = Path(file_path)
    full_path_str = str(path_obj)
    
    if len(full_path_str) <= max_length:
        print(f"{Fore.GREEN}Path is safe ({len(full_path_str)} chars).")
        return full_path_str
    
    # Calculate how much we need to trim
    # We keep the parent directory and the extension intact
    directory = path_obj.parent
    extension = path_obj.suffix
    stem = path_obj.stem
    
    # Calculate available space for the stem
    # (Max - directory length - separator - extension length)
    allowed_stem_len = max_length - len(str(directory)) - 1 - len(extension)
    
    if allowed_stem_len <= 0:
        print(f"{Fore.RED}Error: Directory path is too long to even fit an extension.")
        return str(directory) 

    trimmed_stem = stem[:allowed_stem_len]
    new_path = directory / f"{trimmed_stem}{extension}"
    
    print(f"{Fore.YELLOW}Path too long ({len(full_path_str)} chars).")
    print(f"{Fore.CYAN}Trimmed to: {new_path}")
    
    return str(new_path)

def copy_matching_jsons(filenames, search_root, dest_folder):
    # Ensure destination exists
    dest_folder.mkdir(parents=True, exist_ok=True)
    
    # Standardize target list to ensure everything ends in .json
    target_files = {f if f.lower().endswith('.json') else f"{f}.json" for f in filenames}
    
    # Recursively search for .json files
    for file_path in Path(search_root).rglob("*.json"):
        if file_path.name in target_files:
            dest_path = dest_folder / file_path.name
            
            # Check if source and destination are actually the same file
            if file_path.resolve() == dest_path.resolve():
                continue # Skip copying if they are the same
                
            shutil.copy2(file_path, dest_path)

def copy_matching_pdfs(filenames, search_root, dest_folder):
    # Ensure destination exists
    dest_folder.mkdir(parents=True, exist_ok=True)
    
    # Standardize target list to ensure everything ends in .pdf
    target_files = {f if f.lower().endswith('.pdf') else f"{f}.pdf" for f in filenames}
    
    # Recursively search for .pdf files
    for file_path in Path(search_root).rglob("*.pdf"):
        if file_path.name in target_files:
            dest_path = dest_folder / file_path.name
            
            # Resolve both paths to their absolute form to compare
            # This prevents the "SameFileError" if the file is already there
            if file_path.resolve() == dest_path.resolve():
                continue  # Skip this file and move to the next
                
            shutil.copy2(file_path, dest_path)

def copy_file(src, dest_dir):
    """Standardized simple copy function."""
    try:
        shutil.copy2(str(src), str(dest_dir))
    except Exception as e:
        print(f"Failed to copy {src}: {e}")



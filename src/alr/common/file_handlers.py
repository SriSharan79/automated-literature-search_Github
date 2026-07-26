import pandas as pd
import hashlib
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


def safe_path(file_path, max_length=250):
    """
    Return a filesystem path guaranteed to be a real, writable FILE whose total
    length stays within ``max_length`` — so a long name (a filename-derived id,
    a title) or a deeply nested storage tree can't push the path past the OS
    limit and make the write fail.

    A short path is returned unchanged (as a ``Path``). When it is too long the
    stem is shortened to ``<readable-prefix>_<8-hex-hash><ext>``: the hash of
    the ORIGINAL stem keeps every distinct name unique (no collisions from a
    trimmed shared prefix), and the function is deterministic, so a later read
    resolves to exactly the file the write produced. Unlike a plain stem-trim it
    never degenerates into a bare directory — there is always a filename to
    write, even when the directory alone is very long (the returned path may
    then still exceed the conservative budget, but it is the shortest valid
    unique name we can offer for that directory).
    """
    path_obj = Path(file_path)
    if len(str(path_obj)) <= max_length:
        return path_obj

    directory = path_obj.parent
    ext = path_obj.suffix
    stem = path_obj.stem
    digest = hashlib.sha1(stem.encode("utf-8")).hexdigest()[:8]
    allowed = max_length - (len(str(directory)) + 1) - len(ext)

    if allowed <= len(digest):
        # Directory leaves (almost) no room; use just the unique hash.
        name = digest if allowed <= 0 else digest[:allowed]
    else:
        keep = allowed - 1 - len(digest)      # room for "_" + hash
        name = f"{stem[:keep]}_{digest}"
    new_path = directory / f"{name}{ext}"
    print(f"{Fore.YELLOW}Path too long ({len(str(path_obj))} chars) — writing to: {new_path}")
    return new_path


def sanitize_path_length(file_path, max_length=250):
    """
    Backwards-compatible wrapper over :func:`safe_path` that returns a ``str``.
    Prefer ``safe_path`` in new code (it returns a ``Path`` and never yields a
    bare directory).
    """
    return str(safe_path(file_path, max_length=max_length))

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



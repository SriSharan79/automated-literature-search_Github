import os
import pandas as pd


def check_files_in_directory(excel_path, search_directory, output_path):
    # 1. Load the Excel file
    try:
        df = pd.read_excel(excel_path)
    except Exception as e:
        print(f"Error reading the Excel file: {e}")
        return

    # Ensure the column exists (Change 'File Name' to match your actual column header)
    column_name = "File_Name"
    if column_name not in df.columns:
        print(f"Error: Column '{column_name}' not found in the Excel file.")
        return

    # 2. Build a map of file names to their absolute paths by scanning the directory
    # This is much faster than scanning the disk for every single row
    file_pool = {}
    print("Scanning directory and subdirectories... Please wait.")
    for root, dirs, files in os.walk(search_directory):
        for file in files:
            # Storing the lowercase name to make the search case-insensitive
            file_pool[file.lower()] = os.path.abspath(os.path.join(root, file))

    # 3. Lists to store our new columns
    exists_status = []
    found_paths = []

    # 4. Check each file name against our pool
    for file_name in df[column_name]:
        # Handle empty/NaN cells in Excel safely
        if pd.isna(file_name):
            exists_status.append("No")
            found_paths.append("")
            continue

        file_name_str = str(file_name).strip().lower()

        if file_name_str in file_pool:
            exists_status.append("Yes")
            found_paths.append(file_pool[file_name_str])
        else:
            exists_status.append("No")
            found_paths.append("")

    # 5. Add the new columns to the DataFrame
    df["Exists"] = exists_status
    df["File Path"] = found_paths

    # 6. Save to a new Excel file
    try:
        df.to_excel(output_path, index=False)
        print(f"Success! Updated file saved to: {output_path}")
    except Exception as e:
        print(f"Error saving the new Excel file: {e}")

import os
import shutil


def copy_matching_files(file_list, search_folder, destination_folder):
    # 1. Create the destination folder if it doesn't already exist
    if not os.path.exists(destination_folder):
        os.makedirs(destination_folder)
        print(f"Created destination directory: {destination_folder}")

    # Convert our file list to lowercase for case-insensitive matching
    # Stripping whitespace just in case there are extra spaces
    files_to_find = {name.strip().lower() for name in file_list}

    copied_count = 0

    print("Searching and copying files... Please wait.")

    # 2. Walk through the search folder recursively
    for root, dirs, files in os.walk(search_folder):
        for file in files:
            if file.lower() in files_to_find:
                source_path = os.path.join(root, file)
                dest_path = os.path.join(destination_folder, file)

                # 3. Handle potential duplicate filenames in different subfolders
                if os.path.exists(dest_path):
                    # Append a number to the file name if it already exists in the destination
                    name, ext = os.path.splitext(file)
                    counter = 1
                    while os.path.exists(dest_path):
                        dest_path = os.path.join(
                            destination_folder, f"{name}_{counter}{ext}"
                        )
                        counter += 1

                # 4. Copy the file
                try:
                    shutil.copy2(source_path, dest_path)
                    print(f"Copied: {file} -> {dest_path}")
                    copied_count += 1
                except Exception as e:
                    print(f"Failed to copy {file}. Error: {e}")

    print(f"\nDone! Successfully copied {copied_count} files.")


if __name__ == "__main__":
    # --- Configuration ---
    # # 1. List of filenames you want to search for
    # my_files = ["2025_T Gonschorek_FSWMSAAPFA.pdf"]
    # # "2025_W Doan_A(ACASTCRCASP.pdf", 
    # # "Towards Certification of a Reduced Footprint ACAS-Xu System A Hybrid ML-Based Solution.pdf", 
    # # "A Taxonomy of Software Defect Forms for Certification Tests in Aviation Industry.pdf"]

    # # 2. The main directory you want to look inside (searches this and all subfolders)
    # search_dir = '/remotedata/U/DLR+kata_du/ALR DATA' 

    # # 3. Where you want to copy the found files to
    # dest_dir = "/remotedata/U/DLR+kata_du/ALR DATA/MBSE_MBSA_Aviation/Certification specific/Pdfs"

    # # Run the function
    # copy_matching_files(my_files, search_dir, dest_dir)

    # --- Configuration ---
    # Replace these with your actual paths
    input_excel = "/remotedata/U/DLR+kata_du/ALR DATA/MBSE_MBSA_Aviation/Certification specific/Pdf_collection_list.xlsx"  # Path to your original Excel file
    search_dir = "/remotedata/U/DLR+kata_du/ALR DATA/MBSE_MBSA_Aviation/Certification specific"  # The main folder you want to look inside
    output_excel = "/remotedata/U/DLR+kata_du/ALR DATA/MBSE_MBSA_Aviation/Certification specific/Collection_updated_list.xlsx"  # Where you want to save the results

    # Run the function
    check_files_in_directory(input_excel, search_dir, output_excel)
import os
import shutil
from pathlib import Path
import pandas as pd


def copy_files_from_excel(
    excel_path: str,
    column_name: str,
    search_dir: str,
    dest_dir: str,
    log_file_path: str = "copy_log.csv",
):
    """Searches for files listed in an Excel column, copies them to a destination folder,

    logs progress to the terminal, and exports a CSV summary log.
    """
    print(f"Reading Excel file: {excel_path}...")
    df = pd.read_excel(excel_path)
    if column_name not in df.columns:
        raise ValueError(
            f"Column '{column_name}' not found in the Excel file."
        )

    # Extract target file names, dropping any blank/null values
    target_files = df[column_name].dropna().astype(str).str.strip().tolist()
    total_targets = len(target_files)
    print(
        f"Found {total_targets} target file name(s) in column '{column_name}'."
    )

    print(
        f"\nIndexing files in search directory (including subfolders): {search_dir}..."
    )
    search_path = Path(search_dir)
    file_map = {}

    for file in search_path.rglob("*"):
        if file.is_file():
            # Maps file name to its path
            file_map[file.name] = file

    print(f"Indexed {len(file_map)} total file(s) in the search root.\n")

    # Create destination directory if it doesn't exist
    dest_path = Path(dest_dir)
    dest_path.mkdir(parents=True, exist_ok=True)

    logs = []
    success_count = 0
    fail_count = 0

    print("--- Starting File Copy Operations ---")
    for idx, file_name in enumerate(target_files, start=1):
        status = "Failed"
        original_path = "N/A"
        copied_destination = "N/A"
        error_message = ""

        if file_name in file_map:
            src_file = file_map[file_name]
            original_path = str(src_file)
            target_dest = dest_path / file_name

            try:
                shutil.copy2(src_file, target_dest)
                status = "Success"
                copied_destination = str(target_dest)
                success_count += 1
                print(f"[{idx}/{total_targets}] [SUCCESS] Copied: {file_name}")
            except Exception as e:
                status = "Failed"
                error_message = str(e)
                fail_count += 1
                print(
                    f"[{idx}/{total_targets}] [FAILED] Error copying {file_name}: {e}"
                )
        else:
            error_message = "File not found in search directory"
            fail_count += 1
            print(
                f"[{idx}/{total_targets}] [FAILED] Not Found: {file_name}"
            )

        logs.append(
            {
                "Search Target": file_name,
                "Status": status,
                "Original Path": original_path,
                "Destination Path": copied_destination,
                "Error/Notes": error_message,
            }
        )

    # Export log to CSV
    log_df = pd.DataFrame(logs)
    log_df.to_csv(log_file_path, index=False)

    print("\n--- Summary ---")
    print(f"Total Processed : {total_targets}")
    print(f"Successfully Copied: {success_count}")
    print(f"Failed / Not Found : {fail_count}")
    print(f"Detailed log saved to: {log_file_path}")


# Example usage:
copy_files_from_excel(
    excel_path=r"U:\ALR DATA\Working Analysis\Phd_ppt_data_collection\334_2020-26_Files_list.xlsx",
    column_name="filename",
    search_dir=r"U:\ALR DATA",
    dest_dir=r"C:\Users\kata_du\Documents\Documents to share\Phd_contet",
    log_file_path=r"C:\Users\kata_du\Documents\Documents to share\Phd_contet\transfer_log.csv"
)

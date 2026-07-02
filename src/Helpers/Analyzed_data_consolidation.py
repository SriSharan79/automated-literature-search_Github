import os
import re
from datetime import datetime
from pathlib import Path
import pandas as pd


def merge_step_1_or_logic(df_log, df_meta):
    """Merges download_log and publications_metadata if EITHER

    Publication Name OR File_Name matches. Remaining unmatched rows are
    appended.
    """
    if df_log.empty:
        return df_meta
    if df_meta.empty:
        return df_log

    # Ensure crucial join columns exist in both dataframes to prevent KeyErrors
    for col in ["Publication Name", "File_Name"]:
        if col not in df_log.columns:
            df_log[col] = pd.NA
        if col not in df_meta.columns:
            df_meta[col] = pd.NA

    meta_used_indices = set()
    merged_rows = []

    for _, log_row in df_log.iterrows():
        log_pub = log_row["Publication Name"]
        log_file = log_row["File_Name"]

        match_idx = None
        for idx, meta_row in df_meta.iterrows():
            if idx in meta_used_indices:
                continue

            file_match = (
                pd.notna(log_file)
                and log_file != ""
                and str(log_file).strip().lower()
                == str(meta_row.get("File_Name")).strip().lower()
            )
            pub_match = (
                pd.notna(log_pub)
                and log_pub != ""
                and str(log_pub).strip().lower()
                == str(meta_row.get("Publication Name")).strip().lower()
            )

            if file_match or pub_match:
                match_idx = idx
                break

        if match_idx is not None:
            combined_row = meta_row.combine_first(log_row)
            merged_rows.append(combined_row)
            meta_used_indices.add(match_idx)
        else:
            merged_rows.append(log_row)

    df_combined = pd.DataFrame(merged_rows)

    df_unmatched_meta = df_meta.drop(index=list(meta_used_indices))
    if not df_unmatched_meta.empty:
        df_combined = pd.concat(
            [df_combined, df_unmatched_meta], axis=0, ignore_index=True
        )

    return df_combined


def consolidate_unique_publications(df):
    """Ensures 'Publication Name' entries are completely unique by merging rows

    where the publication name matches, case-insensitively.
    """
    if df.empty or "Publication Name" not in df.columns:
        return df

    # Create a temporary column with lowercase, stripped strings for grouping
    df["_normalized_pub_name"] = (
        df["Publication Name"].astype(str).str.strip().str.lower()
    )

    # Group by the normalized name and combine data across duplicate rows
    # first() keeps the first non-null value for each column in the group
    df_unique = df.groupby("_normalized_pub_name", as_index=False).first()

    # Drop the temporary column used for grouping
    df_unique = df_unique.drop(columns=["_normalized_pub_name"])

    return df_unique


def consolidate_subfolder_data(subfolder_path):
    """Consolidates the specific Excel files found within a single subfolder."""
    excel_files = list(subfolder_path.glob("*.xlsx"))

    download_log_path = None
    pub_metadata_path = None
    file_registry_path = None
    title_assess_path = None

    for file in excel_files:
        name = file.name
        if name.endswith("_download_log.xlsx"):
            download_log_path = file
        elif name.endswith("publications_metadata.xlsx"):
            pub_metadata_path = file
        elif name.endswith("Processed_file_registry.xlsx"):
            file_registry_path = file
        elif name.endswith("Title_Assessment.xlsx"):
            title_assess_path = file

    if not any(
        [
            download_log_path,
            pub_metadata_path,
            file_registry_path,
            title_assess_path,
        ]
    ):
        print(f"No matching files found in: {subfolder_path}")
        return None

    # --- Step 1: Initialize / Load Log & Metadata with OR logic ---
    df_log = (
        pd.read_excel(download_log_path)
        if download_log_path
        else pd.DataFrame()
    )
    df_meta = (
        pd.read_excel(pub_metadata_path)
        if pub_metadata_path
        else pd.DataFrame()
    )

    df_main = merge_step_1_or_logic(df_log, df_meta)

    # --- Step 2: Merge Processed_file_registry.xlsx ---
    if file_registry_path:
        df_registry = pd.read_excel(file_registry_path)
        if "filename" in df_registry.columns:
            df_registry = df_registry.rename(columns={"filename": "File_Name"})

        if not df_main.empty and "File_Name" in df_main.columns:
            df_main = pd.merge(df_main, df_registry, on="File_Name", how="outer")
        else:
            df_main = (
                pd.concat([df_main, df_registry], axis=0, ignore_index=True)
                if not df_main.empty
                else df_registry
            )

    # --- Step 3: Merge Title_Assessment.xlsx ---
    if title_assess_path:
        df_title = pd.read_excel(title_assess_path)
        if not df_main.empty and "Publication Name" in df_main.columns:
            df_main = pd.merge(
                df_main, df_title, on="Publication Name", 
                how="outer"
            )
        else:
            df_main = (
                pd.concat([df_main, df_title], axis=0, ignore_index=True)
                if not df_main.empty
                else df_title
            )

    # --- NEW STEP: Final Case-Insensitive Unique Consolidation ---
    df_main = consolidate_unique_publications(df_main)

    # --- Step 4: Save Subfolder Results ---
    today_str = datetime.today().strftime("%Y-%m-%d")
    output_filename = f"{today_str}_overview.xlsx"
    output_path = subfolder_path / output_filename

    df_main = df_main.fillna("")
    df_main.to_excel(output_path, index=False)
    print(f"Successfully created subfolder overview: {output_path}")

    return df_main


def main(root_directory):
    root_path = Path(root_directory)
    all_dfs = []

    for subfolder in root_path.iterdir():
        if subfolder.is_dir():
            print(f"Processing folder: {subfolder.name}...")
            df_sub = consolidate_subfolder_data(subfolder)
            if df_sub is not None and not df_sub.empty:
                all_dfs.append(df_sub)

    if all_dfs:
        print("\nCombining all subfolder data into master file...")
        df_master = pd.concat(all_dfs, axis=0, ignore_index=True, sort=False)

        # Re-run uniqueness consolidation on the entire master overview to ensure absolute uniqueness globally
        df_master = consolidate_unique_publications(df_master)
        df_master = df_master.fillna("")

        today_str = datetime.today().strftime("%Y-%m-%d")
        master_output_path = root_path / f"{today_str}_master_overview.xlsx"
        df_master.to_excel(master_output_path, index=False)
        print(f"Successfully created master file: {master_output_path}")
    else:
        print("No data found across any subfolders to create a master file.")




if __name__ == "__main__":
    # REPLACE THIS with your actual root directory path
    TARGET_DIRECTORY = "/remotedata/U/DLR+kata_du/ALR DATA"
    main(TARGET_DIRECTORY)

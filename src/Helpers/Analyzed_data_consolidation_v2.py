import os
import re
from datetime import datetime
from pathlib import Path
import pandas as pd
from colorama import init, Fore, Style

# Initialize colorama for cross-platform colored terminal text
init(autoreset=True)


def normalize_series(series: pd.Series) -> pd.Series:
    """Helper to consistently normalize string columns for merging keys."""
    return series.astype(str).str.strip().str.lower()


def clean_suffixed_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Dynamically finds and combines columns duplicated by merges (e.g., col_reg, col_title).
    
    Uses combine_first to ensure missing data in the base column is filled from the
    suffixed columns before dropping them.
    """
    if df.empty:
        return df

    suffixes = ["_meta", "_reg", "_title"]
    cols_to_drop = []

    for col in df.columns:
        for suffix in suffixes:
            if col.endswith(suffix):
                base_col = col[:-len(suffix)]
                if base_col in df.columns:
                    df[base_col] = df[base_col].combine_first(df[col])
                    cols_to_drop.append(col)
                else:
                    df = df.rename(columns={col: base_col})
                break

    if cols_to_drop:
        df = df.drop(columns=cols_to_drop)
        
    return df


def safe_aggregate(group):
    """Aggregates duplicate rows safely. 
    
    If data is identical, it keeps it. If data differs, it concatenates 
    the unique values with a semicolon rather than discarding information.
    """
    agg_res = {}
    for col in group.columns:
        # Special handling for folder tracking columns: if 'Yes' is anywhere, keep 'Yes'
        if str(col).startswith("Folder_"):
            valid_folder_vals = group[col].dropna().astype(str).str.strip()
            if any(valid_folder_vals.str.lower() == "yes"):
                agg_res[col] = "Yes"
                continue

        # Normal processing for other columns
        valid_vals = group[col].dropna().astype(str).str.strip()
        valid_vals = valid_vals[valid_vals != ""]
        unique_vals = valid_vals.unique()
        
        if len(unique_vals) == 0:
            agg_res[col] = pd.NA
        elif len(unique_vals) == 1:
            agg_res[col] = unique_vals[0]
        else:
            agg_res[col] = " ; ".join(unique_vals)
    return pd.Series(agg_res)


def consolidate_unique_publications(df):
    """Ensures 'Publication Name' entries are unique across the dataframe without
    losing conflicting row data. Rows with missing or blank publication names are
    preserved and NOT consolidated together.
    """
    if df.empty or "Publication Name" not in df.columns:
        return df

    # 1. Clean overlapping merge columns first
    df = clean_suffixed_columns(df).reset_index(drop=True)

    # 2. Build explicit boolean masks ensuring identical indexing
    is_blank = (
        df["Publication Name"].isna() | 
        (df["Publication Name"].astype(str).str.strip() == "")
    )

    # 3. Separate rows cleanly using the reliable boolean series
    df_blanks = df.loc[is_blank].copy()
    df_valid = df.loc[~is_blank].copy()

    if df_valid.empty:
        return df_blanks.reset_index(drop=True)

    # 4. Group valid names safely
    df_valid["_norm_pub"] = normalize_series(df_valid["Publication Name"])

    df_unique = (
        df_valid.groupby("_norm_pub", group_keys=False)
        .apply(safe_aggregate, include_groups=False)
    ).reset_index(drop=True)
    
    if "_norm_pub" in df_unique.columns:
        df_unique = df_unique.drop(columns=["_norm_pub"])
        
    # 5. Bring the separate blank entries back home untouched
    df_final = pd.concat([df_unique, df_blanks], axis=0, ignore_index=True, sort=False)
    return df_final


def merge_step_1_or_logic(df_log, df_meta):
    """Merges download_log and publications_metadata on EITHER File_Name
    OR Publication Name using robust vectorized pandas operations.
    """
    if df_log.empty:
        return df_meta.copy()
    if df_meta.empty:
        return df_log.copy()

    df_log = df_log.copy()
    df_meta = df_meta.copy()

    if "File_Name" in df_log.columns:
        df_log["_key_file"] = normalize_series(df_log["File_Name"])
    if "File_Name" in df_meta.columns:
        df_meta["_key_file"] = normalize_series(df_meta["File_Name"])
    if "Publication Name" in df_log.columns:
        df_log["_key_pub"] = normalize_series(df_log["Publication Name"])
    if "Publication Name" in df_meta.columns:
        df_meta["_key_pub"] = normalize_series(df_meta["Publication Name"])

    merge_file = pd.merge(
        df_log, df_meta, 
        on="_key_file", how="outer", suffixes=('', '_meta')
    )

    merge_pub = pd.merge(
        df_log, df_meta, 
        on="_key_pub", how="outer", suffixes=('', '_meta')
    )

    df_combined = pd.concat([merge_file, merge_pub], axis=0, ignore_index=True)
    
    drop_cols = [c for c in ["_key_file", "_key_pub"] if c in df_combined.columns]
    df_combined = df_combined.drop(columns=drop_cols)

    df_combined = clean_suffixed_columns(df_combined)

    return consolidate_unique_publications(df_combined)


def consolidate_subfolder_data(subfolder_path):
    """Consolidates specific Excel files within a subfolder, requiring
    Processed_file_registry.xlsx and at least one other target file.
    """
    excel_files = list(subfolder_path.glob("*.xlsx"))

    download_log_path = None
    pub_metadata_path = None
    file_registry_path = None
    title_assess_path = None

    found_files = []
    identified_count = 0

    for file in excel_files:
        if file.name.endswith("Processed_file_registry.xlsx"):
            file_registry_path = file
            found_files.append(f"{Fore.LIGHTCYAN_EX}File Registry: {Style.DIM}{file.name}")
            identified_count += 1
            break

    if not file_registry_path:
        print(f"  {Fore.RED}⏩ Skipped: 'Processed_file_registry.xlsx' not found in {Fore.YELLOW}{subfolder_path.name}")
        return None

    for file in excel_files:
        name = file.name
        if name.endswith("_download_log.xlsx"):
            download_log_path = file
            found_files.append(f"{Fore.LIGHTBLUE_EX}Download Log: {Style.DIM}{name}")
            identified_count += 1
        elif name.endswith("publications_metadata.xlsx"):
            pub_metadata_path = file
            found_files.append(f"{Fore.LIGHTMAGENTA_EX}Pub Metadata: {Style.DIM}{name}")
            identified_count += 1
        elif name.endswith("Title_Assessment.xlsx"):
            title_assess_path = file
            found_files.append(f"{Fore.LIGHTYELLOW_EX}Title Assess:  {Style.DIM}{name}")
            identified_count += 1

    if identified_count < 2:
        print(f"  {Fore.YELLOW}⚠️  Skipped: Only found File Registry. Minimum 2 target files required in {Fore.YELLOW}{subfolder_path.name}")
        return None

    print(f"  {Fore.WHITE}🔍 Identified files ({identified_count}/4):")
    for file_log in found_files:
        print(f"    🔬 {file_log}")

    # --- Step 1: Initialize / Load Log & Metadata with OR logic ---
    df_log = pd.read_excel(download_log_path) if download_log_path else pd.DataFrame()
    df_meta = pd.read_excel(pub_metadata_path) if pub_metadata_path else pd.DataFrame()

    df_main = merge_step_1_or_logic(df_log, df_meta)

    # --- Step 2: Merge Processed_file_registry.xlsx ---
    df_registry = pd.read_excel(file_registry_path)
    if "filename" in df_registry.columns:
        df_registry = df_registry.rename(columns={"filename": "File_Name"})
    
    if not df_main.empty and "File_Name" in df_main.columns:
        df_main["_key_file"] = normalize_series(df_main["File_Name"])
        df_registry["_key_file"] = normalize_series(df_registry["File_Name"])
        df_main = pd.merge(df_main, df_registry, on="_key_file", how="outer", suffixes=('', '_reg'))
        df_main = df_main.drop(columns=["_key_file"])
    else:
        df_main = pd.concat([df_main, df_registry], axis=0, ignore_index=True)

    df_main = clean_suffixed_columns(df_main)

    # --- Step 3: Merge Title_Assessment.xlsx ---
    if title_assess_path:
        df_title = pd.read_excel(title_assess_path)
        if not df_main.empty and "Publication Name" in df_main.columns:
            df_main["_key_pub"] = normalize_series(df_main["Publication Name"])
            df_title["_key_pub"] = normalize_series(df_title["Publication Name"])
            df_main = pd.merge(df_main, df_title, on="_key_pub", how="outer", suffixes=('', '_title'))
            df_main = df_main.drop(columns=["_key_pub"])
        else:
            df_main = pd.concat([df_main, df_title], axis=0, ignore_index=True)

    # Clean intermediate structural duplicates
    df_main = clean_suffixed_columns(df_main)
    df_main = consolidate_unique_publications(df_main)

    # --- Add Subfolder Source Tracking Columns ---
    if not df_main.empty:
        folder_col_name = f"Folder_{subfolder_path.name}"
        df_main[folder_col_name] = "Yes"

    # --- Step 4: Save Subfolder Results ---
    today_str = datetime.today().strftime("%Y-%m-%d")
    output_filename = f"{today_str}_overview.xlsx"
    output_path = subfolder_path / output_filename

    df_output = df_main.fillna("")
    df_output.to_excel(output_path, index=False)
    print(f"  {Fore.GREEN}✅ Created subfolder overview: {Fore.CYAN}{output_path.name}")

    return df_main


def main(root_directory):
    root_path = Path(root_directory)
    if not root_path.exists():
        print(f"{Fore.RED}❌ Error: Target Directory '{root_directory}' does not exist.")
        return

    all_dfs = []
    print(f"{Fore.BLUE}{Style.BRIGHT}🚀 Starting extraction and consolidation loop in: {root_path}")

    for subfolder in root_path.iterdir():
        if subfolder.is_dir():
            print(f"\n{Fore.WHITE}📁 Processing folder: {Fore.YELLOW}{subfolder.name}...")
            df_sub = consolidate_subfolder_data(subfolder)
            if df_sub is not None and not df_sub.empty:
                all_dfs.append(df_sub)

    if all_dfs:
        print(f"\n{Fore.BLUE}{Style.BRIGHT}🔄 Combining all subfolder structures into master file...")
        df_master = pd.concat(all_dfs, axis=0, ignore_index=True, sort=False)

        # Global uniqueness filter sequence execution and dynamic column merging
        df_master = clean_suffixed_columns(df_master)
        df_master = consolidate_unique_publications(df_master)
        
        today_str = datetime.today().strftime("%Y-%m-%d")
        master_output_path = root_path / f"{today_str}_master_overview2.xlsx"
        
        # Cleanly show blank cells for folder track columns where a publication isn't present
        df_master_final = df_master.fillna("")
        df_master_final.to_excel(master_output_path, index=False)
        print(f"\n{Fore.GREEN}{Style.BRIGHT}🎉 SUCCESS: Created global master overview: {Fore.CYAN}{master_output_path}")
    else:
        print(f"\n{Fore.RED}❌ Error: No valid data found across any subfolders to construct a master file.")


if __name__ == "__main__":
    TARGET_DIRECTORY = "/remotedata/U/DLR+kata_du/ALR DATA"
    main(TARGET_DIRECTORY)
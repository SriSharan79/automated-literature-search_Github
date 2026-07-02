import os
from datetime import datetime
from pathlib import Path
import pandas as pd
from colorama import init, Fore, Style

# Initialize colorama for clean terminal feedback
init(autoreset=True)


def normalize_series(series: pd.Series) -> pd.Series:
    """Consistently normalizes string columns for row merging keys."""
    return series.astype(str).str.strip().str.lower()


def clean_suffixed_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Dynamically finds and combines columns duplicated by structural merges.
    
    Uses combine_first to rescue missing data before dropping redundant columns.
    """
    if df.empty:
        return df

    suffixes = ["_reg", "_dup", "_x", "_y"]
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
    """Aggregates duplicate rows without data loss.
    
    Identical data is kept. Conflicting data is concatenated with a semicolon.
    """
    agg_res = {}
    for col in group.columns:
        # Pass folder markers directly if 'Yes' exists anywhere in the group
        if str(col).startswith("Source_Folder_"):
            valid_folder = group[col].dropna().astype(str).str.strip()
            if any(valid_folder.str.lower() == "yes"):
                agg_res[col] = "Yes"
                continue

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


def consolidate_by_filename(df: pd.DataFrame) -> pd.DataFrame:
    """Ensures rows are consolidated uniquely by File_Name.
    
    Rows missing a filename are preserved untouched.
    """
    if df.empty or "File_Name" not in df.columns:
        return df

    df = clean_suffixed_columns(df).reset_index(drop=True)

    # Separate rows with valid filenames from missing/blank ones
    is_blank = (
        df["File_Name"].isna() | 
        (df["File_Name"].astype(str).str.strip() == "")
    )
    df_blanks = df.loc[is_blank].copy()
    df_valid = df.loc[~is_blank].copy()

    if df_valid.empty:
        return df_blanks.reset_index(drop=True)

    # Normalize filenames for grouping matches
    df_valid["_norm_file"] = normalize_series(df_valid["File_Name"])

    df_unique = (
        df_valid.groupby("_norm_file", group_keys=False)
        .apply(safe_aggregate, include_groups=False)
    ).reset_index(drop=True)
    
    if "_norm_file" in df_unique.columns:
        df_unique = df_unique.drop(columns=["_norm_file"])
        
    return pd.concat([df_unique, df_blanks], axis=0, ignore_index=True, sort=False)


def consolidate_registries(root_directory: str):
    root_path = Path(root_directory)
    if not root_path.exists():
        print(f"{Fore.RED}❌ Error: Root directory '{root_directory}' does not exist.")
        return

    print(f"{Fore.BLUE}{Style.BRIGHT}🔍 Searching recursively for 'Processed_file_registry' files in: {root_path}")
    
    # Locate all matching registry files anywhere down the directory tree
    registry_files = list(root_path.rglob("*Processed_file_registry.xlsx"))
    
    if not registry_files:
        print(f"{Fore.YELLOW}⚠️  No matching 'Processed_file_registry.xlsx' files found.")
        return

    print(f"{Fore.GREEN}Found {len(registry_files)} registry files. Compiling data...\n")
    all_dfs = []

    for file_path in registry_files:
        # Track relative folder location for column flag mapping
        relative_folder = file_path.parent.name
        print(f"  {Fore.LIGHTCYAN_EX}Reading registry from folder: {Style.DIM}{relative_folder}")
        
        try:
            df = pd.read_excel(file_path)
            if df.empty:
                continue
                
            # Standardize filename columns if lowercased in the source file
            if "filename" in df.columns:
                df = df.rename(columns={"filename": "File_Name"})
                
            # Clean duplicate structural columns inside individual file pull
            df = clean_suffixed_columns(df)
            
            # Map tracking column flag for this subfolder location
            folder_flag_col = f"Source_Folder_{relative_folder}"
            df[folder_flag_col] = "Yes"
            
            all_dfs.append(df)
        except Exception as e:
            print(f"  {Fore.RED}❌ Failed to process file {file_path.name}: {e}")

    if all_dfs:
        print(f"\n{Fore.BLUE}{Style.BRIGHT}🔄 Merging structures into final master file...")
        df_master = pd.concat(all_dfs, axis=0, ignore_index=True, sort=False)
        
        # Consolidate globally by File_Name ensuring no duplicate columns or overlapping data blocks
        df_master = clean_suffixed_columns(df_master)
        df_master = consolidate_by_filename(df_master)
        
        # Output generation
        today_str = datetime.today().strftime("%Y-%m-%d")
        output_path = root_path / f"{today_str}_master_registries_consolidated.xlsx"
        
        df_master_final = df_master.fillna("")
        df_master_final.to_excel(output_path, index=False)
        print(f"\n{Fore.GREEN}{Style.BRIGHT}🎉 SUCCESS: Master registry file saved here:\n👉 {Fore.CYAN}{output_path}")
    else:
        print(f"\n{Fore.RED}❌ Data consolidation halted: No valid structures retrieved.")


if __name__ == "__main__":
    TARGET_DIRECTORY = "/remotedata/U/DLR+kata_du/ALR DATA"
    consolidate_registries(TARGET_DIRECTORY)

import shutil
from pathlib import Path
import pandas as pd

from alr.common.file_manager import DataAnalyzeManager


# Assuming DataAnalyzeManager is imported or defined above
# Assuming DataAnalyzeManager is already defined/imported above

def transfer_abstract_files2(excel_path, source_base_folder, target_base_folder, uuid_column="UUID"):
    """
    Reads UUIDs from an Excel file and transfers ALL JSON files and storage assets 
    associated with those UUIDs from the source to the target directory.
    """
    # 1. Read UUIDs from the Excel registry
    print(f"Reading UUIDs from: {excel_path}")
    df = pd.read_excel(excel_path)
    
    if uuid_column not in df.columns:
        raise ValueError(f"Column '{uuid_column}' not found. Available columns: {list(df.columns)}")
        
    uuids = df[uuid_column].dropna().astype(str).unique()
    print(f"Found {len(uuids)} unique UUIDs to process.\n")
    
    # 2. Initialize Managers for Source and Target
    source_manager = DataAnalyzeManager(folder_path=source_base_folder)
    target_manager = DataAnalyzeManager(folder_path=target_base_folder)
    
    files_transferred = 0
    folders_transferred = 0
    
    # 3. Process every UUID
    for doc_id in uuids:
        print(f"Processing transfer for ID: {doc_id}")
        
        # Point both managers to the current UUID files
        source_manager.update_id_files(doc_id)
        target_manager.update_id_files(doc_id)
        
        # Target paths are automatically prepared by target_manager.update_id_files()
        
        # --- Collect all single JSON & Log file paths for this UUID ---
        target_file_mappings = [
            (source_manager.raw_sec_json_path, target_manager.raw_sec_json_path),
            (source_manager.raw_chunks_json_path, target_manager.raw_chunks_json_path),
            # (source_manager.file_usage_log_path, target_manager.file_usage_log_path),
            # (source_manager.ref_json_path, target_manager.ref_json_path),
            (source_manager.abstract_json_path, target_manager.abstract_json_path),
            (source_manager.intro_json_path, target_manager.intro_json_path),
        ]
        
        # Transfer individual files
        for src_f, tgt_f in target_file_mappings:
            if src_f and Path(src_f).exists():
                # Re-verify parent directory existence just in case
                Path(tgt_f).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_f, tgt_f)
                files_transferred += 1
                
        # --- Collect all Asset Subdirectories for this UUID ---
        target_dir_mappings = [
            (source_manager.tables_storage_path, target_manager.tables_storage_path),
            (source_manager.image_storage_path, target_manager.image_storage_path)
        ]
        
        # Transfer directories (Tables and Images)
        for src_d, tgt_d in target_dir_mappings:
            src_dir_path = Path(src_d)
            tgt_dir_path = Path(tgt_d)
            
            if src_dir_path.exists() and src_dir_path.is_dir():
                # Clean merge: copy all items inside the directory to target directory
                for item in src_dir_path.iterdir():
                    if item.is_file():
                        shutil.copy2(item, tgt_dir_path / item.name)
                        files_transferred += 1
                folders_transferred += 1

    print("\n" + "="*40)
    print("Comprehensive Transfer Complete Summary:")
    print(f" Total subfolders processed: {folders_transferred}")
    print(f" Total files copied:         {files_transferred}")
    print("="*40)

def transfer_abstract_files(
    excel_path, source_base_folder, target_base_folder, uuid_column="UUID"
):
    """Reads UUIDs from an Excel file and transfers matching Abstract JSON files

    from a source storage structure to a target storage structure.
    """
    # 1. Read UUIDs from the given Excel sheet
    print(f"Reading UUIDs from: {excel_path}")
    df = pd.read_excel(excel_path)

    if uuid_column not in df.columns:
        raise ValueError(
            f"Column '{uuid_column}' not found in the Excel sheet. Available columns: {list(df.columns)}"
        )

    # Clean and extract unique UUIDs (dropping any NaN values)
    uuids = df[uuid_column].dropna().astype(str).unique()
    print(f"Found {len(uuids)} unique UUIDs to process.\n")

    # 2. Initialize the Manager for both Source and Target to mirror structure
    source_manager = DataAnalyzeManager(folder_path=source_base_folder)
    target_manager = DataAnalyzeManager(folder_path=target_base_folder)

    success_count = 0
    missing_count = 0

    # 3. Process and transfer files loop
    for doc_id in uuids:
        # Update managers to point to the correct files for this specific ID
        source_manager.update_id_files(doc_id)
        target_manager.update_id_files(doc_id)

        # Get abstract JSON paths (converting string paths to Path objects safely)
        src_abstract_file = Path(source_manager.abstract_json_path)
        tgt_abstract_file = Path(target_manager.abstract_json_path)

        # Check if the source file exists before attempting transfer
        if src_abstract_file.exists():
            # shutil.copy2 preserves original metadata (timestamps, etc.)
            shutil.copy2(src_abstract_file, tgt_abstract_file)
            print(f" -> Transferred: {src_abstract_file.name}")
            success_count += 1
        else:
            print(f" [!] Missing: {src_abstract_file.name} not found in source.")
            missing_count += 1

    # Final summary log
    print("\n" + "=" * 40)
    print("Transfer Process Complete Summary:")
    print(f" Successfully transferred: {success_count} files")
    print(f" Missing in source:         {missing_count} files")
    print("=" * 40)


# --- Execution Example ---
if __name__ == "__main__":
    # Define your paths here
    EXCEL_REGISTRY_PATH = "/remotedata/U/DLR+kata_du/ALR DATA/Only_MBSA/Working/Processed_UUIDS.xlsx"
    SOURCE_DIR = "/remotedata/U/DLR+kata_du/ALR DATA/Only_MBSA/Only_MBSA_results"
    TARGET_DIR = "/remotedata/U/DLR+kata_du/ALR DATA/Only_MBSA/Working/Specific_Lit_review2"

    # Run the function (adjust 'uuid_column' name if your Excel header is different)
    transfer_abstract_files2(
        excel_path=EXCEL_REGISTRY_PATH,
        source_base_folder=SOURCE_DIR,
        target_base_folder=TARGET_DIR,
        uuid_column="UUID",  # Replace with actual header name if different (e.g., 'doc_id')
    )
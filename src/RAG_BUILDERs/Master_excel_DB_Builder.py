import sys
sys.path.extend([
    r'src',
    r'src/COLLECTION',
    r'Working_Code',
    r'src/DATA_ANALYSIS',
    r'src/COMMON',
    r'src/Command_Line_UI'
])

from pathlib import Path
from colorama import Fore
import pandas as pd
from COMMON.File_Manager import DataAnalyzeManager, Vec_DB_Manager
from COMMON.Excel_Utils import extract_column, get_corresponding_value
import json
import os

def save_to_db(master_excel_path, sheet_name, json_path, data_entry):
    """
    Appends data to a specific sheet inside a single Master Excel file,
    updates a master 'Overview' sheet at the 1st position with section columns,
    and appends to a JSON list.
    """
    target_uuid = str(data_entry.get("UUID"))
    original_uuid = str(data_entry.get("Original_UUID", target_uuid))
    content_value = data_entry.get("Content", "")
    title = data_entry.get("Title", "")
    filename = data_entry.get("Filename", "")
    
    master_excel_path = Path(master_excel_path)
    
    # --- Check for Duplicates in the Specific Section Sheet ---
    skip_excel = False
    if master_excel_path.exists() and master_excel_path.stat().st_size > 0:
        try:
            with pd.ExcelFile(master_excel_path, engine='openpyxl') as xls:
                if sheet_name in xls.sheet_names:
                    df_check = pd.read_excel(master_excel_path, sheet_name=sheet_name, engine='openpyxl')
                    if not df_check.empty and "UUID" in df_check.columns:
                        if target_uuid in df_check["UUID"].astype(str).values:
                            skip_excel = True
        except Exception as e:
            print(Fore.YELLOW + f"⚠️ Excel sheet '{sheet_name}' read error (will attempt overwrite): {e}")

    # --- Check for Duplicates in JSON ---
    skip_json = False
    existing_json_data = []
    if json_path.exists() and json_path.stat().st_size > 0:
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                existing_json_data = json.load(f)
                if any(str(item.get("UUID")) == target_uuid for item in existing_json_data):
                    skip_json = True
        except (json.JSONDecodeError, FileNotFoundError):
            existing_json_data = []

    if skip_excel and skip_json:
        return # Entry already fully registered

    # --- Save to Excel Workbooks ---
    if not skip_excel:
        try:
            # 1. Update/Create the Individual Section Sheet
            df_new = pd.DataFrame([data_entry])
            all_sheets_data = {}
            sheet_order = []

            if master_excel_path.exists() and master_excel_path.stat().st_size > 0:
                with pd.ExcelFile(master_excel_path, engine='openpyxl') as xls:
                    sheet_order = list(xls.sheet_names)
                    for s_name in sheet_order:
                        all_sheets_data[s_name] = pd.read_excel(master_excel_path, sheet_name=s_name, engine='openpyxl')
                
                # Append or set individual sheet data
                if sheet_name in all_sheets_data:
                    all_sheets_data[sheet_name] = pd.concat([all_sheets_data[sheet_name], df_new], ignore_index=True)
                else:
                    all_sheets_data[sheet_name] = df_new
                    sheet_order.append(sheet_name)
            else:
                all_sheets_data[sheet_name] = df_new
                sheet_order.append(sheet_name)

            # 2. Update/Create the Master "Overview" Sheet
            overview_sheet = "Overview"
            if overview_sheet in all_sheets_data:
                df_overview = all_sheets_data[overview_sheet]
            else:
                # Initialize empty Overview with metadata structure
                df_overview = pd.DataFrame(columns=["UUID", "Title", "Filename"])
            
            # Ensure UUID is treated as string for clean matching
            df_overview["UUID"] = df_overview["UUID"].astype(str)
            
            # Check if this row (Original_UUID) already has a record in Overview
            match_mask = df_overview["UUID"] == original_uuid
            
            if match_mask.any():
                # Update existing row's target section column cell
                df_overview.loc[match_mask, sheet_name] = content_value
                # Keep metadata updated if missing
                if title: df_overview.loc[match_mask, "Title"] = title
                if filename: df_overview.loc[match_mask, "Filename"] = filename
            else:
                # Create a completely fresh row entry for this asset
                new_row = {
                    "UUID": original_uuid,
                    "Title": title,
                    "Filename": filename,
                    sheet_name: content_value
                }
                df_overview = pd.concat([df_overview, pd.DataFrame([new_row])], ignore_index=True)
            
            all_sheets_data[overview_sheet] = df_overview
            
            # 3. Re-order sheets so "Overview" is strictly forced as the 1st tab (index 0)
            if overview_sheet in sheet_order:
                sheet_order.remove(overview_sheet)
            sheet_order.insert(0, overview_sheet)

            # 4. Write everything back into the Master workbook preserving sheet arrangements
            with pd.ExcelWriter(master_excel_path, engine='openpyxl', mode='w') as writer:
                for s_name in sheet_order:
                    all_sheets_data[s_name].to_excel(writer, sheet_name=s_name, index=False)
                    
        except Exception as e:
            print(Fore.RED + f"❌ Failed to save Excel steps for sheet '{sheet_name}': {e}")

    # --- Save to JSON ---
    if not skip_json:
        try:
            existing_json_data.append(data_entry)
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(existing_json_data, f, indent=4)
        except Exception as e:
            print(Fore.RED + f"❌ Failed to save JSON: {e}")


# -------------------------
# Helper functions
# -------------------------

def _load_recorded_abstracts(MF):
    """Reads UUID column from abstract log; returns [] if unavailable (and prints)."""
    if MF.AD_Abstract_log_path and Path(MF.AD_Abstract_log_path).exists():
        return extract_column(MF.AD_Abstract_log_path, "UUID")

    print(Fore.RED + "Abstract Log is not Available")
    return []


def _fetch_metadata(MF, UUID):
    """Fetch title and filename from success excel for a given UUID."""
    title = get_corresponding_value(MF.excel_success, "UUID", UUID, "title")
    file_name = get_corresponding_value(MF.excel_success, "UUID", UUID, "filename")
    return title, file_name


def _load_abstract_json(MF, UUID):
    """Load abstract JSON for the current UUID; returns None on failure (and prints)."""
    if not (MF.abstract_json_path and Path(MF.abstract_json_path).exists()):
        return None

    try:
        with open(MF.abstract_json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(Fore.RED + f"Error loading {UUID}: {e}")
        return None


def _build_sections_Master_map(VDB, master_excel_path):
    """
    Maps section names directly to the Master Excel path, 
    the target Sheet Name, and its standalone JSON path.
    """
    return {
        "Research Problem": (master_excel_path, "Research_Problem", VDB.Research_problem_DB_json),
        "Objective": (master_excel_path, "Objective", VDB.Objective_DB_json),
        "Methodology": (master_excel_path, "Methodology", VDB.Methodology_json),
        "Conclusion": (master_excel_path, "Conclusion", VDB.Conclusion_DB_json),
        "Results": (master_excel_path, "Results", VDB.Results_DB_json),
        "Research Areas": (master_excel_path, "Research_Areas", VDB.Research_Areas_DB_json),
        "Key Concepts": (master_excel_path, "Key_Concepts", VDB.Key_concepts_DB_json),
    }


def _sync_sections_master_for_uuid(UUID, title, file_name, json_data, sections):
    """Iterate sections and save either list items or a single string entry."""
    for key, (ex_path, sheet_name, j_path) in sections.items():
        content_value = json_data.get(key, "Not Found")

        if isinstance(content_value, list):
            _save_list_section(
                UUID=UUID,
                key=key,
                content_list=content_value,
                ex_path=ex_path,
                sheet_name=sheet_name,
                j_path=j_path,
                title=title,
                file_name=file_name
            )
        else:
            _save_single_section(
                UUID=UUID,
                key=key,
                content_value=content_value,
                ex_path=ex_path,
                sheet_name=sheet_name,
                j_path=j_path,
                title=title,
                file_name=file_name
            )


# def _save_list_section(UUID, key, content_list, ex_path, sheet_name, j_path, title, file_name):
#     """Save list-like sections onto specific sheets."""
#     for idx, item in enumerate(content_list):
#         entry = {
#             "UUID": f"{UUID}_{idx}",
#             "Original_UUID": UUID,
#             "Title": title,
#             "Filename": file_name,
#             "Count": str(idx + 1),
#             "Content": str(item),
#         }
#         try:
#             save_to_db(ex_path, sheet_name, j_path, entry)
#         except Exception as e:
#             print(Fore.RED + f"Error saving list item in {key} for {UUID}: {e}")

#     print(Fore.GREEN + f"✅ Synced list '{key}' ({len(content_list)} items) to Sheet '{sheet_name}' for UUID: {UUID}")

def _save_list_section(UUID, key, content_list, ex_path, sheet_name, j_path, title, file_name):
    """Save list-like sections onto specific sheets as a single bulleted string."""
    if not content_list:
        print(Fore.YELLOW + f"⚠️ Content list for '{key}' is empty. Skipping save.")
        return

    # 1. Combine all items into a single string formatted with bullet points
    bulleted_content = "\n".join([f"• {str(item).strip()}" for item in content_list])

    # 2. Structure the final payload (removed "Count" and keeping the original UUID structure)
    entry = {
        "UUID": UUID,
        "Original_UUID": UUID,
        "Title": title,
        "Filename": file_name,
        "Content": bulleted_content,
    }

    # 3. Save the single consolidated entry to the database
    try:
        save_to_db(ex_path, sheet_name, j_path, entry)
        print(Fore.GREEN + f"✅ Synced list '{key}' ({len(content_list)} items consolidated) to Sheet '{sheet_name}' for UUID: {UUID}")
    except Exception as e:
        print(Fore.RED + f"Error saving list content in {key} for {UUID}: {e}")

def _save_single_section(UUID, key, content_value, ex_path, sheet_name, j_path, title, file_name):
    """Save single-string sections onto specific sheets."""
    entry = {
        "UUID": UUID,
        "Original_UUID": UUID,
        "Title": title,
        "Filename": file_name,
        "Content": content_value,
    }
    try:
        save_to_db(ex_path, sheet_name, j_path, entry)
        print(Fore.GREEN + f"✅ Successfully synchronized {key} to Sheet '{sheet_name}' for UUID: {UUID}")
    except Exception as e:
        print(Fore.RED + f"Error saving {key} for {UUID}: {e}")

if __name__ == "__main__":
    storage_path="/remotedata/U/DLR+kata_du/ALR DATA/MBSE_MBSA_Specific_literature/Analyzed_results"
    EXCEL_REGISTRY_PATH = "/remotedata/U/DLR+kata_du/ALR DATA/MBSE_MBSA_Specific_literature/Analyzed_results/Processed_file_registry.xlsx"
    
    # Initialize Managers
    MF = DataAnalyzeManager(folder_path=storage_path)
    VDB = Vec_DB_Manager(folder_path=storage_path) # Assuming initialization structure matches
    
    # Define your single master file destination path
    # MASTER_EXCEL_FILE = os.path.join(storage_path , "20260629_Overview_Database.xlsx")
    MASTER_EXCEL_FILE=VDB.Abstract_Overview
    
    # 1. Map sections tracking the single Excel file + specific sheets
    sections_map = _build_sections_Master_map(VDB, MASTER_EXCEL_FILE)
    
    # 2. Extract logged UUIDs to process
    recorded_uuids = extract_column(EXCEL_REGISTRY_PATH,"UUID")
    # recorded_uuids = _load_recorded_abstracts(MF)
    
    # 3. Execution Loop
    for uuid in recorded_uuids:
        MF.update_id_files(uuid)
        title, file_name = _fetch_metadata(MF, uuid)
        abstract_json = _load_abstract_json(MF, uuid)
        
        if abstract_json:
            _sync_sections_master_for_uuid(uuid, title, file_name, abstract_json, sections_map)
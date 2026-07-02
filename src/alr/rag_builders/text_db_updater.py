
from pathlib import Path
from colorama import Fore
import pandas as pd
from alr.common.file_manager import DataAnalyzeManager, Vec_DB_Manager
from alr.common.excel_utils import extract_column, get_corresponding_value
import json

def save_to_db(excel_path, json_path, data_entry):
    """
    Appends data to an Excel file and a JSON list with explicit engine specification.
    """
    target_uuid = str(data_entry.get("UUID"))
    
    # --- Check for Duplicates in Excel ---
    skip_excel = False
    if excel_path.exists() and excel_path.stat().st_size > 0:
        try:
            # Explicitly use 'openpyxl' engine
            df_check = pd.read_excel(excel_path, engine='openpyxl')
            if not df_check.empty and "UUID" in df_check.columns:
                if target_uuid in df_check["UUID"].astype(str).values:
                    skip_excel = True
        except Exception as e:
            print(Fore.YELLOW + f"⚠️ Excel read error (will attempt overwrite): {e}")

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

    # --- Save to Excel ---
    if not skip_excel:
        try:
            df_new = pd.DataFrame([data_entry])
            if excel_path.exists() and excel_path.stat().st_size > 0:
                df_old = pd.read_excel(excel_path, engine='openpyxl')
                df_final = pd.concat([df_old, df_new], ignore_index=True)
            else:
                df_final = df_new
            # Explicitly use 'openpyxl' engine for writing
            df_final.to_excel(excel_path, index=False, engine='openpyxl')
        except Exception as e:
            print(Fore.RED + f"❌ Failed to save Excel: {e}")

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


def _sync_sections_for_uuid(UUID, title, file_name, json_data, sections):
    """Iterate sections and save either list items or a single string entry."""
    for key, (ex_path, j_path) in sections.items():
        content_value = json_data.get(key, "Not Found")

        if isinstance(content_value, list):
            _save_list_section(
                UUID=UUID,
                key=key,
                content_list=content_value,
                ex_path=ex_path,
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
                j_path=j_path,
                title=title,
                file_name=file_name
            )


def _save_list_section(UUID, key, content_list, ex_path, j_path, title, file_name):
    """Save list-like sections (Research Areas, Key Concepts, etc.) exactly as before."""
    for idx, item in enumerate(content_list):
        entry = {
            "UUID": f"{UUID}_{idx}",
            "Original_UUID": UUID,
            "Title": title,
            "Filename": file_name,
            "Count": str(idx + 1),
            "Content": str(item),
        }
        try:
            save_to_db(ex_path, j_path, entry)
        except Exception as e:
            print(Fore.RED + f"Error saving list item in {key} for {UUID}: {e}")

    print(Fore.GREEN + f"✅ Synced list '{key}' ({len(content_list)} items) for UUID: {UUID}")


def _save_single_section(UUID, key, content_value, ex_path, j_path, title, file_name):
    """Save single-string sections exactly as before."""
    entry = {
        "UUID": UUID,
        "Original_UUID": UUID,
        "Title": title,
        "Filename": file_name,
        "Content": content_value,
    }
    try:
        save_to_db(ex_path, j_path, entry)
        print(Fore.GREEN + f"✅ Successfully synchronized {key} for UUID: {UUID}")
    except Exception as e:
        print(Fore.RED + f"Error saving {key} for {UUID}: {e}")

if __name__ == "__main__":
    storage_path='U:\ALR DATA\SLR_Process_Main\SLR_Process_results'

    

from pathlib import Path
from colorama import Fore
import pandas as pd
from alr.common.file_manager import DataAnalyzeManager, Vec_DB_Manager
from alr.common.excel_utils import extract_column, get_corresponding_value
import json
import os
# ADD:
from alr.common.sections import build_sections_master_map

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


def _load_json_file(path, UUID, label):
    """Load one analysis JSON; returns {} when absent/unreadable (and prints)."""
    if not (path and Path(path).exists()):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(Fore.RED + f"Error loading {label} for {UUID}: {e}")
        return {}


def _load_analysis_json(MF, UUID, sources):
    """
    Merge the analysis JSONs named in ``sources`` (any of "abstract" / "intro" /
    "rescon") into one ``{section_key: value}`` dict. Section keys are unique
    across the three files, so a plain merge is unambiguous and lets the existing
    per-section writer stay source-agnostic. Returns {} when nothing loads.
    """
    merged = {}
    if "abstract" in sources:
        merged.update(_load_abstract_json(MF, UUID) or {})
    if "intro" in sources:
        merged.update(_load_json_file(MF.intro_json_path, UUID, "Introduction JSON"))
    if "rescon" in sources:
        merged.update(_load_json_file(MF.rescon_json_path, UUID, "Results & Conclusion JSON"))
    return merged

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

def build_master_excel_db(storage_path, master_excel_path=None, progress_callback=None,
                          should_cancel=None, section_keys=None):
    """
    Consolidate the per-section analyzed data of a storage space into a single
    master Excel workbook (one "Overview" sheet + one sheet per section).

    ``storage_path`` is a DataAnalyzeManager storage folder. ``master_excel_path``
    defaults to the managed ``Vec_DB_Manager.Abstract_Overview`` location. UUIDs are
    read from the space's ``Processed_file_registry.xlsx``; each document's analysis
    JSONs are mapped across the section sheets via :func:`build_sections_master_map`.

    ``section_keys`` selects which analyzed attributes to write -- any mix of the
    abstract, Introduction and Results & Conclusion attributes registered in
    ``sections.ALL_RAG_SECTIONS``. It defaults to the abstract attributes, which is
    what this builder wrote before the other two analyses existed. Only the JSONs
    actually needed by the selection are read.

    ``progress_callback(done, total)`` is called after each document if given.
    ``should_cancel`` is an optional callable checked before each document for
    cooperative cancellation (partial results are preserved). Returns the number of
    documents written to the master workbook and the path of that workbook.
    """
    from alr.common.sections import RAG_SOURCE_BY_KEY

    MF = DataAnalyzeManager(folder_path=storage_path)
    VDB = Vec_DB_Manager(folder_path=storage_path)

    master_excel_path = Path(master_excel_path) if master_excel_path else Path(VDB.Abstract_Overview)

    # Map sections onto the single master file + their specific sheets.
    sections_map = build_sections_master_map(VDB, master_excel_path, only=section_keys)
    if not sections_map:
        print(Fore.RED + "⚠️ No attributes selected. Nothing to consolidate.")
        return 0, master_excel_path

    # Only load the analysis JSONs the selected attributes actually come from.
    sources = {RAG_SOURCE_BY_KEY[key] for key in sections_map if key in RAG_SOURCE_BY_KEY}

    # UUIDs to process come from the processed-file registry.
    if not Path(MF.excel_success).exists():
        print(Fore.RED + f"⚠️ No processed-file registry found at {MF.excel_success}. Nothing to consolidate.")
        return 0, master_excel_path

    recorded_uuids = extract_column(MF.excel_success, "UUID")
    total = len(recorded_uuids)
    written = 0

    for i, uuid in enumerate(recorded_uuids, 1):
        if should_cancel is not None and should_cancel():
            print(Fore.YELLOW + "Master Excel build cancelled by user.")
            break

        MF.update_id_files(uuid)
        title, file_name = _fetch_metadata(MF, uuid)
        json_data = _load_analysis_json(MF, uuid, sources)

        if json_data:
            _sync_sections_master_for_uuid(uuid, title, file_name, json_data, sections_map)
            written += 1

        if progress_callback:
            progress_callback(i, total)

    print(Fore.GREEN + f"✅ Master Excel workbook updated with {written} document(s): {master_excel_path}")
    return written, master_excel_path


if __name__ == "__main__":
    storage_path = "/remotedata/U/DLR+kata_du/ALR DATA/MBSE_MBSA_Specific_literature/Analyzed_results"
    build_master_excel_db(storage_path)
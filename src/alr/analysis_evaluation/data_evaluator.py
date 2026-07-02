import os
import sys
from pathlib import Path
import pandas as pd

from alr.common.file_manager import DataAnalyzeManager, Vec_DB_Manager
from alr.common.sections import build_sections_eval_map
from alr.rag_builders.master_excel_db_builder import _fetch_metadata, _load_abstract_json, _load_recorded_abstracts
from alr.common.json_utils import get_key_from_file, get_value_by_pair
from alr.rag_builders.vector_db_updater import add_new_strings_to_index, create_faiss_index_cosine, load_index_file, save_index_file, search_similar, vectorize_strings
from colorama import Fore, Style


# =====================================================================
# MODULAR EXCEL HELPERS
# =====================================================================

def _is_duplicate_in_sheet(file_path, sheet_name, target_uuid):
    """Checks if a UUID already exists in a given Excel sheet."""
    if file_path.exists() and file_path.stat().st_size > 0:
        try:
            with pd.ExcelFile(file_path, engine='openpyxl') as xls:
                if sheet_name in xls.sheet_names:
                    df = pd.read_excel(file_path, sheet_name=sheet_name, engine='openpyxl')
                    if not df.empty and "UUID" in df.columns:
                        if target_uuid in df["UUID"].astype(str).values:
                            return True
        except Exception as e:
            print(Fore.YELLOW + f"⚠️ Sheet '{sheet_name}' read error: {e}")
    return False


def _write_section_sheet_flat(file_path, sheet_name, data_entry):
    """Appends or updates a data entry for section files ensuring single row flat format per UUID."""
    try:
        df_new = pd.DataFrame([data_entry])
        all_sheets = {}
        sheet_order = []
        target_uuid = str(data_entry.get("UUID"))

        if file_path.exists() and file_path.stat().st_size > 0:
            with pd.ExcelFile(file_path, engine='openpyxl') as xls:
                sheet_order = list(xls.sheet_names)
                for s in sheet_order:
                    all_sheets[s] = pd.read_excel(file_path, sheet_name=s, engine='openpyxl')
            
            if sheet_name in all_sheets:
                df_sheet = all_sheets[sheet_name]
                df_sheet["UUID"] = df_sheet["UUID"].astype(str)
                match_mask = df_sheet["UUID"] == target_uuid
                
                if match_mask.any():
                    # Update row to dynamically balance any column structure changes
                    for col in df_new.columns:
                        df_sheet.loc[match_mask, col] = df_new[col].values[0]
                    all_sheets[sheet_name] = df_sheet
                else:
                    all_sheets[sheet_name] = pd.concat([df_sheet, df_new], ignore_index=True)
            else:
                all_sheets[sheet_name] = df_new
                sheet_order.append(sheet_name)
        else:
            all_sheets[sheet_name] = df_new
            sheet_order.append(sheet_name)

        with pd.ExcelWriter(file_path, engine='openpyxl', mode='w') as writer:
            for s in sheet_order:
                all_sheets[s].to_excel(writer, sheet_name=s, index=False)
    except Exception as e:
        print(Fore.RED + f"❌ Failed to write to section sheet '{sheet_name}': {e}")


def _update_master_overview(storage_dir, sheet_name, uuid, title, filename, text_content, true_count, false_count, full_section_entry):
    """Updates the centralized Master Overview tracking true/false count per section and bulleted texts."""
    
    VDB = Vec_DB_Manager(storage_dir)
    try:
        master_overview_path = VDB.Abstract_Eval_Overview
        overview_sheet = "Overview"
        all_sheets = {}

        if master_overview_path.exists() and master_overview_path.stat().st_size > 0:
            with pd.ExcelFile(master_overview_path, engine='openpyxl') as xls:
                for s in xls.sheet_names:
                    all_sheets[s] = pd.read_excel(master_overview_path, sheet_name=s, engine='openpyxl')
        
        df_overview = all_sheets.get(overview_sheet, pd.DataFrame(columns=["UUID", "Title", "Filename"]))
        df_overview["UUID"] = df_overview["UUID"].astype(str)

        match_mask = df_overview["UUID"] == str(uuid)

        # Dynamic target tracking column assignments
        true_col = f"{sheet_name}_True_Count"
        false_col = f"{sheet_name}_False_Count"

        if match_mask.any():
            df_overview.loc[match_mask, sheet_name] = text_content
            df_overview.loc[match_mask, true_col] = true_count
            df_overview.loc[match_mask, false_col] = false_count
            if title: df_overview.loc[match_mask, "Title"] = title
            if filename: df_overview.loc[match_mask, "Filename"] = filename
        else:
            new_row = {
                "UUID": str(uuid),
                "Title": title,
                "Filename": filename,
                sheet_name: text_content,
                true_col: true_count,
                false_col: false_count
            }
            df_overview = pd.concat([df_overview, pd.DataFrame([new_row])], ignore_index=True)

        all_sheets[overview_sheet] = df_overview

        # First write overview updates back to dictionary structures
        with pd.ExcelWriter(master_overview_path, engine='openpyxl', mode='w') as writer:
            # Force 'Overview' tab to stay strictly at index position 0
            all_sheets[overview_sheet].to_excel(writer, sheet_name=overview_sheet, index=False)
            for s, df in all_sheets.items():
                if s != overview_sheet:
                    df.to_excel(writer, sheet_name=s, index=False)
                    
        # Reuse helper function to cleanly inject or update individual section tabs inside Master Overview
        _write_section_sheet_flat(master_overview_path, sheet_name, full_section_entry)

    except Exception as e:
        print(Fore.RED + f"❌ Failed to update single Master Overview file: {e}")


# =====================================================================
# CORE PROCESSING LAYER
# =====================================================================


def _sync_sections_for_uuid(UUID, title, file_name, json_data, sections, storage_path):
    """Iterates through layout mappings to transform and evaluate section lists or strings."""
    for key, (ex_path, _) in sections.items():
        content_value = json_data.get(key, "Not Found")
        abstract_text = json_data.get("Abstract Text identified:", "Not Found")

        if isinstance(content_value, list):
            _save_list_section(
                UUID=UUID,
                key=key,
                content_list=content_value,
                ex_path=ex_path,
                title=title,
                file_name=file_name,
                abs_txt=abstract_text,
                storage_path=storage_path
            )
        else:
            _save_single_section(
                UUID=UUID,
                key=key,
                content_value=content_value,
                ex_path=ex_path,
                title=title,
                file_name=file_name,
                abs_txt=abstract_text,
                storage_path=storage_path
            )  


def _save_list_section(UUID, key, content_list, ex_path, title, file_name, abs_txt, storage_path):
    """Flattens lists horizontally using alternation layouts and uploads aggregate statistics."""
    # Prevent execution processing if already successfully evaluated
    if _is_duplicate_in_sheet(Path(ex_path), key, str(UUID)):
        return

    entry = {
        "UUID": str(UUID),
        "Title": title,
        "Filename": file_name,
    }

    bullet_lines = []
    true_count = 0
    false_count = 0

    for idx, item in enumerate(content_list):
        content_str = str(item)
        is_subset = content_str.lower() in str(abs_txt).lower() if abs_txt != "Not Found" else False
        
        if is_subset:
            true_count += 1
        else:
            false_count += 1

        # Populate alternating structural sequence columns
        count_label = idx + 1
        # entry[f"{key} {count_label}"] = f"{key} {count_label}"
        entry[f"{key} {count_label} value"] = content_str
        entry[f"{key} {count_label} Is_Subset"] = is_subset

        bullet_lines.append(f"- {content_str}")

    # Write the flattened row format into the sectional file
    _write_section_sheet_flat(Path(ex_path), key, entry)

    # Compile structured details to overview tracker
    aggregate_bullets = "\n ".join(bullet_lines)
    _update_master_overview(storage_path, key, UUID, title, file_name, aggregate_bullets, true_count, false_count, entry)
    print(Fore.GREEN + f"✅ Synced flat list '{key}' ({len(content_list)} items) for UUID: {UUID}")


def _save_single_section(UUID, key, content_value, ex_path, title, file_name, abs_txt, storage_path):
    """Saves single text objects cleanly mapping their matching criteria directly."""
    if _is_duplicate_in_sheet(Path(ex_path), key, str(UUID)):
        return

    content_str = str(content_value)
    is_subset = content_str.lower() in str(abs_txt).lower() if abs_txt != "Not Found" else False
    
    true_count = 1 if is_subset else 0
    false_count = 0 if is_subset else 1

    entry = {
        "UUID": str(UUID),
        "Title": title,
        "Filename": file_name,
        "Content": content_str,
        "Is_Subset": is_subset
    }
    
    _write_section_sheet_flat(Path(ex_path), key, entry)
    # Save overview text and append sectional layout data onto a unique sheet inside Master_Overview.xlsx
    _update_master_overview(storage_path, key, UUID, title, file_name, content_str, true_count, false_count, entry)
    print(Fore.GREEN + f"✅ Synced single_section '{key}' for UUID: {UUID}")


def generate_databases(Storage_path):
    MF = DataAnalyzeManager(Storage_path)
    VDB = Vec_DB_Manager(Storage_path)

    recorded_abstracts = _load_recorded_abstracts(MF)
    if not recorded_abstracts:
        return

    sections = build_sections_eval_map(VDB)

    for UUID in recorded_abstracts:
        MF.update_id_files(UUID)

        title, file_name = _fetch_metadata(MF, UUID)
        json_data = _load_abstract_json(MF, UUID)
        if not json_data:
            continue

        _sync_sections_for_uuid(
            UUID=UUID,
            title=title,
            file_name=file_name,
            json_data=json_data,
            sections=sections,
            storage_path=Storage_path
        ) 


def generate_combined_databases(Source_path, Storage_path):
    MF = DataAnalyzeManager(Source_path)
    VDB = Vec_DB_Manager(Storage_path)

    recorded_abstracts = _load_recorded_abstracts(MF)
    if not recorded_abstracts:
        return

    sections = build_sections_eval_map(VDB)

    for UUID in recorded_abstracts:
        MF.update_id_files(UUID)

        title, file_name = _fetch_metadata(MF, UUID)
        json_data = _load_abstract_json(MF, UUID)
        if not json_data:
            continue

        _sync_sections_for_uuid(
            UUID=UUID,
            title=title,
            file_name=file_name,
            json_data=json_data,
            sections=sections,
            storage_path=Storage_path
        ) 

if __name__ == "__main__":
    Source_paths=[
        '/remotedata/U/DLR+kata_du/ALR DATA/AI_RM/AI_REQ_Results',    
        '/remotedata/U/DLR+kata_du/ALR DATA/AI_SE_Domains_main/AI_SE_Processed_results',
        '/remotedata/U/DLR+kata_du/ALR DATA/LLM_Safety/LLM_Safety_Results',
        '/remotedata/U/DLR+kata_du/ALR DATA/MBSE_MBSA_Aviation/MBSE_MBSA_Aviation_Results',
        '/remotedata/U/DLR+kata_du/ALR DATA/Only_MBSA/Only_MBSA_results'
    ]
    storage_path='/remotedata/U/DLR+kata_du/ALR DATA/00_Container/Combined_DB/AI_SE_Domains'
    storage_path='/remotedata/U/DLR+kata_du/ALR DATA/Only_MBSA/Only_MBSA_results'
    generate_databases(storage_path)

    # generate_databases(storage_path)
    # for S_path in Source_paths:
    #     generate_combined_databases(S_path, storage_path)
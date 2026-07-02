import os
import sys

from RAG_BUILDERs.Master_excel_DB_Builder import _build_sections_Master_map, _sync_sections_master_for_uuid


sys.path.extend([
    r'src',
    r'src/COLLECTION',
    r'Working_Code',
    r'src/DATA_ANALYSIS',
    r'src/COMMON',
    r'src/Command_Line_UI',
    r'Working_Code'
])

import faiss

import json
from datetime import datetime
from COMMON.Excel_Utils import*
from COMMON.File_Manager import DataAnalyzeManager, Vec_DB_Manager
from COMMON.JSON_file_Utils import get_key_from_file, get_value_by_pair
from RAG_BUILDERs.Text_DB_updater import _build_sections_map, _fetch_metadata, _load_abstract_json, _load_recorded_abstracts, _sync_sections_for_uuid
from RAG_BUILDERs.Vector_DB_Updater import add_new_strings_to_index, create_faiss_index_cosine, load_index_file, save_index_file, search_similar, vectorize_strings
from colorama import Fore, Style


        
def _build_sections_map_VDB(VDB):
    """Keeps the exact same section mapping."""
    return {
        "Research Problem": (VDB.Research_problem_DB_bin, VDB.Research_problem_DB_json),
        "Objective": (VDB.Objective_DB_bin, VDB.Objective_DB_json),
        "Methodology": (VDB.Methodology_bin, VDB.Methodology_json),
        "Conclusion": (VDB.Conclusion_DB_bin, VDB.Conclusion_DB_json),
        "Results": (VDB.Results_DB_bin, VDB.Results_DB_json),
        "Research Areas": (VDB.Research_Areas_DB_bin, VDB.Research_Areas_DB_json),
        "Key Concepts": (VDB.Key_concepts_DB_bin, VDB.Key_concepts_DB_json),
    }


def update_VDB_status(VDB, key, str_count, vec_count):
    json_path = VDB.DB_update_logger

    entry = {
        "File type": key,
        "excel_json_entries": str_count,
        "index_vecs": vec_count,
        "time_stamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    skip_json = False
    existing_json_data = []

    # Load existing JSON if present
    if json_path.exists() and json_path.stat().st_size > 0:
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                existing_json_data = json.load(f)

            rec_str_count = get_value_by_pair(json_path, "File type", key, "excel_json_entries")
            rec_index_vecs = get_value_by_pair(json_path, "File type", key, "index_vecs")

            if rec_str_count == str_count and rec_index_vecs == vec_count:
                skip_json = True

        except (json.JSONDecodeError, FileNotFoundError):
            existing_json_data = []

    # If nothing changed, do nothing (keep your existing logic)
    if skip_json:
        print(Fore.YELLOW + f"⏭️ No changes for '{key}' (entries={str_count}, vecs={vec_count}). Skipping update.")
        return False

    # Ensure list format
    if not isinstance(existing_json_data, list):
        existing_json_data = []

    # Update if exists (anchored by "File type"), else append
    updated = False
    for i, rec in enumerate(existing_json_data):
        if isinstance(rec, dict) and rec.get("File type") == key:
            existing_json_data[i] = entry
            updated = True
            break

    if not updated:
        existing_json_data.append(entry)

    # Write back
    try:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(existing_json_data, f, ensure_ascii=False, indent=2)

        action = "Updated" if updated else "Added"
        print(Fore.GREEN + f"✅ {action} VDB status for '{key}' -> entries={str_count}, vecs={vec_count}")
        return True

    except Exception as e:
        print(Fore.RED + f"❌ Failed to write VDB status log: {e}")
        return False
        
def _sync_sections_VDB(VDB, sections):
    """Iterate sections and save either list items or a single string entry."""
    for key, (VDB_path, j_path) in sections.items():
        strings= get_key_from_file(j_path,"Content")
        index=load_index_file(VDB_path)        
        str_count=len(strings)
        if index:
            vec_count = getattr(index, "ntotal", 0) or 0
            print(f"   • Existing vectors in index: {vec_count}")

            if str_count==vec_count:
                print(Fore.YELLOW + "   ⏭️  No sync needed (strings == vectors)." + Style.RESET_ALL)
                update_VDB_status(VDB, key, str_count, vec_count)
                continue

            if vec_count > str_count:
                # Defensive print (logic still same flow; we just avoid slicing weirdness)
                print(Fore.YELLOW + f"   ⚠️  Index has more vectors ({vec_count}) than strings ({str_count}). Skipping add." + Style.RESET_ALL)
                update_VDB_status(VDB, key, str_count, vec_count)
                continue
            new_strings = strings[vec_count:]
            print(Fore.CYAN + f"   ➕ Adding {len(new_strings)} new strings to index..." + Style.RESET_ALL)

            add_new_strings_to_index(VDB_path, new_strings)

            print(Fore.CYAN + "   📝 Updating VDB status log..." + Style.RESET_ALL)
            index_in =load_index_file(VDB_path) 
            vec_count = index_in.ntotal
            update_VDB_status(VDB, key, str_count, vec_count)

            print(Fore.GREEN + f"   ✅ Done: {key} (added {len(new_strings)} vectors)" + Style.RESET_ALL)
        else:
            print(Fore.YELLOW + "   🆕 No existing index found. Creating a new FAISS index..." + Style.RESET_ALL)
            embeds = vectorize_strings(strings)
            print(f"   • Embeddings computed: {len(embeds)}")
            index_in = create_faiss_index_cosine(embeds)
            print(Fore.CYAN + "   💾 Saving new index file..." + Style.RESET_ALL)
            save_index_file(index_in, VDB_path)
            # For a brand new index, vec_count should be 0 for status comparison
            vec_count = index_in.ntotal
            print(Fore.CYAN + "   📝 Updating VDB status log..." + Style.RESET_ALL)
            update_VDB_status(VDB, key, str_count, vec_count)
            print(Fore.GREEN + f"   ✅ Created index and synced: {key}" + Style.RESET_ALL)
            
                
def generate_databases(Storage_path):
    MF = DataAnalyzeManager(Storage_path)
    VDB = Vec_DB_Manager(Storage_path)    
    MASTER_EXCEL_FILE=VDB.Abstract_Overview

    recorded_abstracts = _load_recorded_abstracts(MF)
    if not recorded_abstracts:
        return

    sections = _build_sections_map(VDB)    
    Master_map = _build_sections_Master_map(VDB, MASTER_EXCEL_FILE)

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
            sections=sections
        )
        _sync_sections_master_for_uuid(UUID, title, file_name, json_data, Master_map) 
    
    secs_VDB= _build_sections_map_VDB(VDB)   
    _sync_sections_VDB(VDB, secs_VDB)


def generate_combined_databases(Source_path,Storage_path):
    MF = DataAnalyzeManager(Source_path)
    VDB = Vec_DB_Manager(Storage_path)
    MASTER_EXCEL_FILE=VDB.Abstract_Overview

    recorded_abstracts = _load_recorded_abstracts(MF)
    if not recorded_abstracts:
        return

    sections = _build_sections_map(VDB)
    Master_map = _build_sections_Master_map(VDB, MASTER_EXCEL_FILE)

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
            sections=sections
        ) 
        _sync_sections_master_for_uuid(UUID, title, file_name, json_data, Master_map) 
    
    secs_VDB= _build_sections_map_VDB(VDB)   
    _sync_sections_VDB(VDB, secs_VDB)

     


if __name__ == "__main__":
    Source_paths=['/remotedata/U/DLR+kata_du/ALR DATA/AI_RM/AI_REQ_Results',    
    '/remotedata/U/DLR+kata_du/ALR DATA/AI_SE_Domains_main/AI_SE_Processed_results',
    '/remotedata/U/DLR+kata_du/ALR DATA/LLM_Safety/LLM_Safety_Results',
    '/remotedata/U/DLR+kata_du/ALR DATA/MBSE_MBSA_Aviation/MBSE_MBSA_Aviation_Results',
    '/remotedata/U/DLR+kata_du/ALR DATA/Only_MBSA/Only_MBSA_results']
    storage_path='/remotedata/U/DLR+kata_du/ALR DATA/00_Container/Combined_DB/AI_SE_Domains'

    
    generate_databases(storage_path)
    for S_path in Source_paths:
        generate_combined_databases(S_path,storage_path)



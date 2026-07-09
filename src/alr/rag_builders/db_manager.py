from alr.rag_builders.master_excel_db_builder import _sync_sections_master_for_uuid
from alr.common.sections import*
import json
import pandas as pd
from pathlib import Path
from datetime import datetime
from alr.common.file_manager import DataAnalyzeManager, Vec_DB_Manager
from alr.common.json_utils import get_key_from_file, get_value_by_pair
from alr.rag_builders.text_db_updater import _fetch_metadata, _load_abstract_json, _load_recorded_abstracts, _sync_sections_for_uuid
from alr.rag_builders.vector_db_updater import add_new_strings_to_index, create_faiss_index_cosine, load_index_file, save_index_file, search_similar, vectorize_strings
from colorama import Fore, Style
from alr.common.sections import build_sections_map_vdb, build_sections_map_vdb_excel

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


def _excel_content_strings(excel_path):
    """
    Read the "Content" column POSITIONALLY from a section Excel DB.

    This is the string source for the vector index. It deliberately mirrors
    what querry_excecuter aligns against at query time, with one important
    difference: blank/NaN cells are NOT dropped — they are replaced with a
    single-space placeholder. Dropping them would shift every following row
    and shrink the count, which is exactly the index/Excel mismatch this
    change fixes. With placeholders, Excel row i == index vector i, always.

    Returns [] when the Excel is missing/empty/has no Content column, so the
    caller can skip the section instead of wiping a valid index.
    """
    excel_path = Path(excel_path)
    if not excel_path.exists() or excel_path.stat().st_size == 0:
        return []
    try:
        df = pd.read_excel(excel_path)
    except Exception as e:
        print(Fore.RED + f"   ❌ Could not read Excel DB '{excel_path.name}': {e}" + Style.RESET_ALL)
        return []
    if "Content" not in df.columns:
        print(Fore.RED + f"   ❌ No 'Content' column in '{excel_path.name}'." + Style.RESET_ALL)
        return []
    # Keep one string per row; placeholder for blanks (some embedding APIs
    # reject empty strings, and we must keep positional alignment).
    return [(s if s.strip() else " ") for s in df["Content"].fillna("").astype(str)]


def _rebuild_section_index(VDB_path, strings):
    """Force-rebuild one section's FAISS index from the given strings."""
    embeds = vectorize_strings(strings)
    print(f"   • Embeddings computed: {len(embeds)}")
    index_in = create_faiss_index_cosine(embeds)
    save_index_file(index_in, VDB_path)
    return index_in.ntotal


def _sync_sections_VDB(VDB, sections, rebuild: bool = False):
    """
    Sync each section's FAISS index against its EXCEL DB "Content" column
    (previously the section JSON — which could drift ahead of the Excel and
    left index.ntotal > Excel rows with no way to heal).

    sections: {key: (bin_path, excel_path)} — from build_sections_map_vdb_excel.

    rebuild=False (default): incremental — appends strings[vec_count:] when
        the Excel has more rows than the index. NOTE: incremental append is
        only positionally valid AFTER a one-time rebuild, because existing
        indexes were built in JSON order. Any count mismatch in the other
        direction (index > Excel) is reported with instructions instead of
        being silently skipped.
    rebuild=True: re-embed every section from the Excel from scratch. Run
        once when migrating from the JSON-sourced indexes, or whenever
        entries were edited/removed (append-only sync can't see those).
    """
    for key, (VDB_path, ex_path) in sections.items():
        print(Fore.LIGHTBLUE_EX + f"\n— Section: {key}" + Style.RESET_ALL)
        strings = _excel_content_strings(ex_path)
        str_count = len(strings)

        if str_count == 0:
            print(Fore.YELLOW + f"   ⏭️  No Excel content for '{key}'. Skipping (index left untouched)." + Style.RESET_ALL)
            continue

        if rebuild:
            print(Fore.CYAN + f"   🔄 Rebuilding index from Excel ({str_count} rows)..." + Style.RESET_ALL)
            vec_count = _rebuild_section_index(VDB_path, strings)
            update_VDB_status(VDB, key, str_count, vec_count)
            print(Fore.GREEN + f"   ✅ Rebuilt: {key} ({vec_count} vectors)" + Style.RESET_ALL)
            continue

        index = load_index_file(VDB_path)
        if index:
            vec_count = getattr(index, "ntotal", 0) or 0
            print(f"   • Existing vectors in index: {vec_count} | Excel rows: {str_count}")

            if str_count == vec_count:
                print(Fore.YELLOW + "   ⏭️  No sync needed (Excel rows == vectors)." + Style.RESET_ALL)
                update_VDB_status(VDB, key, str_count, vec_count)
                continue

            if vec_count > str_count:
                print(Fore.RED
                      + f"   ⚠️  Index has more vectors ({vec_count}) than Excel rows ({str_count}). "
                      + f"This index predates the Excel-sourced sync (or rows were removed). "
                      + f"Run the sync with rebuild=True (rebuild_vector_databases) to fix alignment."
                      + Style.RESET_ALL)
                update_VDB_status(VDB, key, str_count, vec_count)
                continue

            new_strings = strings[vec_count:]
            print(Fore.CYAN + f"   ➕ Adding {len(new_strings)} new strings to index..." + Style.RESET_ALL)

            add_new_strings_to_index(VDB_path, new_strings)

            print(Fore.CYAN + "   📝 Updating VDB status log..." + Style.RESET_ALL)
            index_in = load_index_file(VDB_path)
            vec_count = index_in.ntotal
            update_VDB_status(VDB, key, str_count, vec_count)

            print(Fore.GREEN + f"   ✅ Done: {key} (added {len(new_strings)} vectors)" + Style.RESET_ALL)
        else:
            print(Fore.YELLOW + "   🆕 No existing index found. Creating a new FAISS index from Excel..." + Style.RESET_ALL)
            vec_count = _rebuild_section_index(VDB_path, strings)
            print(Fore.CYAN + "   📝 Updating VDB status log..." + Style.RESET_ALL)
            update_VDB_status(VDB, key, str_count, vec_count)
            print(Fore.GREEN + f"   ✅ Created index and synced: {key}" + Style.RESET_ALL)


def rebuild_vector_databases(Storage_path):
    """
    One-time (or on-demand) full rebuild of every section's FAISS index from
    its Excel DB "Content" column.

    Run this ONCE per storage space when switching from the old JSON-sourced
    indexes: existing indexes were built in JSON order, so incremental
    Excel-based appends are only positionally valid after this rebuild. Also
    the right tool after editing/deleting entries in an Excel DB, since the
    incremental sync is append-only and cannot see such changes.
    """
    VDB = Vec_DB_Manager(Storage_path)
    secs_VDB = build_sections_map_vdb_excel(VDB)
    print(Fore.CYAN + Style.BRIGHT + f"--- Rebuilding all vector DBs from Excel: {Storage_path} ---" + Style.RESET_ALL)
    _sync_sections_VDB(VDB, secs_VDB, rebuild=True)
    print(Fore.GREEN + Style.BRIGHT + "--- Vector DB rebuild complete ---" + Style.RESET_ALL)


def generate_databases(Storage_path, do_text: bool = True, do_vector: bool = True,
                       rebuild_vector: bool = False):
    """
    Sync the RAG databases for a storage space.

    do_text:   sync the text databases (per-section JSON/Excel DBs + master
               Excel overview) from the recorded abstracts.
    do_vector: sync the FAISS vector DBs. The vector sync now reads the
               per-section EXCEL DBs ("Content" column, positionally) written
               by the text sync — the same source the query executor aligns
               against — instead of the section JSONs, which could drift
               ahead of the Excel and desynchronize index.ntotal from the
               Excel row count. A vector-only run assumes the text DBs are
               up to date.
    rebuild_vector: force a from-scratch re-embed of every section index from
               the Excel. Needed ONCE when migrating existing (JSON-order)
               indexes to the Excel-sourced sync; afterwards the default
               incremental append is safe again.

    Defaults keep the previous combined behaviour for existing callers. Both
    syncs are incremental: the text side upserts per UUID and the vector side
    (see _sync_sections_VDB) only embeds/appends the Excel rows that are not
    yet in the index, instead of rebuilding from scratch.
    """
    MF = DataAnalyzeManager(Storage_path)
    VDB = Vec_DB_Manager(Storage_path)
    MASTER_EXCEL_FILE = VDB.Abstract_Overview

    recorded_abstracts = _load_recorded_abstracts(MF)
    if not recorded_abstracts:
        return

    if do_text:
        sections = build_sections_map(VDB)
        Master_map = build_sections_master_map(VDB, MASTER_EXCEL_FILE)

        # One UUID cache per sync run: each section's Excel/JSON DB pair is
        # read once up front (see text_db_updater.save_to_db) instead of being
        # re-read for every single entry, which makes re-runs over an
        # already-synced space near-instant on the text side.
        uuid_cache = {}

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
                uuid_cache=uuid_cache
            )
            _sync_sections_master_for_uuid(UUID, title, file_name, json_data, Master_map)

    if do_vector:
        secs_VDB = build_sections_map_vdb_excel(VDB)
        _sync_sections_VDB(VDB, secs_VDB, rebuild=rebuild_vector)


def generate_combined_databases(Source_path, Storage_path, rebuild_vector: bool = False):
    MF = DataAnalyzeManager(Source_path)
    VDB = Vec_DB_Manager(Storage_path)
    MASTER_EXCEL_FILE = VDB.Abstract_Overview

    recorded_abstracts = _load_recorded_abstracts(MF)
    if not recorded_abstracts:
        return

    sections = build_sections_map(VDB)
    Master_map = build_sections_master_map(VDB, MASTER_EXCEL_FILE)

    # Same per-run UUID cache as generate_databases (one read per DB pair).
    uuid_cache = {}

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
            uuid_cache=uuid_cache
        )
        _sync_sections_master_for_uuid(UUID, title, file_name, json_data, Master_map)

    secs_VDB = build_sections_map_vdb_excel(VDB)
    _sync_sections_VDB(VDB, secs_VDB, rebuild=rebuild_vector)




if __name__ == "__main__":
    Source_paths=['/remotedata/U/DLR+kata_du/ALR DATA/AI_RM/AI_REQ_Results',
    '/remotedata/U/DLR+kata_du/ALR DATA/AI_SE_Domains_main/AI_SE_Processed_results',
    '/remotedata/U/DLR+kata_du/ALR DATA/LLM_Safety/LLM_Safety_Results',
    '/remotedata/U/DLR+kata_du/ALR DATA/MBSE_MBSA_Aviation/MBSE_MBSA_Aviation_Results',
    '/remotedata/U/DLR+kata_du/ALR DATA/Only_MBSA/Only_MBSA_results']
    storage_path='/remotedata/U/DLR+kata_du/ALR DATA/00_Container/Combined_DB/AI_SE_Domains'

    # ONE-TIME migration: existing indexes were built in JSON order, so
    # rebuild them from the Excel DBs before the incremental sync takes over.
    # rebuild_vector_databases(storage_path)

    generate_databases(storage_path)
    for S_path in Source_paths:
        generate_combined_databases(S_path,storage_path)
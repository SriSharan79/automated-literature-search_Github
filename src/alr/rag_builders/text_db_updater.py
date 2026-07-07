from pathlib import Path
from colorama import Fore
import pandas as pd
from alr.common.file_manager import DataAnalyzeManager, Vec_DB_Manager
from alr.common.excel_utils import extract_column, get_corresponding_value
import json

def _load_db_pair(excel_path, json_path):
    """
    Read an Excel/JSON DB pair ONCE and return a cache entry for save_to_db:

        {"excel_df": DataFrame or None,  "excel": set of UUID strings,
         "json_data": list,              "json":  set of UUID strings,
         "excel_read_error": bool}

    Used by save_to_db's uuid_cache fast path so one sync run performs one
    read per file instead of one read per entry.
    """
    excel_df = None
    excel_uuids = set()
    excel_read_error = False
    if excel_path.exists() and excel_path.stat().st_size > 0:
        try:
            # Explicitly use 'openpyxl' engine
            excel_df = pd.read_excel(excel_path, engine='openpyxl')
            if not excel_df.empty and "UUID" in excel_df.columns:
                excel_uuids = set(excel_df["UUID"].astype(str).values)
        except Exception as e:
            print(Fore.YELLOW + f"⚠️ Excel read error (will attempt overwrite): {e}")
            excel_df = None
            excel_read_error = True

    json_data = []
    json_uuids = set()
    if json_path.exists() and json_path.stat().st_size > 0:
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
            if not isinstance(json_data, list):
                json_data = []
            json_uuids = {str(item.get("UUID")) for item in json_data if isinstance(item, dict)}
        except (json.JSONDecodeError, FileNotFoundError):
            json_data = []

    return {"excel_df": excel_df, "excel": excel_uuids,
            "json_data": json_data, "json": json_uuids,
            "excel_read_error": excel_read_error}


def save_to_db(excel_path, json_path, data_entry, uuid_cache=None):
    """
    Appends data to an Excel file and a JSON list with explicit engine specification.

    uuid_cache: optional dict shared across calls within ONE sync run. When
    given, each (excel_path, json_path) pair is read once via _load_db_pair()
    and the registered UUIDs (plus the loaded Excel DataFrame / JSON list) are
    kept up to date in memory, so the duplicate check is O(1) and a re-run
    over an already-synced space no longer re-reads both files for every
    single entry. All writes go through this function, which keeps the cache
    consistent with the files. Pass None (default) to keep the original
    read-per-entry behaviour unchanged.
    """
    target_uuid = str(data_entry.get("UUID"))

    cache_entry = None
    if uuid_cache is not None:
        cache_key = (str(excel_path), str(json_path))
        cache_entry = uuid_cache.get(cache_key)
        if cache_entry is None:
            cache_entry = _load_db_pair(excel_path, json_path)
            uuid_cache[cache_key] = cache_entry
        skip_excel = target_uuid in cache_entry["excel"]
        skip_json = target_uuid in cache_entry["json"]
        existing_json_data = cache_entry["json_data"]
    else:
        # --- Check for Duplicates in Excel (original per-call behaviour) ---
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

        # --- Check for Duplicates in JSON (original per-call behaviour) ---
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
            if cache_entry is not None:
                if cache_entry["excel_read_error"] and excel_path.exists() and excel_path.stat().st_size > 0:
                    # Mirror the uncached path: an existing-but-unreadable
                    # workbook must not be silently overwritten. Retry the read
                    # (raising into the except below, exactly like before).
                    df_old = pd.read_excel(excel_path, engine='openpyxl')
                else:
                    df_old = cache_entry["excel_df"]
            elif excel_path.exists() and excel_path.stat().st_size > 0:
                df_old = pd.read_excel(excel_path, engine='openpyxl')
            else:
                df_old = None

            if df_old is not None:
                df_final = pd.concat([df_old, df_new], ignore_index=True)
            else:
                df_final = df_new
            # Explicitly use 'openpyxl' engine for writing
            df_final.to_excel(excel_path, index=False, engine='openpyxl')
            if cache_entry is not None:
                cache_entry["excel_df"] = df_final
                cache_entry["excel"].add(target_uuid)
        except Exception as e:
            print(Fore.RED + f"❌ Failed to save Excel: {e}")

    # --- Save to JSON ---
    if not skip_json:
        try:
            existing_json_data.append(data_entry)
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(existing_json_data, f, indent=4)
            if cache_entry is not None:
                # existing_json_data IS cache_entry["json_data"], so the cached
                # list already contains the new entry; just record the UUID.
                cache_entry["json"].add(target_uuid)
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


def _sync_sections_for_uuid(UUID, title, file_name, json_data, sections, uuid_cache=None):
    """
    Iterate sections and save either list items or a single string entry.

    uuid_cache: optional dict shared across all UUIDs of one sync run (see
    save_to_db) so each section's Excel/JSON pair is read once per run instead
    of once per entry.
    """
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
                file_name=file_name,
                uuid_cache=uuid_cache
            )
        else:
            _save_single_section(
                UUID=UUID,
                key=key,
                content_value=content_value,
                ex_path=ex_path,
                j_path=j_path,
                title=title,
                file_name=file_name,
                uuid_cache=uuid_cache
            )


def _save_list_section(UUID, key, content_list, ex_path, j_path, title, file_name, uuid_cache=None):
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
            save_to_db(ex_path, j_path, entry, uuid_cache=uuid_cache)
        except Exception as e:
            print(Fore.RED + f"Error saving list item in {key} for {UUID}: {e}")

    print(Fore.GREEN + f"✅ Synced list '{key}' ({len(content_list)} items) for UUID: {UUID}")


def _save_single_section(UUID, key, content_value, ex_path, j_path, title, file_name, uuid_cache=None):
    """Save single-string sections exactly as before."""
    entry = {
        "UUID": UUID,
        "Original_UUID": UUID,
        "Title": title,
        "Filename": file_name,
        "Content": content_value,
    }
    try:
        save_to_db(ex_path, j_path, entry, uuid_cache=uuid_cache)
        print(Fore.GREEN + f"✅ Successfully synchronized {key} for UUID: {UUID}")
    except Exception as e:
        print(Fore.RED + f"Error saving {key} for {UUID}: {e}")

if __name__ == "__main__":
    storage_path=r'U:\ALR DATA\SLR_Process_Main\SLR_Process_results'

from pathlib import Path
from datetime import datetime
from colorama import Fore, Style
import pandas as pd
from alr.common.file_manager import DataAnalyzeManager, Vec_DB_Manager
from alr.common.excel_utils import extract_column, get_corresponding_value
import json
import os
# ADD:
from alr.common.sections import build_sections_master_map
from alr.rag_builders import db_cache

# Documents that could NOT be written into a built database. A failure on one
# document never aborts a build: it is skipped, recorded in one of these logs
# and the build continues with the next file. Both logs are append-only, so
# they keep the history of every run.
MASTER_EXCEL_SKIPPED_LOG = "Master_Excel_not_added.xlsx"


def _skip_row(stage, error, space=None, uuid="", title="", filename="", sections=""):
    """One row of a 'not added to the database' log (shared by the master
    Excel and common-DB builders)."""
    return {
        "UUID": uuid, "Title": title, "Filename": filename,
        "Source_Folder": str(space) if space else "",
        "Stage": stage, "Sections": sections,
        "Error": f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error),
        "Run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _append_skiplog(log_path, skipped_rows):
    """Append this run's failures to a skip log (never overwrites earlier
    runs). Returns the log path, or None when nothing was written."""
    if not skipped_rows:
        return None
    log_path = Path(log_path)
    try:
        rows = list(skipped_rows)
        if log_path.exists() and log_path.stat().st_size > 0:
            try:
                rows = pd.read_excel(log_path).to_dict("records") + rows
            except Exception:
                pass  # unreadable old log: start a fresh one from this run
        log_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_excel(log_path, index=False)
        return log_path
    except Exception as e:
        print(Fore.RED + f"❌ Could not write the skip log {log_path}: {e}")
        return None


def _save_master_skiplog(master_excel_path, skipped_rows):
    """Write the master-workbook skip log next to the workbook itself."""
    return _append_skiplog(Path(master_excel_path).parent / MASTER_EXCEL_SKIPPED_LOG,
                           skipped_rows)

# ---------------------------------------------------------------------------
# Batched workbook writing
# ---------------------------------------------------------------------------
# Without a session, every single section of every single document re-reads
# AND re-writes the whole master workbook (all sheets) plus its section JSON.
# That is quadratic in the size of the database: a build slows down the longer
# it runs and looks like the app has hung. Inside a session the workbook and
# the JSONs are read once, all entries are applied in memory, and the files
# are written once at the end (plus periodic flushes so a crash or a cancel
# keeps what has been done so far).
_SESSIONS = {}

OVERVIEW_SHEET = "Overview"

# Documents between checkpoint writes inside a session (a crash or a cancel
# then costs at most this many documents, not the whole run).
FLUSH_EVERY = 25


class _WorkbookSession:
    """In-memory image of one master workbook + the section JSONs beside it."""

    def __init__(self, master_excel_path):
        self.path = Path(master_excel_path)
        self.sheets = {}          # sheet name -> DataFrame
        self.order = []           # sheet order, "Overview" forced first on write
        self.uuids = {}           # sheet name -> set of UUID strings
        self.dirty_excel = False
        self._load()

    def _load(self):
        if self.path.exists() and self.path.stat().st_size > 0:
            try:
                with pd.ExcelFile(self.path, engine="openpyxl") as xls:
                    self.order = list(xls.sheet_names)
                    for s_name in self.order:
                        df = pd.read_excel(self.path, sheet_name=s_name, engine="openpyxl")
                        self.sheets[s_name] = df
                        self.uuids[s_name] = (set(df["UUID"].astype(str).values)
                                              if "UUID" in df.columns else set())
            except Exception as e:
                print(Fore.YELLOW + f"⚠️ Master workbook read error (will attempt overwrite): {e}")
                self.sheets, self.order, self.uuids = {}, [], {}

    def flush(self):
        """Write the workbook and every touched JSON DB back to disk."""
        if self.dirty_excel:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            order = [s for s in self.order if s != OVERVIEW_SHEET]
            if OVERVIEW_SHEET in self.sheets:
                order.insert(0, OVERVIEW_SHEET)
            with pd.ExcelWriter(self.path, engine="openpyxl", mode="w") as writer:
                for s_name in order:
                    self.sheets[s_name].to_excel(writer, sheet_name=s_name, index=False)
            self.dirty_excel = False
        # The section JSONs are shared with the per-section text DB writer.
        db_cache.flush()


class workbook_session:
    """
    Context manager batching every :func:`save_to_db` write for one master
    workbook: read once, apply in memory, write once on exit.

    Nested/duplicate sessions for the same workbook are reference-counted, so
    a caller can open one without knowing whether an outer one already exists.
    Entering is free of behaviour changes for other workbooks — save_to_db
    falls back to its per-entry read/write path when no session is active.
    """

    def __init__(self, master_excel_path):
        self.key = str(Path(master_excel_path))

    def __enter__(self):
        entry = _SESSIONS.get(self.key)
        if entry is None:
            entry = [_WorkbookSession(self.key), 0]
            _SESSIONS[self.key] = entry
        entry[1] += 1
        db_cache.open_scope()
        return entry[0]

    def __exit__(self, exc_type, exc, tb):
        entry = _SESSIONS.get(self.key)
        if entry is None:
            db_cache.close_scope()
            return False
        entry[1] -= 1
        if entry[1] <= 0:
            del _SESSIONS[self.key]
            try:
                entry[0].flush()
            except Exception as e:  # noqa: BLE001 - report, never mask the body's error
                print(Fore.RED + f"❌ Failed to write the master workbook {entry[0].path}: {e}")
        db_cache.close_scope()
        return False


def _active_session(master_excel_path):
    entry = _SESSIONS.get(str(Path(master_excel_path)))
    return entry[0] if entry else None


def _apply_section_entry(sheets, order, sheet_name, data_entry):
    """Add one entry to its section sheet and mirror it onto the Overview
    sheet, in memory. Shared by the batched and per-entry write paths."""
    df_new = pd.DataFrame([data_entry])
    if sheet_name in sheets:
        sheets[sheet_name] = pd.concat([sheets[sheet_name], df_new], ignore_index=True)
    else:
        sheets[sheet_name] = df_new
        order.append(sheet_name)

    target_uuid = str(data_entry.get("UUID"))
    original_uuid = str(data_entry.get("Original_UUID", target_uuid))
    content_value = data_entry.get("Content", "")
    title = data_entry.get("Title", "")
    filename = data_entry.get("Filename", "")

    df_overview = sheets.get(OVERVIEW_SHEET)
    if df_overview is None:
        df_overview = pd.DataFrame(columns=["UUID", "Title", "Filename"])
    df_overview["UUID"] = df_overview["UUID"].astype(str)

    match_mask = df_overview["UUID"] == original_uuid
    if match_mask.any():
        df_overview.loc[match_mask, sheet_name] = content_value
        if title:
            df_overview.loc[match_mask, "Title"] = title
        if filename:
            df_overview.loc[match_mask, "Filename"] = filename
    else:
        df_overview = pd.concat(
            [df_overview, pd.DataFrame([{
                "UUID": original_uuid, "Title": title,
                "Filename": filename, sheet_name: content_value,
            }])], ignore_index=True)

    sheets[OVERVIEW_SHEET] = df_overview
    if OVERVIEW_SHEET not in order:
        order.append(OVERVIEW_SHEET)


def save_to_db(master_excel_path, sheet_name, json_path, data_entry):
    """
    Appends data to a specific sheet inside a single Master Excel file,
    updates a master 'Overview' sheet at the 1st position with section columns,
    and appends to a JSON list.

    Inside a :class:`workbook_session` the workbook and the JSON are held in
    memory and written once when the session closes; otherwise this keeps its
    original read-modify-write-per-entry behaviour.
    """
    target_uuid = str(data_entry.get("UUID"))
    master_excel_path = Path(master_excel_path)
    json_path = Path(json_path)

    session = _active_session(master_excel_path)
    if session is not None:
        skip_excel = target_uuid in session.uuids.get(sheet_name, ())
        if not skip_excel:
            _apply_section_entry(session.sheets, session.order, sheet_name, data_entry)
            session.uuids.setdefault(sheet_name, set()).add(target_uuid)
            session.dirty_excel = True
        # The JSON DB is shared with the per-section text writer, so it goes
        # through the shared cache rather than a private copy.
        db_cache.add(json_path, data_entry)
        return

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
            all_sheets_data = {}
            sheet_order = []
            if master_excel_path.exists() and master_excel_path.stat().st_size > 0:
                with pd.ExcelFile(master_excel_path, engine='openpyxl') as xls:
                    sheet_order = list(xls.sheet_names)
                    for s_name in sheet_order:
                        all_sheets_data[s_name] = pd.read_excel(master_excel_path, sheet_name=s_name, engine='openpyxl')

            _apply_section_entry(all_sheets_data, sheet_order, sheet_name, data_entry)

            # Re-order sheets so "Overview" is strictly forced as the 1st tab.
            if OVERVIEW_SHEET in sheet_order:
                sheet_order.remove(OVERVIEW_SHEET)
            sheet_order.insert(0, OVERVIEW_SHEET)

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
    """
    Iterate sections and save either list items or a single string entry.

    One unwritable section never stops the others: failures are collected and
    returned as ``{section key: error string}`` (empty dict when everything
    was written), so callers can report exactly what did not make it in.
    """
    failed = {}
    for key, (ex_path, sheet_name, j_path) in sections.items():
        content_value = json_data.get(key, "Not Found")

        if isinstance(content_value, list):
            err = _save_list_section(
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
            err = _save_single_section(
                UUID=UUID,
                key=key,
                content_value=content_value,
                ex_path=ex_path,
                sheet_name=sheet_name,
                j_path=j_path,
                title=title,
                file_name=file_name
            )
        if err:
            failed[key] = err
    return failed


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
    """Save list-like sections onto specific sheets as a single bulleted string.
    Returns None on success, or the error string when the section could not be
    written (never raises, so the remaining sections still get their chance)."""
    if not content_list:
        print(Fore.YELLOW + f"⚠️ Content list for '{key}' is empty. Skipping save.")
        return None

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
        return None
    except Exception as e:
        print(Fore.RED + f"Error saving list content in {key} for {UUID}: {e}")
        return f"{type(e).__name__}: {e}"

def _save_single_section(UUID, key, content_value, ex_path, sheet_name, j_path, title, file_name):
    """Save single-string sections onto specific sheets. Returns None on
    success, or the error string when the section could not be written."""
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
        return None
    except Exception as e:
        print(Fore.RED + f"Error saving {key} for {UUID}: {e}")
        return f"{type(e).__name__}: {e}"

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

    A document that cannot be consolidated (unreadable analysis JSON, missing
    metadata, a locked or broken master workbook / section JSON) is skipped and
    the build continues with the next file; a document whose data was only
    partly written counts as incomplete rather than aborting the run. Every such
    item is appended to ``Master_Excel_not_added.xlsx`` next to the master
    workbook, with the UUID, title, filename, the affected attributes and the
    error.

    ``progress_callback(done, total)`` is called after each document if given.
    ``should_cancel`` is an optional callable checked before each document for
    cooperative cancellation (partial results are preserved). Returns
    ``(written, master_excel_path, not_added)`` where not_added is the list of
    skip-log rows produced by this run.
    """
    from alr.common.sections import RAG_SOURCE_BY_KEY

    MF = DataAnalyzeManager(folder_path=storage_path)
    VDB = Vec_DB_Manager(folder_path=storage_path)

    master_excel_path = Path(master_excel_path) if master_excel_path else Path(VDB.Abstract_Overview)

    # Map sections onto the single master file + their specific sheets.
    sections_map = build_sections_master_map(VDB, master_excel_path, only=section_keys)
    if not sections_map:
        print(Fore.RED + "⚠️ No attributes selected. Nothing to consolidate.")
        return 0, master_excel_path, []

    # Only load the analysis JSONs the selected attributes actually come from.
    sources = {RAG_SOURCE_BY_KEY[key] for key in sections_map if key in RAG_SOURCE_BY_KEY}

    # UUIDs to process come from the processed-file registry.
    if not Path(MF.excel_success).exists():
        print(Fore.RED + f"⚠️ No processed-file registry found at {MF.excel_success}. Nothing to consolidate.")
        return 0, master_excel_path, []

    recorded_uuids = extract_column(MF.excel_success, "UUID")
    total = len(recorded_uuids)
    written = 0
    # Documents that could not be consolidated; the build carries on and these
    # end up in MASTER_EXCEL_SKIPPED_LOG.
    not_added = []

    # One read + one write for the whole workbook instead of one of each per
    # section per document (which made long builds crawl).
    with workbook_session(master_excel_path) as session:
        for i, uuid in enumerate(recorded_uuids, 1):
            if should_cancel is not None and should_cancel():
                print(Fore.YELLOW + "Master Excel build cancelled by user.")
                break

            title = file_name = ""
            try:
                MF.update_id_files(uuid)
                title, file_name = _fetch_metadata(MF, uuid)
                json_data = _load_analysis_json(MF, uuid, sources)

                if json_data:
                    failed = _sync_sections_master_for_uuid(
                        uuid, title, file_name, json_data, sections_map)
                    if failed:
                        print(Fore.RED + f"❌ '{file_name or uuid}': {len(failed)} attribute(s) could not be "
                                         f"written to the master workbook — continuing with the next file.")
                        not_added.append(_skip_row(
                            "partial" if len(failed) < len(sections_map) else "sync",
                            "; ".join(f"{k}: {v}" for k, v in failed.items()),
                            uuid=uuid, title=title, filename=file_name,
                            sections=", ".join(failed)))
                    if len(failed) < len(sections_map):
                        written += 1
            except Exception as e:
                print(Fore.RED + f"❌ '{file_name or uuid}' could not be consolidated into the master "
                                 f"workbook ({type(e).__name__}: {e}) — skipped, continuing with the next file.")
                not_added.append(_skip_row("document", e, uuid=uuid, title=title, filename=file_name))

            # Checkpoint so a crash or a cancel keeps the work done so far,
            # without paying a full workbook write per document.
            if i % FLUSH_EVERY == 0:
                try:
                    session.flush()
                except Exception as e:  # noqa: BLE001 - a failed checkpoint must not stop the build
                    print(Fore.RED + f"❌ Could not checkpoint the master workbook: {e}")

            if progress_callback:
                progress_callback(i, total)

    print(Fore.GREEN + f"✅ Master Excel workbook updated with {written} document(s): {master_excel_path}")
    if not_added:
        log_path = _save_master_skiplog(master_excel_path, not_added)
        print(Fore.YELLOW + Style.BRIGHT
              + f"⚠️ {len(not_added)} document(s) were NOT (fully) added to the master workbook"
              + (f" — see {log_path}" if log_path else "") + Style.RESET_ALL)
    return written, master_excel_path, not_added


if __name__ == "__main__":
    storage_path = "/remotedata/U/DLR+kata_du/ALR DATA/MBSE_MBSA_Specific_literature/Analyzed_results"
    build_master_excel_db(storage_path)
"""
document_inspector.py - SQL-first single-document lookup for the Review tool.

Answers "show me everything about this document": the lookup always starts
from the SQL database; whatever is missing there is filled from the storage
space the document was synced from (its ``source_folder``), or from a
user-chosen space when the document was never synced at all. The PDF is
located the same way: known paths first (``relative_path``, the space's
``pdf_files`` folder), then an optional recursive search through a
user-chosen folder tree.

Every assembled row carries its provenance ("SQL", "Storage space" or
"Registry") so the Review UI can show where each value came from.
"""

import json
import os
from pathlib import Path

from alr.common.sql_store import (
    ENRICHMENT_COLUMNS,
    SECTION_COLUMNS,
    _BASE_COLUMNS,
)

SEARCH_MODES = ("UUID", "Title", "Filename")

REGISTRY_FILENAME = "Processed_file_registry.xlsx"

# Registry-column name per search mode (the on-disk registry uses "UUID",
# the SQL table uses lowercase "uuid").
_REGISTRY_FIELD = {"uuid": "UUID", "title": "title", "filename": "filename"}
_SQL_FIELD = {"uuid": "uuid", "title": "title", "filename": "filename"}

SOURCE_SQL = "SQL"
SOURCE_SPACE = "Storage space"
SOURCE_REGISTRY = "Registry"


def _load_json(path):
    """Return (parsed, raw_text) for a JSON file; (None, None) when absent/unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return None, None
    try:
        return json.loads(raw), raw
    except (json.JSONDecodeError, ValueError):
        return None, raw


def _blank(value) -> bool:
    if value is None:
        return True
    s = str(value).strip()
    return not s or s.lower() in ("nan", "none", "null")


# ---------------------------------------------------------------------------
# Lookups: SQL first, then a storage space's registry
# ---------------------------------------------------------------------------
def lookup_sql_documents(store, mode: str, term: str) -> list:
    """
    Find documents in the SQL database by UUID, Title or Filename.
    UUID prefers an exact (case-insensitive) match; all modes fall back to a
    case-insensitive contains match. Returns a list of row dicts.
    """
    term = str(term or "").strip()
    field = _SQL_FIELD.get(str(mode or "").strip().lower())
    if not term or not field:
        return []
    term_l = term.lower()
    rows = store.list_documents(search=term)
    matches = [r for r in rows if term_l in str(r.get(field) or "").lower()]
    if field == "uuid":
        exact = [r for r in matches if str(r.get("uuid") or "").strip().lower() == term_l]
        if exact:
            return exact
    return matches


def is_storage_space(folder) -> bool:
    """A folder counts as a storage space when it holds the processed-file registry."""
    if not folder:
        return False
    return (Path(folder) / REGISTRY_FILENAME).exists()


def find_registry_documents(space_folder, mode: str, term: str) -> list:
    """
    Find documents in a storage space's Processed_file_registry.xlsx by UUID,
    Title or Filename (same matching rules as the SQL lookup). Returns the
    matching registry rows as dicts.
    """
    import pandas as pd

    term = str(term or "").strip()
    col = _REGISTRY_FIELD.get(str(mode or "").strip().lower())
    reg_path = Path(space_folder or "") / REGISTRY_FILENAME
    if not term or not col or not reg_path.exists():
        return []
    try:
        df = pd.read_excel(reg_path)
    except Exception:
        return []
    if col not in df.columns:
        return []
    term_l = term.lower()
    rows = [r for r in df.to_dict("records")
            if term_l in str(r.get(col) or "").lower()]
    if col == "UUID":
        exact = [r for r in rows if str(r.get("UUID") or "").strip().lower() == term_l]
        if exact:
            return exact
    return rows


# ---------------------------------------------------------------------------
# Storage-space payloads (the on-disk analysis JSONs for one document)
# ---------------------------------------------------------------------------
def load_space_payloads(space_folder, uuid: str) -> dict:
    """
    Read the document's on-disk analysis JSONs from a storage space:
    abstract, introduction, results & conclusion and references. The folder
    must already be a storage space (registry present) - this never turns an
    arbitrary folder into one. Returns {} when it isn't.

    Keys: abstract / intro / rescon / references (parsed, or None),
    the matching *_raw texts, and pdf_dir (the space's pdf_files folder).
    """
    uuid = str(uuid or "").strip()
    if not uuid or not is_storage_space(space_folder):
        return {}

    from alr.common.artifact_cleanup import prune_touched_folders
    from alr.common.file_manager import DataAnalyzeManager

    MF = DataAnalyzeManager(space_folder)
    paths = {
        "abstract": os.path.join(MF.AD_Abstract, f"{uuid}_Abstract.json"),
        "intro": os.path.join(MF.AD_Intro, f"{uuid}_Intro.json"),
        "rescon": os.path.join(MF.AD_ResCon, f"{uuid}_Results_Conclusion.json"),
        "references": os.path.join(MF.references_subfolder, f"{uuid}_References.json"),
    }
    payloads = {"pdf_dir": str(MF.pdf_subfolder)}
    for name, path in paths.items():
        parsed, raw = _load_json(path)
        payloads[name] = parsed
        payloads[f"{name}_raw"] = raw
    try:
        # The manager mkdirs its tree; drop anything that stayed empty.
        prune_touched_folders()
    except Exception:
        pass
    return payloads


# ---------------------------------------------------------------------------
# Assembling the document view (SQL first, space fills the gaps)
# ---------------------------------------------------------------------------
_IDENTITY_FIELDS = ("uuid", "title", "filename", "relative_path", "source_folder",
                    "timestamp", "time_taken", "created_at", "updated_at")
_STATUS_FIELDS = ("status_sectioning", "status_references", "status_abstract",
                  "status_introduction")
# Registry-row fields shown when the document was never synced to SQL.
_REGISTRY_IDENTITY = ("UUID", "title", "filename", "relative_path", "timestamp",
                      "time_taken")
_REGISTRY_STATUS = ("sectioning", "references", "abstract", "Introduction",
                    "Results_Conclusion")


def _as_text(value) -> str:
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def assemble_document_view(sql_row=None, registry_row=None, payloads=None) -> list:
    """
    Build the full picture of one document as ordered rows
    ``(group, field, value, source)``.

    Policy: SQL values always come first; a field that is empty in SQL is
    filled from the storage-space payloads (marked "Storage space"). Results &
    Conclusion content is never stored in SQL, so it always comes from the
    space. When there is no SQL row at all, identity/status come from the
    registry row (marked "Registry").
    """
    payloads = payloads or {}
    rows = []

    def add(group, field, value, source):
        if _blank(value):
            return
        rows.append((group, field, _as_text(value).strip(), source))

    def add_expanded(group, data, raw, source):
        """Expand a parsed JSON dict into per-key rows; fall back to the raw text."""
        if isinstance(data, dict) and data:
            for key, value in data.items():
                add(group, str(key), value, source)
        elif not _blank(raw):
            add(group, "Raw JSON", raw, source)

    if sql_row:
        for field in _IDENTITY_FIELDS:
            add("Identity", field, sql_row.get(field), SOURCE_SQL)
        for field in _STATUS_FIELDS:
            add("Processing status", field, sql_row.get(field), SOURCE_SQL)

        # Abstract sections: SQL column first, space JSON fills the gap.
        abstract = payloads.get("abstract") or {}
        for key, col in SECTION_COLUMNS.items():
            if not _blank(sql_row.get(col)):
                add("Abstract sections", key, sql_row.get(col), SOURCE_SQL)
            elif isinstance(abstract, dict) and not _blank(abstract.get(key)):
                add("Abstract sections", key, abstract.get(key), SOURCE_SPACE)
        if not _blank(sql_row.get("abstract_text")):
            add("Abstract text", "abstract_text", sql_row.get("abstract_text"), SOURCE_SQL)
        else:
            for key, value in (abstract if isinstance(abstract, dict) else {}).items():
                if key not in SECTION_COLUMNS and not _blank(value):
                    add("Abstract text", key, value, SOURCE_SPACE)

        # Introduction: SQL raw JSON first, else the space JSON.
        intro_raw = sql_row.get("introduction_json")
        if not _blank(intro_raw):
            try:
                add_expanded("Introduction", json.loads(intro_raw), intro_raw, SOURCE_SQL)
            except (json.JSONDecodeError, TypeError, ValueError):
                add("Introduction", "introduction_json", intro_raw, SOURCE_SQL)
        else:
            add_expanded("Introduction", payloads.get("intro"),
                         payloads.get("intro_raw"), SOURCE_SPACE)

        # Results & Conclusion analysis content only lives in the space.
        add_expanded("Results & Conclusion", payloads.get("rescon"),
                     payloads.get("rescon_raw"), SOURCE_SPACE)

        # References: SQL raw JSON first, else the space JSON.
        if not _blank(sql_row.get("references_json")):
            add("References", "references_json", sql_row.get("references_json"), SOURCE_SQL)
        else:
            add("References", "references_json", payloads.get("references_raw"), SOURCE_SPACE)

        for field in ENRICHMENT_COLUMNS:
            add("Enrichment", field, sql_row.get(field), SOURCE_SQL)
        # Custom columns (e.g. user-defined topic classifications).
        for field, value in sql_row.items():
            if field not in _BASE_COLUMNS:
                add("Custom columns", field, value, SOURCE_SQL)
        return rows

    # --- No SQL row: everything comes from the registry + the space JSONs ---
    registry_row = registry_row or {}
    for field in _REGISTRY_IDENTITY:
        add("Identity", field, registry_row.get(field), SOURCE_REGISTRY)
    for field in _REGISTRY_STATUS:
        add("Processing status", field, registry_row.get(field), SOURCE_REGISTRY)

    abstract = payloads.get("abstract")
    add_expanded("Abstract sections", abstract, payloads.get("abstract_raw"), SOURCE_SPACE)
    add_expanded("Introduction", payloads.get("intro"),
                 payloads.get("intro_raw"), SOURCE_SPACE)
    add_expanded("Results & Conclusion", payloads.get("rescon"),
                 payloads.get("rescon_raw"), SOURCE_SPACE)
    add("References", "references_json", payloads.get("references_raw"), SOURCE_SPACE)
    return rows


def missing_after_merge(view_rows) -> list:
    """Groups that ended up with no data at all (helps the UI hint at gaps)."""
    present = {group for group, _f, _v, _s in view_rows}
    wanted = ("Abstract sections", "Introduction", "Results & Conclusion", "References")
    return [g for g in wanted if g not in present]


# ---------------------------------------------------------------------------
# PDF location
# ---------------------------------------------------------------------------
def document_filename(sql_row=None, registry_row=None) -> str:
    for row in (sql_row, registry_row):
        if row and not _blank(row.get("filename")):
            return str(row["filename"]).strip()
    return ""


def candidate_pdf_paths(sql_row=None, registry_row=None, space_folder=None,
                        payloads=None) -> list:
    """
    Known places the PDF could be, in preference order: the recorded
    relative_path, then ``pdf_files/<filename>`` under the SQL source_folder,
    the chosen space and the payloads' pdf_dir. Deduplicated, order kept.
    """
    filename = document_filename(sql_row, registry_row)
    candidates = []
    for row in (sql_row, registry_row):
        rel = (row or {}).get("relative_path")
        if not _blank(rel):
            candidates.append(str(rel))
    bases = [(sql_row or {}).get("source_folder"), space_folder,
             (payloads or {}).get("pdf_dir")]
    if filename:
        for base in bases:
            if base and not _blank(base):
                base = str(base)
                if os.path.basename(os.path.normpath(base)) == "pdf_files":
                    candidates.append(os.path.join(base, filename))
                else:
                    candidates.append(os.path.join(base, "pdf_files", filename))
    seen, out = set(), []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def find_pdf(sql_row=None, registry_row=None, space_folder=None, payloads=None):
    """First existing file among the known candidate paths, or None."""
    for candidate in candidate_pdf_paths(sql_row, registry_row, space_folder, payloads):
        if os.path.isfile(candidate):
            return candidate
    return None


def search_pdf_recursive(root, filename, should_cancel=None, progress_callback=None) -> list:
    """
    Search ``root`` and every nested subfolder for files named ``filename``
    (case-insensitive). ``progress_callback(scanned_dirs, current_dir)`` is
    called per directory; ``should_cancel()`` stops the walk early. Returns
    all matches found.
    """
    target = str(filename or "").strip().lower()
    if not target or not root or not os.path.isdir(root):
        return []
    matches = []
    scanned = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        if should_cancel and should_cancel():
            break
        scanned += 1
        if progress_callback:
            progress_callback(scanned, dirpath)
        for fn in filenames:
            if fn.lower() == target:
                matches.append(os.path.join(dirpath, fn))
    return matches

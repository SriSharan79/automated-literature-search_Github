"""
alr.common.analysis_precheck
============================

Single source of truth for "does this document already have X, and where".

Before the batch pipeline runs an expensive step (Docling extraction, an LLM
classification, a data evaluation, DOI lookup) it consults these helpers to see
whether the result already exists -- either as a file inside the
``DataAnalyzeManager`` storage space *or* as a column in the SQLite store. If it
exists in one space but not the other it can be **copied** across instead of
recomputed; if it exists in neither the step is executed.

The dated-file locators (:func:`find_dated_files_with`, :func:`latest_dated_row`)
also back the user-facing "list the files where a document's data lives" feature
and the "copy from the previous ``{date}`` file" behaviour for classification and
evaluation.

Everything here is read-only and defensive: unreadable/missing files simply mean
"not present" rather than raising.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

# Token analyze_abstract writes into the abstract JSON when no abstract was found.
_ABSTRACT_ERROR_TOKEN = "ERROR_NO_ABSTRACT_FOUND"
# Leading YYYY-MM-DD prefix of the managed dated workbooks.
_DATE_PREFIX_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _nonempty(value) -> bool:
    """True if a SQL/cell value carries real content (not None/blank/'nan')."""
    if value is None:
        return False
    text = str(value).strip()
    return text != "" and text.lower() != "nan"


def _date_key(path: Path):
    """Sort key: (YYYY-MM-DD from the filename or '', mtime) -- newest first later."""
    match = _DATE_PREFIX_RE.search(path.name)
    date_part = match.group(1) if match else ""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (date_part, mtime)


def find_dated_files_with(folder, name_contains, key_col, key_value, sheet_name=None):
    """
    Return the dated ``.xlsx`` workbooks under ``folder`` whose name contains
    ``name_contains`` and that hold a row where ``key_col`` equals ``key_value``.

    Results are sorted newest-first (by the ``YYYY-MM-DD`` filename prefix, then
    modification time). Used both to *list* where a document's data lives and to
    detect prior data for copy-from-previous. ``sheet_name`` selects a specific
    sheet (e.g. the multi-sheet evaluation overview's ``Overview`` tab); the
    default reads the first sheet.
    """
    import pandas as pd

    folder = Path(folder)
    if not folder.is_dir() or not _nonempty(key_value):
        return []

    target = str(key_value).strip()
    matches = []
    for path in folder.glob(f"*{name_contains}*.xlsx"):
        if path.name.startswith("~$"):  # skip Excel lock files
            continue
        try:
            df = pd.read_excel(path, sheet_name=sheet_name) if sheet_name else pd.read_excel(path)
        except Exception:
            continue
        if key_col not in df.columns or df.empty:
            continue
        if target in df[key_col].astype(str).str.strip().values:
            matches.append(path)

    return sorted(matches, key=_date_key, reverse=True)


def latest_dated_row(folder, name_contains, key_col, key_value, sheet_name=None):
    """
    Return ``(path, row_dict)`` for the newest workbook under ``folder`` that
    contains ``key_value`` in ``key_col`` -- the most recent prior result to copy
    from -- or ``(None, None)`` when nothing matches.
    """
    import pandas as pd

    files = find_dated_files_with(folder, name_contains, key_col, key_value, sheet_name=sheet_name)
    if not files:
        return None, None

    target = str(key_value).strip()
    for path in files:  # already newest-first
        try:
            df = pd.read_excel(path, sheet_name=sheet_name) if sheet_name else pd.read_excel(path)
        except Exception:
            continue
        if key_col not in df.columns:
            continue
        hit = df[df[key_col].astype(str).str.strip() == target]
        if not hit.empty:
            return path, hit.iloc[0].to_dict()
    return None, None


def _json_present(path) -> bool:
    """True if a JSON file exists and is non-empty."""
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def _abstract_json_ok(abstract_path) -> bool:
    """True if the abstract JSON exists and carries no 'no abstract' error token."""
    if not _json_present(abstract_path):
        return False
    try:
        with open(abstract_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    if isinstance(data, dict):
        return not any(str(v) == _ABSTRACT_ERROR_TOKEN for v in data.values())
    return True


def _eval_storage_present(MF, uuid) -> bool:
    """True if a dated evaluation overview already records this UUID on disk."""
    try:
        from alr.common.file_manager import Vec_DB_Manager
        VDB = Vec_DB_Manager(MF.folder)
        folder = VDB.Abstract_Overview_folder
    except Exception:
        return False
    return bool(find_dated_files_with(folder, "Abstract_Eval_Overview", "UUID", uuid, sheet_name="Overview"))


def document_status(MF, uuid, filename, doc_row=None) -> dict:
    """
    Report, for one document, whether each analysis / enrichment artifact is
    present in the on-disk **storage space** and in the **SQL** row.

    ``MF`` is a ``DataAnalyzeManager`` (or a folder path). ``doc_row`` is the
    document's SQLite row dict (from ``AnalyzedDataStore.get_document``); when
    omitted the ``*_sql`` flags are all False. Returns a dict of booleans with
    ``<artifact>_storage`` / ``<artifact>_sql`` keys for abstract, intro,
    references, doi, title_class, abstract_class and eval.
    """
    from alr.common.file_manager import DataAnalyzeManager

    if not isinstance(MF, DataAnalyzeManager):
        MF = DataAnalyzeManager(MF)

    fname = str(filename) if filename is not None else ""
    row = doc_row or {}

    status = {
        # --- storage space (on disk) ---
        "abstract_storage": _abstract_json_ok(os.path.join(MF.AD_Abstract, f"{uuid}_Abstract.json")),
        "intro_storage": _json_present(os.path.join(MF.AD_Intro, f"{uuid}_Intro.json")),
        "references_storage": _json_present(os.path.join(MF.references_subfolder, f"{uuid}_References.json")),
        "doi_storage": bool(find_dated_files_with(MF.doi_metadata_subfolder, "DOI_Metadata", "File_Name", fname)),
        "title_class_storage": bool(find_dated_files_with(MF.classification_subfolder, "Title_Classification", "filename", fname)),
        "abstract_class_storage": bool(find_dated_files_with(MF.classification_subfolder, "Abstract_Classification", "filename", fname)),
        "eval_storage": _eval_storage_present(MF, uuid),
        # --- SQL row ---
        "abstract_sql": _nonempty(row.get("abstract_text")),
        "intro_sql": _nonempty(row.get("introduction_json")),
        "references_sql": _nonempty(row.get("references_json")),
        "doi_sql": _nonempty(row.get("doi_link")),
        "title_class_sql": _nonempty(row.get("classification")),
        "abstract_class_sql": _nonempty(row.get("abstract_classification")),
        "eval_sql": _nonempty(row.get("evaluation_score")),
    }
    return status


# ---------------------------------------------------------------------------
# Space-wide completeness ("what is still missing, and can it be reused?")
# ---------------------------------------------------------------------------

# Human labels for the stages reported by :func:`compute_space_gaps`, in the
# order the finalization dialog should show them.
GAP_STAGES: tuple[tuple[str, str], ...] = (
    ("title_class", "Title classification"),
    ("abstract_class", "Abstract classification"),
    ("eval_abstract", "Abstract evaluation"),
    ("eval_intro", "Introduction evaluation"),
    ("eval_rescon", "Results & Conclusion evaluation"),
    ("doi", "DOI / metadata"),
    ("intro_extract", "Introduction extraction"),
    ("references_extract", "References extraction"),
)


def _eval_overview_folders(MF):
    """(folder, name_contains) per evaluation target, or {} if unavailable."""
    try:
        from alr.common.file_manager import Vec_DB_Manager
        VDB = Vec_DB_Manager(MF.folder)
    except Exception:
        return {}
    return {
        "eval_abstract": (VDB.Abstract_Overview_folder, "Abstract_Eval_Overview"),
        "eval_intro": (VDB.Introduction_DB, "Introduction_Eval_Overview"),
        "eval_rescon": (VDB.ResCon_DB, "Results_Conclusion_Eval_Overview"),
    }


def compute_space_gaps(MF, db_path=None) -> dict:
    """
    Report, for a whole storage space, which enrichment stages are **missing from
    SQL** and which of those could be filled by data that already exists on disk
    (a prior dated workbook, or an analysis JSON that simply hasn't been synced).

    Returns ``{stage: {"possible": [uuid, ...], "missing": [uuid, ...],
    "reusable": [uuid, ...], "label": str}}`` for the stages in :data:`GAP_STAGES`,
    **omitting** any stage with nothing missing. ``reusable`` is always a subset of
    ``missing``: those are the documents whose gap can be closed by copying instead
    of re-running an LLM/extraction.

    Drives the finalization dialog: the user picks, per stage, whether to reuse the
    existing data, run the stage fresh, or skip it. Read-only and defensive --
    unreadable files count as "not present".
    """
    from alr.common.file_manager import DataAnalyzeManager
    from alr.common.excel_utils import extract_column, get_corresponding_value
    from alr.common.sql_store import AnalyzedDataStore, DB_PATH

    if not isinstance(MF, DataAnalyzeManager):
        MF = DataAnalyzeManager(MF)

    if not Path(MF.excel_success).exists():
        return {}
    try:
        uuids = extract_column(MF.excel_success, "UUID")
    except Exception:
        return {}

    try:
        store = AnalyzedDataStore(db_path or DB_PATH)
        rows = {str(d.get("uuid")): d for d in store.list_documents()
                if d.get("source_folder") == str(MF.folder)}
    except Exception:
        rows = {}

    eval_folders = _eval_overview_folders(MF)
    gaps = {stage: {"possible": [], "missing": [], "reusable": [], "label": label}
            for stage, label in GAP_STAGES}

    for uuid in uuids:
        uuid = str(uuid)
        row = rows.get(uuid, {})
        try:
            title = get_corresponding_value(MF.excel_success, "UUID", uuid, "title")
            filename = get_corresponding_value(MF.excel_success, "UUID", uuid, "filename")
        except Exception:
            title, filename = None, None
        fname = str(filename) if filename is not None else ""

        MF.update_id_files(uuid)
        has_abstract = _abstract_json_ok(MF.abstract_json_path) or _nonempty(row.get("abstract_text"))
        has_intro = _json_present(MF.intro_json_path)
        has_rescon = _json_present(MF.rescon_json_path)
        has_refs = _json_present(MF.ref_json_path)

        # (stage, prerequisite present, SQL value, reuse probe)
        checks = (
            ("title_class", _nonempty(title) and str(title).strip() != "Title Not Found",
             row.get("classification"),
             lambda: bool(find_dated_files_with(MF.classification_subfolder,
                                                "Title_Classification", "filename", fname))),
            ("abstract_class", has_abstract, row.get("abstract_classification"),
             lambda: bool(find_dated_files_with(MF.classification_subfolder,
                                                "Abstract_Classification", "filename", fname))),
            ("eval_abstract", has_abstract, row.get("evaluation_score"),
             lambda: _eval_reusable(eval_folders, "eval_abstract", uuid)),
            ("eval_intro", has_intro, row.get("intro_evaluation_score"),
             lambda: _eval_reusable(eval_folders, "eval_intro", uuid)),
            ("eval_rescon", has_rescon, row.get("rescon_evaluation_score"),
             lambda: _eval_reusable(eval_folders, "eval_rescon", uuid)),
            ("doi", True, row.get("doi_link"),
             lambda: bool(find_dated_files_with(MF.doi_metadata_subfolder,
                                                "DOI_Metadata", "File_Name", fname))),
            # Extraction gaps are reusable when the analysis JSON is already on
            # disk -- the final sync alone closes them, no re-analysis needed.
            ("intro_extract", True, row.get("introduction_json"), lambda: has_intro),
            ("references_extract", True, row.get("references_json"), lambda: has_refs),
        )

        for stage, possible, sql_value, reuse_probe in checks:
            if not possible:
                continue
            gaps[stage]["possible"].append(uuid)
            if _nonempty(sql_value):
                continue
            gaps[stage]["missing"].append(uuid)
            try:
                if reuse_probe():
                    gaps[stage]["reusable"].append(uuid)
            except Exception:
                pass

    return {stage: info for stage, info in gaps.items() if info["missing"]}


def _eval_reusable(eval_folders, stage, uuid) -> bool:
    """True if a dated evaluation overview for ``stage`` already records ``uuid``."""
    entry = eval_folders.get(stage)
    if not entry:
        return False
    folder, name_contains = entry
    return bool(find_dated_files_with(folder, name_contains, "UUID", uuid, sheet_name="Overview"))


def locate_document_data(MF, uuid, filename):
    """
    List, per enrichment kind, the dated workbooks in the storage space that hold
    this document's data. Returns ``{kind: [Path, ...]}`` (newest-first) for
    ``doi`` / ``title_class`` / ``abstract_class`` / ``eval``. Backs the
    user-facing "which files contain this document's data" feature.
    """
    from alr.common.file_manager import DataAnalyzeManager, Vec_DB_Manager

    if not isinstance(MF, DataAnalyzeManager):
        MF = DataAnalyzeManager(MF)
    fname = str(filename) if filename is not None else ""

    try:
        eval_folder = Vec_DB_Manager(MF.folder).Abstract_Overview_folder
    except Exception:
        eval_folder = MF.folder

    return {
        "doi": find_dated_files_with(MF.doi_metadata_subfolder, "DOI_Metadata", "File_Name", fname),
        "title_class": find_dated_files_with(MF.classification_subfolder, "Title_Classification", "filename", fname),
        "abstract_class": find_dated_files_with(MF.classification_subfolder, "Abstract_Classification", "filename", fname),
        "eval": find_dated_files_with(eval_folder, "Abstract_Eval_Overview", "UUID", uuid, sheet_name="Overview"),
    }

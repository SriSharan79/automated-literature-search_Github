import os
import re
import sys
from pathlib import Path
import pandas as pd

from alr.analysis_evaluation import word_grounding
from alr.analysis_evaluation.per_metric_sheets import GROUNDING_SHEET, open_for
from alr.common import excel_session
from alr.common.excel_utils import Checkpointer, set_cell as _set_cell
from alr.common.file_manager import DataAnalyzeManager, Vec_DB_Manager
from alr.common.sections import build_sections_eval_map, is_literal_match_key
from alr.rag_builders.master_excel_db_builder import (
    FLUSH_EVERY, _append_skiplog, _fetch_metadata, _load_abstract_json,
    _load_recorded_abstracts, _skip_row,
)
from datetime import datetime as dt      # Alias avoids conflicts
# Documents an evaluation pass could not evaluate. One failure never stops the
# pass: the document is skipped and recorded here, beside the overview.
EVAL_SKIPPED_LOG = "Evaluation_not_added.xlsx"
from alr.common.json_utils import get_key_from_file, get_value_by_pair
from alr.rag_builders.vector_db_updater import add_new_strings_to_index, create_faiss_index_cosine, load_index_file, save_index_file, search_similar, vectorize_strings
from colorama import Fore, Style


def _normalize_for_match(text):
    """Lowercase and strip all whitespace so line breaks/spacing don't break the match."""
    return re.sub(r"\s+", "", str(text).lower())


def _is_subset_match(content_str, abs_txt):
    """Whitespace-insensitive, case-insensitive substring containment check."""
    if not _usable_reference(abs_txt):
        return False
    return _normalize_for_match(content_str) in _normalize_for_match(abs_txt)


# What the analysis pipeline writes where it has no content: the section
# locator's "Not Found", the attribute extractor's "No information available",
# and what an absent JSON key or an empty Excel cell reads back as. These are
# markers, not answers.
_PLACEHOLDERS = ("not found", "no information available", "nan", "none", "null")


def _is_placeholder(text):
    """True when a value is one of the pipeline's "nothing here" markers."""
    return str(text or "").strip().lower() in ("",) + _PLACEHOLDERS


def _usable_reference(text):
    """True when a reference text is real content rather than a missing marker.

    ``"Not Found"`` is what the analyzer writes when it could not identify the
    text; matching against it would let the literal words "not" and "found"
    ground themselves.
    """
    return not _is_placeholder(text)


def _grade(key, content_str, reference, vocabulary=None):
    """
    Grounding of one extracted value against the reference text.

    Two regimes, selected by the attribute (:func:`is_literal_match_key`):

    * ``Research Areas`` / ``Key Concepts`` keep the original whole-string
      substring check — one True/False per value.
    * every other attribute is graded **word by word**: articles and
      prepositions are dropped, the remaining content words are each looked up
      in the reference text, and the value's grounding is the percentage of
      them that are present (:mod:`alr.analysis_evaluation.word_grounding`).

    Returns ``(true_count, false_count, columns)`` where ``columns`` are the
    evaluation cells to write beside the value. In the word regime the counts
    are **words**, not values, so the existing per-section totals and the SQL
    evaluation score become graded coverage without changing shape.

    A value the analyzer never produced (``"Not Found"``, ``"No information
    available"``, blank) counts **neither true nor false**: grounding measures
    how well an extracted answer is supported by the source, and there is no
    answer here to support. Counting it as a miss — which is what happened
    before — punished a document for an attribute the extractor left empty and
    dragged its score down for a reason unrelated to grounding quality. The row
    is still written, so the gap stays visible in the sheet; it simply does not
    vote. (:func:`alr.data_analysis.section_resolver.complete_space` is what
    fills those gaps.)
    """
    if _is_placeholder(content_str):
        return 0, 0, {"Is_Subset": False, "Words_Checked": 0, "Words_Found": 0,
                      "Words_Missing": 0, "Grounding_%": None, "Missing_Words": ""}

    if is_literal_match_key(key):
        is_subset = _is_subset_match(content_str, reference)
        return (1 if is_subset else 0), (0 if is_subset else 1), {"Is_Subset": is_subset}

    if not _usable_reference(reference):
        # Nothing to ground against: every content word counts as missing.
        res = word_grounding.grounding(content_str, "", vocabulary=frozenset())
    else:
        res = word_grounding.grounding(content_str, reference, vocabulary=vocabulary)
    return res["true"], res["false"], {
        "Is_Subset": res["grounded"],
        "Words_Checked": res["checked"],
        "Words_Found": res["true"],
        "Words_Missing": res["false"],
        "Grounding_%": res["percent"],
        "Missing_Words": word_grounding.missing_preview(res),
    }


# =====================================================================
# MODULAR EXCEL HELPERS
# =====================================================================

def safe_sheet_title(name):
    """
    Excel worksheet titles are capped at 31 characters and cannot contain
    ``[ ] : * ? / \\``. openpyxl otherwise *silently truncates* a longer name
    (e.g. the Results & Conclusion section "Limitations or Boundary Conditions",
    34 chars), which breaks read-back-by-name: the next run looks up the full
    name, misses the truncated sheet, and appends a duplicate. Normalizing here
    (deterministically) keeps section-sheet writes idempotent and silences the
    "Title is more than 31 characters" warning.
    """
    s = re.sub(r"[\[\]:*?/\\]", "-", str(name)).strip()
    return s[:31] or "Sheet"


def _is_duplicate_in_sheet(file_path, sheet_name, target_uuid):
    """Checks if a UUID already exists in a given Excel sheet."""
    sheet_name = safe_sheet_title(sheet_name)
    try:
        book, owned = excel_session.acquire(file_path, first_sheet="Overview")
        df = book.sheets.get(sheet_name)
        if df is not None and not df.empty and "UUID" in df.columns:
            return target_uuid in df["UUID"].astype(str).values
    except Exception as e:
        print(Fore.YELLOW + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:⚠️ Sheet '{sheet_name}' read error: {e}")
    return False


def _apply_flat_entry(book, sheet_name, data_entry):
    """Insert-or-update one UUID row on ``sheet_name`` of an in-memory book."""
    df_new = pd.DataFrame([data_entry])
    target_uuid = str(data_entry.get("UUID"))
    df_sheet = book.sheets.get(sheet_name)

    if df_sheet is None or df_sheet.empty or "UUID" not in df_sheet.columns:
        df_sheet = df_new if df_sheet is None or df_sheet.empty else pd.concat(
            [df_sheet, df_new], ignore_index=True)
    else:
        df_sheet["UUID"] = df_sheet["UUID"].astype(str)
        match_mask = df_sheet["UUID"] == target_uuid
        if match_mask.any():
            # Update row to dynamically balance any column structure changes
            for col in df_new.columns:
                _set_cell(df_sheet, match_mask, col, df_new[col].values[0])
        else:
            df_sheet = pd.concat([df_sheet, df_new], ignore_index=True)
    book.set_sheet(sheet_name, df_sheet)


def _write_section_sheet_flat(file_path, sheet_name, data_entry):
    """Appends or updates a data entry for section files ensuring single row flat format per UUID.

    Inside an :func:`excel_session.workbook_session` this only touches the
    in-memory image; otherwise the workbook is written straight back, exactly
    as before."""
    try:
        sheet_name = safe_sheet_title(sheet_name)
        book, owned = excel_session.acquire(file_path, first_sheet="Overview")
        _apply_flat_entry(book, sheet_name, data_entry)
        excel_session.release(book, owned)
    except Exception as e:
        print(Fore.RED + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:❌ Failed to write to section sheet '{sheet_name}': {e}")


def _update_master_overview(storage_dir, sheet_name, uuid, title, filename, text_content, true_count, false_count, full_section_entry, overview_path=None):
    """
    Updates the centralized Master Overview tracking true/false count per section
    and bulleted texts. ``overview_path`` selects the workbook (defaults to the
    abstract evaluation overview; the intro evaluation passes its own).
    """
    VDB = Vec_DB_Manager(storage_dir)
    try:
        master_overview_path = Path(overview_path) if overview_path else VDB.Abstract_Eval_Overview
        overview_sheet = "Overview"
        book, owned = excel_session.acquire(master_overview_path, first_sheet=overview_sheet)

        df_overview = book.sheets.get(overview_sheet)
        if df_overview is None or df_overview.empty:
            df_overview = pd.DataFrame(columns=["UUID", "Title", "Filename"])
        df_overview["UUID"] = df_overview["UUID"].astype(str)

        match_mask = df_overview["UUID"] == str(uuid)

        # Dynamic target tracking column assignments
        true_col = f"{sheet_name}_True_Count"
        false_col = f"{sheet_name}_False_Count"
        # Grounding of this attribute: the share of the counted units (words for
        # the prose attributes, whole values for Research Areas / Key Concepts)
        # that were found in the reference text.
        pct_col = f"{sheet_name}_Grounding_%"
        counted = int(true_count) + int(false_count)
        percent = round(100.0 * int(true_count) / counted, 1) if counted else None

        if match_mask.any():
            _set_cell(df_overview, match_mask, sheet_name, text_content)
            _set_cell(df_overview, match_mask, true_col, true_count)
            _set_cell(df_overview, match_mask, false_col, false_count)
            _set_cell(df_overview, match_mask, pct_col, percent)
            if title: _set_cell(df_overview, match_mask, "Title", title)
            if filename: _set_cell(df_overview, match_mask, "Filename", filename)
        else:
            new_row = {
                "UUID": str(uuid),
                "Title": title,
                "Filename": filename,
                sheet_name: text_content,
                true_col: true_count,
                false_col: false_count,
                pct_col: percent,
            }
            df_overview = pd.concat([df_overview, pd.DataFrame([new_row])], ignore_index=True)

        book.set_sheet(overview_sheet, df_overview)

        # Inject or update this section's own tab inside the Master Overview
        # (same in-memory image; 'Overview' is forced first when it is written).
        _apply_flat_entry(book, safe_sheet_title(sheet_name), full_section_entry)
        excel_session.release(book, owned)

    except Exception as e:
        print(Fore.RED + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:❌ Failed to update single Master Overview file: {e}")


# =====================================================================
# CORE PROCESSING LAYER
# =====================================================================


def _sync_sections_for_uuid(UUID, title, file_name, json_data, sections, storage_path,
                            text_key="Abstract Text identified:", overview_path=None,
                            overwrite=False, rows_out=None):
    """
    Iterates through layout mappings to transform and evaluate section lists or
    strings. Returns a stats dict ``{section_key: {"true": t, "false": f}}`` that
    records, per section, how many extracted items were grounded in (a subset of)
    the reference text (``text_key`` -- the identified abstract or introduction
    text). ``overview_path`` selects the master-overview workbook to update.

    ``overwrite=True`` rewrites the Excel rows of already-evaluated UUIDs in
    place with the freshly computed values (used by ``mode="generate"``);
    ``overwrite=False`` (default, previous behaviour) leaves existing rows
    untouched and only appends UUIDs not yet in the sheets.
    """
    stats = {}
    reference_text = json_data.get(text_key, "Not Found")
    # The reference is tokenized ONCE per document; every word-level check below
    # is then a set lookup instead of re-splitting the whole abstract per value.
    vocabulary = (word_grounding.reference_words(reference_text)
                  if _usable_reference(reference_text) else frozenset())
    for key, (ex_path, _) in sections.items():
        content_value = json_data.get(key, "Not Found")

        if isinstance(content_value, list):
            t, f = _save_list_section(
                UUID=UUID,
                key=key,
                content_list=content_value,
                ex_path=ex_path,
                title=title,
                file_name=file_name,
                abs_txt=reference_text,
                storage_path=storage_path,
                overview_path=overview_path,
                overwrite=overwrite,
                vocabulary=vocabulary,
                rows_out=rows_out,
            )
        else:
            t, f = _save_single_section(
                UUID=UUID,
                key=key,
                content_value=content_value,
                ex_path=ex_path,
                title=title,
                file_name=file_name,
                abs_txt=reference_text,
                storage_path=storage_path,
                overview_path=overview_path,
                overwrite=overwrite,
                vocabulary=vocabulary,
                rows_out=rows_out,
            )
        total = t + f
        stats[key] = {
            "true": t, "false": f,
            # "literal" = whole-string substring check (counts are values);
            # "word" = article/preposition-stripped word check (counts are words).
            "mode": "literal" if is_literal_match_key(key) else "word",
            "percent": round(100.0 * t / total, 1) if total else None,
        }
    return stats


def _grounding_row(key, uuid, title, file_name, item_no, content, columns):
    """One row of the per-metric workbook's ``grounding`` sheet.

    ``Check`` records which regime produced it, so a reader knows why the word
    columns are empty on a ``Research Areas`` / ``Key Concepts`` row rather
    than reading the blanks as missing data — and marks the rows that carry no
    extracted value at all, whose zero tally is a non-vote rather than a
    perfect miss.
    """
    row = {
        "UUID": str(uuid), "Title": title, "Filename": file_name,
        "Section": key, "Item": item_no, "Content": content,
        "grounding": columns.get("Is_Subset"),
        "Check": ("not evaluated (no value extracted)" if _is_placeholder(content)
                  else "literal" if is_literal_match_key(key) else "word"),
    }
    for col in ("Words_Checked", "Words_Found", "Words_Missing",
                "Grounding_%", "Missing_Words"):
        if col in columns:
            row[col] = columns[col]
    return row


def _save_list_section(UUID, key, content_list, ex_path, title, file_name, abs_txt, storage_path, overview_path=None, overwrite=False, vocabulary=None, rows_out=None):
    """
    Flattens lists horizontally using alternation layouts and uploads aggregate
    statistics. Always computes the true/false counts (returned for SQL), but
    only writes the Excel sheet when this UUID has not already been evaluated
    -- unless ``overwrite=True``, which updates the existing row in place
    (_write_section_sheet_flat upserts by UUID).

    The counts come from :func:`_grade`: values of ``Research Areas`` /
    ``Key Concepts`` are counted whole (one True/False each), every other
    attribute's items are counted **word by word**.
    """
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
        t, f, columns = _grade(key, content_str, abs_txt, vocabulary)
        true_count += t
        false_count += f

        # Populate alternating structural sequence columns
        count_label = idx + 1
        # entry[f"{key} {count_label}"] = f"{key} {count_label}"
        entry[f"{key} {count_label} value"] = content_str
        for col, val in columns.items():
            entry[f"{key} {count_label} {col}"] = val
        if rows_out is not None:
            rows_out.append(_grounding_row(key, UUID, title, file_name,
                                           count_label, content_str, columns))

        bullet_lines.append(f"- {content_str}")

    # Skip the Excel write if already evaluated (unless overwriting), but
    # still return the counts.
    if overwrite or not _is_duplicate_in_sheet(Path(ex_path), key, str(UUID)):
        _write_section_sheet_flat(Path(ex_path), key, entry)
        aggregate_bullets = "\n ".join(bullet_lines)
        _update_master_overview(storage_path, key, UUID, title, file_name, aggregate_bullets, true_count, false_count, entry, overview_path=overview_path)
        print(Fore.GREEN + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:✅ Evaluated flat list '{key}' ({len(content_list)} items) for UUID: {UUID}")

    return true_count, false_count


def _save_single_section(UUID, key, content_value, ex_path, title, file_name, abs_txt, storage_path, overview_path=None, overwrite=False, vocabulary=None, rows_out=None):
    """
    Saves single text objects cleanly mapping their matching criteria directly.
    Always computes the true/false counts (returned for SQL), but only writes
    the Excel sheet when this UUID has not already been evaluated -- unless
    ``overwrite=True``, which updates the existing row in place.

    For the prose attributes the row now carries the word-level grounding
    beside the value: how many content words were checked, how many were found
    in the reference text, the resulting ``Grounding_%`` and the words that
    were missing (see :func:`_grade`).
    """
    content_str = str(content_value)
    true_count, false_count, columns = _grade(key, content_str, abs_txt, vocabulary)

    entry = {
        "UUID": str(UUID),
        "Title": title,
        "Filename": file_name,
        "Content": content_str,
        **columns,
    }
    if rows_out is not None:
        rows_out.append(_grounding_row(key, UUID, title, file_name, 1,
                                       content_str, columns))

    if overwrite or not _is_duplicate_in_sheet(Path(ex_path), key, str(UUID)):
        _write_section_sheet_flat(Path(ex_path), key, entry)
        # Save overview text and append sectional layout data onto a unique sheet inside Master_Overview.xlsx
        _update_master_overview(storage_path, key, UUID, title, file_name, content_str, true_count, false_count, entry, overview_path=overview_path)
        print(Fore.GREEN + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:✅ Evaluated single_section '{key}' for UUID: {UUID}")

    return true_count, false_count


def _eval_score(stats):
    """Overall grounding from per-section stats: (percent, total_true, total_false).

    The counted units are whatever each attribute counts — words for the prose
    attributes, whole values for Research Areas / Key Concepts — so a document's
    score is the share of all counted units grounded in its reference text.
    """
    total_true = sum(int(v.get("true", 0)) for v in stats.values())
    total_false = sum(int(v.get("false", 0)) for v in stats.values())
    total = total_true + total_false
    percent = round(100.0 * total_true / total, 1) if total else None
    return percent, total_true, total_false


# target -> (SQL json column, SQL score column) for the evaluation summary.
_EVAL_SQL_COLUMNS = {
    "abstract": ("evaluation_json", "evaluation_score"),
    "intro": ("intro_evaluation_json", "intro_evaluation_score"),
    "rescon": ("rescon_evaluation_json", "rescon_evaluation_score"),
}


def _push_eval_to_sql(uuid, stats, db_path=None, target="abstract"):
    """
    Write the evaluation summary (per-section subset counts + overall score) into
    the SQLite document row, if that row exists. ``target`` selects the column
    pair (abstract vs intro). Returns True on update.
    """
    import json as _json
    from alr.common.sql_store import AnalyzedDataStore, DB_PATH

    json_col, score_col = _EVAL_SQL_COLUMNS[target]
    store = AnalyzedDataStore(db_path or DB_PATH)
    if not store.get_document(uuid):
        return False
    percent, _t, _f = _eval_score(stats)
    store.update_document(uuid, {
        json_col: _json.dumps(stats),
        score_col: "" if percent is None else str(percent),
    })
    return True


def _existing_evaluation(uuid, db_path=None, target="abstract"):
    """
    Return the stored per-section stats for ``uuid`` if this document has already
    been evaluated for ``target`` (its SQL score column is present), else ``None``.
    Used by ``mode="copy"`` to reuse a prior evaluation instead of recomputing.
    """
    import json as _json
    from alr.common.sql_store import AnalyzedDataStore, DB_PATH

    json_col, score_col = _EVAL_SQL_COLUMNS[target]
    try:
        store = AnalyzedDataStore(db_path or DB_PATH)
        row = store.get_document(uuid)
    except Exception:
        return None
    if not row:
        return None
    score = row.get(score_col)
    if score is None or str(score).strip() in ("", "nan"):
        return None
    raw = row.get(json_col)
    if raw:
        try:
            return _json.loads(raw)
        except (ValueError, TypeError):
            pass
    return {}  # score present but no detail: still counts as "already evaluated"


def _load_intro_json(MF, uuid):
    """Load {uuid}_Intro.json from the space's Introduction data folder (or None)."""
    import json as _json

    path = Path(os.path.join(MF.AD_Intro, f"{uuid}_Intro.json"))
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _json.load(f)
    except (OSError, _json.JSONDecodeError) as e:
        print(Fore.YELLOW + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:⚠️ Could not read intro JSON for {uuid}: {e}")
        return None


def _load_recorded_intros(MF):
    """UUIDs with a recorded introduction analysis (mirrors _load_recorded_abstracts)."""
    from alr.common.excel_utils import extract_column

    if MF.AD_Intro_log_path and Path(MF.AD_Intro_log_path).exists():
        return extract_column(MF.AD_Intro_log_path, "UUID")
    print(Fore.RED + "Introduction Log is not Available")
    return []


def _load_rescon_json(MF, uuid):
    """Load {uuid}_Results_Conclusion.json from the space's Results & Conclusion folder (or None)."""
    import json as _json

    path = Path(os.path.join(MF.AD_ResCon, f"{uuid}_Results_Conclusion.json"))
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _json.load(f)
    except (OSError, _json.JSONDecodeError) as e:
        print(Fore.YELLOW + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:⚠️ Could not read Results & Conclusion JSON for {uuid}: {e}")
        return None


def _load_recorded_rescons(MF):
    """UUIDs with a recorded Results & Conclusion analysis."""
    from alr.common.excel_utils import extract_column

    if MF.AD_ResCon_log_path and Path(MF.AD_ResCon_log_path).exists():
        return extract_column(MF.AD_ResCon_log_path, "UUID")
    print(Fore.RED + "Results & Conclusion Log is not Available")
    return []


def evaluate_document(storage_path, uuid, db_path=None, push_sql=True, mode="generate",
                      target="abstract", per_metric=None):
    """
    Evaluate one analyzed document (by ``uuid``): write its per-section evaluation
    Excel sheets and, when ``push_sql`` is set, mirror the summary into the SQLite
    store so overviews can use it.

    ``target`` selects what is evaluated: ``"abstract"`` (default) grounds the 7
    abstract sections against the identified abstract text (SQL columns
    ``evaluation_json`` / ``evaluation_score``); ``"intro"`` grounds the analyzed
    introduction keys (Background, Motivation, Gaps & Limitations, RQs & Scope)
    against the identified introduction text (SQL columns
    ``intro_evaluation_json`` / ``intro_evaluation_score``); ``"rescon"``
    grounds the Results & Conclusion keys (Results Mentioned, Limitations or
    Boundary Conditions, Summary of the Content, Future Work, Outlook) against
    the identified results & conclusion text (SQL columns
    ``rescon_evaluation_json`` / ``rescon_evaluation_score``).

    ``mode="copy"`` reuses a prior evaluation when one already exists in SQL
    (skips recomputation; unevaluated documents are still computed);
    ``mode="generate"`` (default) always recomputes AND rewrites the Excel rows
    of already-evaluated UUIDs in place with the fresh values.

    ``per_metric`` is an open
    :class:`~alr.analysis_evaluation.per_metric_sheets.PerMetricWorkbook` (or
    ``None``, the default): when given, this document's per-item grounding rows
    are also buffered onto its ``grounding`` sheet. Only the Evaluation tab
    passes one — the per-document analysis pipeline evaluates one document at a
    time and has no use for a whole-space comparison workbook.

    Returns the per-section stats dict, or ``None`` if the source JSON is missing.
    Safe to call repeatedly: the Excel writes upsert per UUID (no duplicates).
    """
    from alr.common.sections import (
        build_intro_sections_eval_map, build_rescon_sections_eval_map,
        INTRO_TEXT_KEY, ABSTRACT_TEXT_KEY, RESCON_TEXT_KEY,
    )

    if mode == "copy":
        prior = _existing_evaluation(uuid, db_path, target=target)
        if prior is not None:
            print(Fore.YELLOW + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:⏭️ Reusing existing {target} evaluation for {uuid} (copy mode).")
            return prior

    MF = storage_path if isinstance(storage_path, DataAnalyzeManager) else DataAnalyzeManager(storage_path)
    VDB = Vec_DB_Manager(MF.folder)

    if target == "intro":
        sections = build_intro_sections_eval_map(VDB)
        text_key = INTRO_TEXT_KEY
        overview_path = VDB.Introduction_Eval_Overview
        loader = _load_intro_json
    elif target == "rescon":
        sections = build_rescon_sections_eval_map(VDB)
        text_key = RESCON_TEXT_KEY
        overview_path = VDB.ResCon_Eval_Overview
        loader = _load_rescon_json
    else:
        sections = build_sections_eval_map(VDB)
        text_key = ABSTRACT_TEXT_KEY
        overview_path = VDB.Abstract_Eval_Overview
        loader = _load_abstract_json

    MF.update_id_files(uuid)
    title, file_name = _fetch_metadata(MF, uuid)
    json_data = loader(MF, uuid)
    if not json_data:
        return None

    rows_out = [] if per_metric is not None and per_metric.enabled else None
    stats = _sync_sections_for_uuid(
        UUID=uuid, title=title, file_name=file_name,
        json_data=json_data, sections=sections, storage_path=str(MF.folder),
        text_key=text_key, overview_path=overview_path,
        overwrite=(mode == "generate"), rows_out=rows_out,
    )
    if rows_out:
        per_metric.add(GROUNDING_SHEET, uuid, rows_out)
    if push_sql:
        try:
            _push_eval_to_sql(uuid, stats, db_path, target=target)
        except Exception as e:
            print(Fore.YELLOW + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:⚠️ Could not push {target} evaluation to SQL for {uuid}: {e}")
    return stats


def _eval_workbooks(MF, target):
    """``(overview_path, [per-section eval workbook paths])`` for one target."""
    from alr.common.sections import (
        build_intro_sections_eval_map, build_rescon_sections_eval_map,
    )
    VDB = Vec_DB_Manager(MF.folder)
    if target == "intro":
        sections, overview = build_intro_sections_eval_map(VDB), VDB.Introduction_Eval_Overview
    elif target == "rescon":
        sections, overview = build_rescon_sections_eval_map(VDB), VDB.ResCon_Eval_Overview
    else:
        sections, overview = build_sections_eval_map(VDB), VDB.Abstract_Eval_Overview
    return Path(overview), [Path(ex) for ex, _key in sections.values()]


def evaluate_space(storage_path, db_path=None, progress_callback=None, should_cancel=None,
                   mode="generate", target="abstract", per_metric=False) -> int:
    """
    Evaluate every analyzed document in a storage space (build the evaluation Excel
    DBs and sync the summary into SQLite). Returns the number of documents evaluated.

    ``target`` selects the data evaluated: ``"abstract"`` (default, documents from
    the abstract log), ``"intro"`` (documents from the introduction log) or
    ``"rescon"`` (documents from the Results & Conclusion log).
    ``mode="copy"`` reuses prior evaluations already recorded in SQL (skips
    recompute); ``mode="generate"`` (default) recomputes. ``progress_callback(done,
    total)`` is called after each document if given, and ``should_cancel`` is
    checked before each for cooperative cancellation.

    ``per_metric=True`` additionally fills the ``grounding`` sheet of the
    target's per-metric workbook (``{date}_..._Per_Metric.xlsx``): one row per
    extracted item across every section and every document, the whole-space
    counterpart of the per-section eval sheets. It is written by the Evaluation
    tab only — the analysis pipeline's per-document evaluation leaves it alone.
    """
    from contextlib import ExitStack

    MF = storage_path if isinstance(storage_path, DataAnalyzeManager) else DataAnalyzeManager(storage_path)

    recorded = {"intro": _load_recorded_intros,
                "rescon": _load_recorded_rescons}.get(target, _load_recorded_abstracts)(MF)
    if not recorded:
        return 0

    overview_path, section_paths = _eval_workbooks(MF, target)

    count = 0
    total = len(recorded)
    checkpointer = Checkpointer()
    # Documents that could not be evaluated; the pass carries on and these end
    # up in EVAL_SKIPPED_LOG next to the overview.
    not_added = []

    with ExitStack() as stack:
        # Every evaluation workbook is written in one batch instead of a full
        # read+rewrite per section per document (quadratic, and what made a
        # long evaluation pass look frozen).
        books = [stack.enter_context(excel_session.workbook_session(p, first_sheet="Overview"))
                 for p in [overview_path, *section_paths]]
        # One sheet per metric, filled only when the caller asked for it; the
        # buffer is flushed on the same checkpoint as the workbooks below.
        metric_book = stack.enter_context(
            open_for(Vec_DB_Manager(MF.folder), target, enabled=per_metric))
        for i, uuid in enumerate(recorded, 1):
            if should_cancel is not None and should_cancel():
                print("Evaluation cancelled by user.")
                break
            try:
                evaluate_document(MF, uuid, db_path=db_path, push_sql=True, mode=mode,
                                  target=target, per_metric=metric_book)
                count += 1
            except Exception as e:
                print(Fore.YELLOW + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:⚠️ {target.title()} evaluation failed for {uuid}: {e} "
                                    f"— skipped, continuing with the next document.")
                not_added.append(_skip_row(f"evaluate-{target}", e, uuid=uuid))
            # Checkpoint so a crash or a cancel keeps the work done so far.
            # Checkpoint so a crash or a cancel keeps the work done so far —
            # but not so often that rewriting the (growing) workbooks costs
            # more than the evaluation itself; see excel_utils.Checkpointer.
            if checkpointer.due(i):
                print(f"💾 Checkpointing evaluation workbooks at document {i}/{total}…")
                for book in books:
                    try:
                        book.flush()
                    except Exception as e:  # noqa: BLE001 - never stop the pass on a checkpoint
                        print(Fore.RED + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:❌ Could not checkpoint {book.path}: {e}")
                metric_book.flush()
            if progress_callback:
                progress_callback(i, total)

    print(Fore.GREEN + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:✅ Evaluated {count} document(s) ({target}); results synced to SQL.")
    if not_added:
        log_path = _append_skiplog(Path(overview_path).parent / EVAL_SKIPPED_LOG, not_added)
        print(Fore.YELLOW + Style.BRIGHT
              + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:⚠️ {len(not_added)} document(s) were NOT evaluated"
              + (f" — see {log_path}" if log_path else "") + Style.RESET_ALL)
    return count


def generate_databases(Storage_path):
    """Back-compatible entry point: evaluate the whole space (Excel DBs + SQL sync)."""
    return evaluate_space(Storage_path)


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
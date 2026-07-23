"""
alr.analysis_evaluation.publication_classification.classify_runner
=================================================================

Bridge the standalone publication-classification logic into the analysis flow.
``classify_space`` classifies every analyzed document in a storage space by its
title (reusing :func:`title_classifier.classify_title`), writes the managed
``Publication_Classification.xlsx`` and stores a summary in the SQLite store's
``classification`` column.
"""

from __future__ import annotations


def _load_existing_classification(excel_path):
    """
    If ``excel_path`` already exists and has rows, return
    ``(existing_rows, existing_filenames)`` -- a list-of-dict copy of the
    current rows (for re-saving/merging) and the set of filenames already
    present. Returns ``([], set())`` if the file doesn't exist, is empty, or
    can't be read.
    """
    import pandas as pd
    from pathlib import Path

    if not Path(excel_path).exists():
        return [], set()
    try:
        df = pd.read_excel(excel_path)
    except Exception:
        return [], set()
    if df.empty:
        return [], set()
    filenames = set(df.get("filename", pd.Series(dtype=str)).dropna().astype(str))
    return df.to_dict("records"), filenames


def has_existing_classification(manager, kind="title") -> int:
    """
    Return how many documents already have classification data saved in the
    managed workbook -- ``Publication_Classification.xlsx`` for
    ``kind="title"``, or ``Abstract_Classification.xlsx`` for
    ``kind="abstract"``. 0 means there's nothing saved yet (no need to prompt
    about overwriting vs. continuing).
    """
    from alr.common.file_manager import DataAnalyzeManager

    if not isinstance(manager, DataAnalyzeManager):
        manager = DataAnalyzeManager(manager)

    excel_path = manager.classification_excel if kind == "title" else manager.abstract_classification_excel
    _, existing_filenames = _load_existing_classification(excel_path)
    return len(existing_filenames)


# Per-kind wiring: which managed workbook, dated-file marker, SQL column, source
# text field and classifier function each classification kind uses.
def _kind_config(manager, kind):
    from alr.analysis_evaluation.publication_classification.title_classifier import (
        classify_title, classify_abstract,
    )
    if kind == "abstract":
        return {
            "excel_path": manager.abstract_classification_excel,
            "name_contains": "Abstract_Classification",
            "sql_col": "abstract_classification",
            "text_field": "abstract_text",
            "classify_fn": classify_abstract,
        }
    return {
        "excel_path": manager.classification_excel,
        "name_contains": "Title_Classification",
        "sql_col": "classification",
        "text_field": "title",
        "classify_fn": classify_title,
    }


def _text_is_valid(kind, text):
    if text is None:
        return False
    text = str(text).strip()
    if not text:
        return False
    if kind == "title" and text == "Title Not Found":
        return False
    return True


def _append_classification_row(excel_path, row):
    """
    Merge one classification row into the (dated) workbook, keyed by ``filename``:
    replace an existing row for that file, otherwise append. Writing per-row keeps
    today's dated file accumulating as documents are processed one at a time, and
    leaves prior dated files untouched.
    """
    import pandas as pd
    from pathlib import Path

    existing = []
    if Path(excel_path).exists():
        try:
            existing = pd.read_excel(excel_path).to_dict("records")
        except Exception:
            existing = []
    fname = str(row.get("filename"))
    merged = [r for r in existing if str(r.get("filename")) != fname]
    merged.append(row)
    pd.DataFrame(merged).to_excel(excel_path, index=False)


def copy_classification_from_previous(manager, filename, title, kind="title", db_path=None, uuid=None):
    """
    Reuse a prior dated classification result for one document instead of calling
    the LLM. Looks up the newest ``*_{kind}_Classification.xlsx`` row for
    ``filename``; if found, copies it into **today's** dated workbook and pushes
    the summary into SQL. Returns the copied row dict, or ``None`` if no prior
    result exists (caller should then classify it fresh).
    """
    from alr.common.file_manager import DataAnalyzeManager
    from alr.common.sql_store import AnalyzedDataStore, DB_PATH
    from alr.common.analysis_precheck import latest_dated_row
    from alr.analysis_evaluation.publication_classification.title_classifier import TAXONOMY_TOPICS

    if not isinstance(manager, DataAnalyzeManager):
        manager = DataAnalyzeManager(manager)
    cfg = _kind_config(manager, kind)

    _, prev = latest_dated_row(manager.classification_subfolder, cfg["name_contains"], "filename", filename)
    if not prev:
        return None

    result = {t: bool(prev.get(t)) for t in TAXONOMY_TOPICS if t in prev}
    true_topics = [t for t, v in result.items() if v]

    if uuid:
        store = AnalyzedDataStore(db_path or DB_PATH)
        store.update_document(uuid, {cfg["sql_col"]: ", ".join(true_topics)})

    out_row = {"filename": filename, "title": title, **result}
    _append_classification_row(cfg["excel_path"], out_row)
    return out_row


def classify_document(manager, doc, kind="title", db_path=None, service=None, mode="generate", store=None) -> bool:
    """
    Classify a single document (``doc`` is a SQLite row dict with ``uuid`` /
    ``filename`` / ``title`` / ``abstract_text``) and persist the result.

    ``mode="copy"`` first tries :func:`copy_classification_from_previous` and only
    calls the LLM when no prior dated result exists. ``mode="generate"`` always
    LLM-classifies. Either way the result is written to today's dated workbook and
    the SQL summary column. Returns True if something was written.
    """
    from alr.common.file_manager import DataAnalyzeManager
    from alr.common.sql_store import AnalyzedDataStore, DB_PATH

    if not isinstance(manager, DataAnalyzeManager):
        manager = DataAnalyzeManager(manager)
    cfg = _kind_config(manager, kind)
    filename = doc.get("filename")
    title = doc.get("title")
    uuid = doc.get("uuid")

    if mode == "copy":
        copied = copy_classification_from_previous(manager, filename, title, kind, db_path=db_path, uuid=uuid)
        if copied is not None:
            return True  # reused a prior result; no LLM call

    text = doc.get(cfg["text_field"])
    if not _text_is_valid(kind, text):
        return False

    result = cfg["classify_fn"](text, service=service) or {}  # {topic: bool}
    true_topics = [t for t, v in result.items() if v]

    store = store or AnalyzedDataStore(db_path or DB_PATH)
    if uuid:
        store.update_document(uuid, {cfg["sql_col"]: ", ".join(true_topics)})
    _append_classification_row(cfg["excel_path"], {"filename": filename, "title": title, **result})
    return True


def _run_classification(manager, kind, db_path=None, progress_callback=None,
                        should_cancel=None, overwrite=True, service=None, mode=None) -> int:
    from alr.common.file_manager import DataAnalyzeManager
    from alr.common.sql_store import AnalyzedDataStore, DB_PATH

    if not isinstance(manager, DataAnalyzeManager):
        manager = DataAnalyzeManager(manager)
    cfg = _kind_config(manager, kind)

    store = AnalyzedDataStore(db_path or DB_PATH)
    docs = [d for d in store.list_documents() if d.get("source_folder") == str(manager.folder)]

    # When not overwriting, skip documents already present in today's workbook
    # (used by the Tab-5 "continue" option). Copy mode reuses prior data instead
    # of the LLM, so it is safe to visit every document.
    if not overwrite and (mode or "generate") != "copy":
        _, existing_filenames = _load_existing_classification(cfg["excel_path"])
        if existing_filenames:
            docs = [d for d in docs if str(d.get("filename")) not in existing_filenames]

    updated = 0
    total = len(docs)
    for i, d in enumerate(docs, 1):
        if should_cancel is not None and should_cancel():
            print(f"{kind.title()} classification cancelled by user.")
            break
        if classify_document(manager, d, kind=kind, db_path=db_path, service=service,
                             mode=mode or "generate", store=store):
            updated += 1
        if progress_callback:
            progress_callback(i, total)

    print(f"{kind.title()} classification updated {updated} document(s).")
    return updated


def classify_space(manager, db_path=None, progress_callback=None, should_cancel=None,
                   overwrite=True, service=None, mode=None) -> int:
    """
    Classify each document in a storage space by title and persist the result
    (SQLite ``classification`` column + managed dated ``Title_Classification.xlsx``).

    ``manager`` is a DataAnalyzeManager (or folder path). Returns the number of
    documents classified. Requires an API key for the chosen ``service`` ('B' =
    Blablador, 'C' = Chat AI, 'O' = DLR Ollama).

    ``mode`` controls reuse: ``"copy"`` copies each document's newest prior dated
    classification (no LLM) and only classifies genuinely-new documents;
    ``"generate"`` (or ``None``) always LLM-classifies. ``overwrite=False`` (only
    meaningful with generate) skips documents already present in today's workbook.
    ``progress_callback(done, total)`` / ``should_cancel`` support progress and
    cooperative cancellation (partial results are saved).
    """
    return _run_classification(manager, "title", db_path=db_path, progress_callback=progress_callback,
                               should_cancel=should_cancel, overwrite=overwrite, service=service, mode=mode)


def classify_abstract_space(manager, db_path=None, progress_callback=None, should_cancel=None,
                            overwrite=True, service=None, mode=None) -> int:
    """
    Classify each document by its **identified abstract text** and persist the
    result (SQLite ``abstract_classification`` column + managed dated
    ``Abstract_Classification.xlsx``). Reads the ``abstract_text`` column the
    storage sync populated from each abstract JSON.

    Independent of title classification. ``mode`` / ``overwrite`` / ``service`` /
    ``progress_callback`` / ``should_cancel`` behave as in :func:`classify_space`.
    """
    return _run_classification(manager, "abstract", db_path=db_path, progress_callback=progress_callback,
                               should_cancel=should_cancel, overwrite=overwrite, service=service, mode=mode)


def classify_custom_space(manager, topic, tags, source="title", db_path=None,
                          progress_callback=None, should_cancel=None, service=None) -> int:
    """
    Classify every analyzed document in a storage space against a
    **user-defined** tag taxonomy (custom classification prompt built from
    ``tags``; see ``title_classifier.build_custom_classifier_prompt``).

    * ``topic``   -- the user's topic tag. It names the dated workbook
      (``{date}_{Topic}_Classification.xlsx`` in the space's classification
      folder) and the SQLite column the summary is recorded under (sanitized;
      behaves like ``classification`` / ``abstract_classification``).
    * ``tags``    -- list of classification tags the LLM must decide true/false.
    * ``source``  -- "title" or "abstract": which document text is classified.

    Returns the number of documents classified. Requires an API key for the
    chosen ``service``.
    """
    import re
    from datetime import datetime
    from alr.common.file_manager import DataAnalyzeManager
    from alr.common.sql_store import AnalyzedDataStore, DB_PATH, register_custom_column
    from alr.analysis_evaluation.publication_classification.title_classifier import classify_custom

    if not isinstance(manager, DataAnalyzeManager):
        manager = DataAnalyzeManager(manager)

    # Strip whitespace AND surrounding quote characters, so tags pasted as a
    # quoted list ('"Hybrid Powertrain", "Electric Motor", ...') classify the
    # same as plain comma-separated ones.
    tags = [str(t).strip().strip('\'"“”‘’').strip() for t in tags]
    tags = [t for t in tags if t]
    if not tags:
        print("Custom classification: no tags given; nothing to do.")
        return 0

    # The user's topic tag names both the SQL column and the dated workbook.
    sql_col = register_custom_column(topic, db_path=db_path)
    safe_topic = re.sub(r"[^A-Za-z0-9_\- ]+", "", str(topic).strip()).strip().replace(" ", "_") or sql_col
    current_date = datetime.now().strftime("%Y-%m-%d")
    excel_path = str(manager.classification_subfolder / f"{current_date}_{safe_topic}_Classification.xlsx")

    text_field = "abstract_text" if source == "abstract" else "title"
    kind = "abstract" if source == "abstract" else "title"
    source_label = "Abstract" if source == "abstract" else "Title"

    store = AnalyzedDataStore(db_path or DB_PATH)
    docs = [d for d in store.list_documents() if d.get("source_folder") == str(manager.folder)]

    updated = 0
    total = len(docs)
    for i, d in enumerate(docs, 1):
        if should_cancel is not None and should_cancel():
            print("Custom classification cancelled by user.")
            break
        text = d.get(text_field)
        if _text_is_valid(kind, text):
            result = classify_custom(text, tags, topic=topic, service=service,
                                     source_label=source_label)
            true_tags = [t for t, v in result.items() if v]
            if d.get("uuid"):
                store.update_document(d["uuid"], {sql_col: ", ".join(true_tags)})
            _append_classification_row(
                excel_path, {"filename": d.get("filename"), "title": d.get("title"), **result})
            updated += 1
        if progress_callback:
            progress_callback(i, total)

    print(f"Custom classification '{topic}' updated {updated} document(s) "
          f"-> {excel_path} (SQL column: {sql_col}).")
    return updated


def question_score_space(manager, source="registry", download_log=None, output_excel=None,
                         progress_callback=None):
    """
    Run the question-scored publication classification for a storage space.

    This is an **on-demand** operation only (never part of the automatic analysis
    flow). Each title/publication name is scored against the full question set,
    producing a multi-sheet workbook (a Summary sheet plus one sheet per section
    with per-question True/False and a score).

    ``manager`` is a DataAnalyzeManager (or a folder path). ``source`` selects the
    input:

    * ``"registry"``     -> the ``title`` column of ``Processed_file_registry.xlsx``.
    * ``"download_log"`` -> the ``Publication Name`` column of the download-log
      Excel given by ``download_log``.

    Output defaults to the managed ``question_classification_excel`` path inside
    the space. Returns the output workbook path (or ``None`` on failure).
    Requires a Blablador API key.
    """
    import importlib
    from pathlib import Path
    from alr.common.file_manager import DataAnalyzeManager

    # The module filename contains an apostrophe, so it must be imported by name.
    q_logic = importlib.import_module(
        "alr.analysis_evaluation.publication_classification.Classification_logic_with_Q's"
    )

    if not isinstance(manager, DataAnalyzeManager):
        manager = DataAnalyzeManager(manager)

    if source == "download_log":
        if not download_log or not Path(download_log).exists():
            print("No valid download-log Excel provided for question scoring.")
            return None
        file_path = str(download_log)
        column_name = "Publication Name"
    else:  # registry
        if not Path(manager.excel_success).exists():
            print("No processed-file registry found; nothing to score.")
            return None
        file_path = str(manager.excel_success)
        column_name = "title"

    output_excel = str(output_excel or manager.question_classification_excel)
    print(f"Question-scored classification: '{column_name}' from {file_path} -> {output_excel}")
    q_logic.classify_excel_data_to_sheets(
        file_path=file_path, column_name=column_name, output_file_path=output_excel,
        progress_callback=progress_callback
    )
    return output_excel
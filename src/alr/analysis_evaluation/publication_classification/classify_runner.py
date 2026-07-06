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


def classify_space(manager, db_path=None, progress_callback=None, should_cancel=None, overwrite=True) -> int:
    """
    Classify each document in a storage space by title and persist the result.

    ``manager`` is a DataAnalyzeManager (or a folder path). Returns the number of
    documents classified. Requires a Blablador API key (classify_title uses it);
    individual titles that fail fall back to an all-False result.

    ``progress_callback(done, total)`` is called after each document if given.
    ``should_cancel`` is an optional callable checked before each document for
    cooperative cancellation (partial results are saved).

    If ``Publication_Classification.xlsx`` already has rows for this space:

    * ``overwrite=True`` (default) reclassifies every matching document and
      replaces the workbook from scratch.
    * ``overwrite=False`` skips documents whose filename is already present in
      the workbook and only classifies the remaining ones, merging the new
      rows into the existing data. Use :func:`has_existing_classification` to
      check beforehand and let the user choose.
    """
    import pandas as pd
    from alr.common.file_manager import DataAnalyzeManager
    from alr.common.sql_store import AnalyzedDataStore, DB_PATH
    from alr.analysis_evaluation.publication_classification.title_classifier import classify_title

    if not isinstance(manager, DataAnalyzeManager):
        manager = DataAnalyzeManager(manager)

    store = AnalyzedDataStore(db_path or DB_PATH)
    docs = [d for d in store.list_documents() if d.get("source_folder") == str(manager.folder)]

    rows, existing_filenames = ([], set()) if overwrite else _load_existing_classification(manager.classification_excel)
    if existing_filenames:
        docs = [d for d in docs if str(d.get("filename")) not in existing_filenames]

    updated = 0
    total = len(docs)
    for i, d in enumerate(docs, 1):
        if should_cancel is not None and should_cancel():
            print("Classification cancelled by user.")
            break
        title = d.get("title")
        if title and str(title).strip() not in ("", "Title Not Found"):
            result = classify_title(title)  # {topic: bool}
            true_topics = [t for t, v in (result or {}).items() if v]
            store.update_document(d["uuid"], {"classification": ", ".join(true_topics)})
            rows.append({"filename": d.get("filename"), "title": title, **(result or {})})
            updated += 1
        if progress_callback:
            progress_callback(i, total)

    if rows:
        pd.DataFrame(rows).to_excel(manager.classification_excel, index=False)
    print(f"Classification updated {updated} document(s).")
    return updated


def classify_abstract_space(manager, db_path=None, progress_callback=None, should_cancel=None, overwrite=True) -> int:
    """
    Classify each document in a storage space by its **identified abstract text**
    (reusing :func:`title_classifier.classify_abstract`) and persist the result in
    the SQLite store's ``abstract_classification`` column, plus a managed
    ``Abstract_Classification.xlsx`` workbook.

    Independent of title classification: it reads the ``abstract_text`` column that
    the storage sync populated from each document's abstract JSON. Returns the
    number of documents classified. Requires a Blablador API key.

    ``progress_callback(done, total)`` is called after each document if given, and
    ``should_cancel`` is checked before each document for cooperative cancellation.

    If ``Abstract_Classification.xlsx`` already has rows for this space:

    * ``overwrite=True`` (default) reclassifies every matching document and
      replaces the workbook from scratch.
    * ``overwrite=False`` skips documents whose filename is already present in
      the workbook and only classifies the remaining ones, merging the new
      rows into the existing data. Use :func:`has_existing_classification`
      (``kind="abstract"``) to check beforehand and let the user choose.
    """
    import pandas as pd
    from alr.common.file_manager import DataAnalyzeManager
    from alr.common.sql_store import AnalyzedDataStore, DB_PATH
    from alr.analysis_evaluation.publication_classification.title_classifier import classify_abstract

    if not isinstance(manager, DataAnalyzeManager):
        manager = DataAnalyzeManager(manager)

    store = AnalyzedDataStore(db_path or DB_PATH)
    docs = [d for d in store.list_documents() if d.get("source_folder") == str(manager.folder)]

    rows, existing_filenames = ([], set()) if overwrite else _load_existing_classification(manager.abstract_classification_excel)
    if existing_filenames:
        docs = [d for d in docs if str(d.get("filename")) not in existing_filenames]

    updated = 0
    total = len(docs)
    for i, d in enumerate(docs, 1):
        if should_cancel is not None and should_cancel():
            print("Abstract classification cancelled by user.")
            break
        abstract_text = d.get("abstract_text")
        if abstract_text and str(abstract_text).strip():
            result = classify_abstract(abstract_text)  # {topic: bool}
            true_topics = [t for t, v in (result or {}).items() if v]
            store.update_document(d["uuid"], {"abstract_classification": ", ".join(true_topics)})
            rows.append({"filename": d.get("filename"), "title": d.get("title"), **(result or {})})
            updated += 1
        if progress_callback:
            progress_callback(i, total)

    if rows:
        pd.DataFrame(rows).to_excel(manager.abstract_classification_excel, index=False)
    print(f"Abstract classification updated {updated} document(s).")
    return updated


def question_score_space(manager, source="registry", download_log=None, output_excel=None):
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
        file_path=file_path, column_name=column_name, output_file_path=output_excel
    )
    return output_excel
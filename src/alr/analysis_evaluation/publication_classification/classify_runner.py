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


def classify_space(manager, db_path=None, progress_callback=None, should_cancel=None) -> int:
    """
    Classify each document in a storage space by title and persist the result.

    ``manager`` is a DataAnalyzeManager (or a folder path). Returns the number of
    documents classified. Requires a Blablador API key (classify_title uses it);
    individual titles that fail fall back to an all-False result.

    ``progress_callback(done, total)`` is called after each document if given.
    ``should_cancel`` is an optional callable checked before each document for
    cooperative cancellation (partial results are saved).
    """
    import pandas as pd
    from alr.common.file_manager import DataAnalyzeManager
    from alr.common.sql_store import AnalyzedDataStore, DB_PATH
    from alr.analysis_evaluation.publication_classification.title_classifier import classify_title

    if not isinstance(manager, DataAnalyzeManager):
        manager = DataAnalyzeManager(manager)

    store = AnalyzedDataStore(db_path or DB_PATH)
    docs = [d for d in store.list_documents() if d.get("source_folder") == str(manager.folder)]

    rows = []
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


def classify_abstract_space(manager, db_path=None, progress_callback=None, should_cancel=None) -> int:
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
    """
    import pandas as pd
    from alr.common.file_manager import DataAnalyzeManager
    from alr.common.sql_store import AnalyzedDataStore, DB_PATH
    from alr.analysis_evaluation.publication_classification.title_classifier import classify_abstract

    if not isinstance(manager, DataAnalyzeManager):
        manager = DataAnalyzeManager(manager)

    store = AnalyzedDataStore(db_path or DB_PATH)
    docs = [d for d in store.list_documents() if d.get("source_folder") == str(manager.folder)]

    rows = []
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

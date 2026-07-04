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


def classify_space(manager, db_path=None) -> int:
    """
    Classify each document in a storage space by title and persist the result.

    ``manager`` is a DataAnalyzeManager (or a folder path). Returns the number of
    documents classified. Requires a Blablador API key (classify_title uses it);
    individual titles that fail fall back to an all-False result.
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
    for d in docs:
        title = d.get("title")
        if not title or str(title).strip() in ("", "Title Not Found"):
            continue
        result = classify_title(title)  # {topic: bool}
        true_topics = [t for t, v in (result or {}).items() if v]
        store.update_document(d["uuid"], {"classification": ", ".join(true_topics)})
        rows.append({"filename": d.get("filename"), "title": title, **(result or {})})
        updated += 1

    if rows:
        pd.DataFrame(rows).to_excel(manager.classification_excel, index=False)
    print(f"Classification updated {updated} document(s).")
    return updated

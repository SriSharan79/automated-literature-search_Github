"""
alr.common.download_log_enrich
==============================

Identify bibliographic metadata for analyzed documents by scanning **all**
``*_download_log.xlsx`` workbooks under a root folder and fuzzy-matching each
document's title against the log's ``Publication Name`` column (or, failing that,
its filename against a file-name column). Matched metadata (link, authors,
first author, publication year) is written into the SQLite store for
documents that do not already have those values.

This complements :meth:`AnalyzedDataStore.merge_download_log`, which only does an
exact ``File_Name`` -> ``filename`` join.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd


def _norm(text) -> str:
    if text is None:
        return ""
    t = re.sub(r"[^a-z0-9\s]", " ", str(text).lower())
    return re.sub(r"\s+", " ", t).strip()


def _find_col(columns, *candidates):
    """Return the first column whose normalized name matches any candidate."""
    norm_map = {re.sub(r"[^a-z0-9]", "", str(c).lower()): c for c in columns}
    for cand in candidates:
        key = re.sub(r"[^a-z0-9]", "", cand.lower())
        if key in norm_map:
            return norm_map[key]
    return None


# download-log column (normalized-name candidates) -> document column
_METADATA_MAP = [
    (("link", "url", "doilink"), "link"),
    (("authors", "author"), "authors"),
    (("firstauthor",), "first_author"),
    (("publicationyear", "year"), "publication_year"),
]


def _collect_log_rows(logs):
    """Read every download-log workbook into a single list of normalized row dicts."""
    rows = []
    for log_path in logs:
        try:
            df = pd.read_excel(log_path)
        except Exception as e:
            print(f"⚠️ Could not read download log {log_path}: {e}")
            continue

        pub_col = _find_col(df.columns, "Publication Name", "Title")
        file_col = _find_col(df.columns, "File_Name", "File Name", "Filename")
        meta_cols = {}
        for candidates, db_col in _METADATA_MAP:
            col = _find_col(df.columns, *candidates)
            if col is not None:
                meta_cols[db_col] = col

        for _, r in df.iterrows():
            fields = {}
            for db_col, src_col in meta_cols.items():
                val = r.get(src_col)
                if val is not None and str(val).strip() and str(val).lower() != "nan":
                    fields[db_col] = str(val).strip()
            rows.append({
                "pub_name": str(r.get(pub_col)) if pub_col else "",
                "pub_norm": _norm(r.get(pub_col)) if pub_col else "",
                "file_name": str(r.get(file_col)) if file_col else "",
                "fields": fields,
                "source": Path(log_path).name,
            })
    return rows


def enrich_from_download_logs(root, db_path=None, threshold: int = 88,
                              progress_callback=None, should_cancel=None) -> int:
    """
    Scan all ``*_download_log.xlsx`` files under ``root`` and enrich documents in
    the SQLite store by fuzzy-matching their title against each log's Publication
    Name (falling back to a filename match). Only empty metadata fields are filled.
    Returns the number of documents updated.
    """
    from rapidfuzz import fuzz, process as rf_process
    from alr.common.storage_scanner import find_download_logs
    from alr.common.sql_store import AnalyzedDataStore, DB_PATH

    logs = find_download_logs(root)
    if not logs:
        print(f"No *_download_log.xlsx files found under {root}.")
        return 0
    print(f"🔎 Scanning {len(logs)} download-log file(s) for metadata matches...")

    log_rows = _collect_log_rows(logs)
    if not log_rows:
        return 0

    # Index for fuzzy matching by publication name, and a direct filename map.
    pub_choices = {i: r["pub_norm"] for i, r in enumerate(log_rows) if r["pub_norm"]}
    by_filename = {r["file_name"]: r for r in log_rows if r["file_name"]}

    store = AnalyzedDataStore(db_path or DB_PATH)
    docs = store.list_documents()
    total = len(docs)
    updated = 0

    for idx, doc in enumerate(docs, 1):
        if should_cancel is not None and should_cancel():
            print("Download-log enrichment cancelled by user.")
            break

        match_row = None
        # 1. Exact filename match against a log file-name column.
        if doc.get("filename") and doc["filename"] in by_filename:
            match_row = by_filename[doc["filename"]]
        # 2. Fuzzy title <-> Publication Name.
        if match_row is None and pub_choices:
            title_norm = _norm(doc.get("title"))
            if len(title_norm) >= 10:
                best = rf_process.extractOne(
                    title_norm, pub_choices, scorer=fuzz.token_sort_ratio)
                if best and best[1] >= threshold:
                    match_row = log_rows[best[2]]

        if match_row and match_row["fields"]:
            # Only fill fields the document is currently missing.
            fields = {c: v for c, v in match_row["fields"].items()
                      if not str(doc.get(c) or "").strip()}
            if fields:
                store.update_document(doc["uuid"], fields)
                updated += 1
                print(f"  ✅ {doc.get('filename')} <- {match_row['source']} ({', '.join(fields)})")

        if progress_callback:
            progress_callback(idx, total)

    print(f"Download-log enrichment updated {updated} document(s).")
    return updated

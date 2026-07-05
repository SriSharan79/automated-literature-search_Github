"""
alr.data_analysis.batch_dedup
=============================

Before batch-processing a folder of PDFs, detect documents whose *title* (as
extracted from the PDF) fuzzy-matches a document that was **already analyzed**
(recorded in the storage space's ``Processed_file_registry.xlsx``) or another PDF
earlier in the same batch. Such duplicates are skipped and their filenames are
logged to the managed ``Skipped_Duplicates.xlsx`` workbook, so the batch never
re-analyzes the same content under a different filename.

The heavy title extraction (:func:`title_extracter.get_title_in_the_file`) and the
document-processing pipeline are imported lazily so importing this module stays
cheap.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd


def _normalize_title(title) -> str:
    """Lowercase, strip punctuation and collapse whitespace for robust matching."""
    if title is None:
        return ""
    text = re.sub(r"[^a-z0-9\s]", " ", str(title).lower())
    return re.sub(r"\s+", " ", text).strip()


def _is_usable_title(title: str) -> bool:
    """A title is usable for matching only if it is present and specific enough."""
    norm = _normalize_title(title)
    if len(norm) < 10:
        return False
    if norm in {"title not found", "no metadata title", "metadata not found",
                "title identification failed"}:
        return False
    return True


def find_new_and_duplicate_pdfs(
    source_folder,
    storage_path=None,
    threshold: int = 88,
    llm_service: str = "o",
    progress_callback=None,
    should_cancel=None,
):
    """
    Split the PDFs under ``source_folder`` into those to process and those to skip
    as duplicates.

    A candidate PDF is a duplicate when its extracted title fuzzy-matches (ratio
    >= ``threshold``) either a title already in the registry (previously analyzed)
    or a title accepted earlier in this same batch, or when its filename already
    exists in the registry. Returns ``(to_process, skipped)`` where ``to_process``
    is a list of ``Path`` and ``skipped`` is a list of dicts describing each skip.

    ``progress_callback(done, total)`` is called after each PDF if given, and
    ``should_cancel`` is an optional callable checked before each PDF.
    """
    from rapidfuzz import fuzz, process as rf_process
    from alr.common.file_manager import DataAnalyzeManager
    from alr.data_analysis.title_extracter import get_title_in_the_file

    manager = storage_path if isinstance(storage_path, DataAnalyzeManager) else DataAnalyzeManager(storage_path)
    if llm_service is None:
        llm_service = manager.llm_service or "o"

    # Titles/filenames already analyzed (from the registry).
    known_titles = {}   # normalized title -> original title
    known_files = set()
    if Path(manager.excel_success).exists():
        try:
            df = pd.read_excel(manager.excel_success)
            if "filename" in df.columns:
                known_files = {str(f) for f in df["filename"].dropna().tolist()}
            if "title" in df.columns:
                for t in df["title"].dropna().tolist():
                    if _is_usable_title(t):
                        known_titles[_normalize_title(t)] = str(t)
        except Exception as e:
            print(f"⚠️ Could not read registry for dedup ({e}); treating all files as new.")

    pdfs = sorted(Path(source_folder).rglob("*.pdf"))
    total = len(pdfs)
    to_process, skipped = [], []
    batch_titles = {}   # normalized title accepted in this batch -> filename

    for i, pdf in enumerate(pdfs, 1):
        if should_cancel is not None and should_cancel():
            print("Duplicate scan cancelled by user.")
            break

        # Filename already analyzed -> skip immediately (no title extraction cost).
        if pdf.name in known_files:
            skipped.append({"filename": pdf.name, "path": str(pdf), "title": "",
                            "matched_against": "registry (same filename)",
                            "matched_value": pdf.name, "score": 100})
            if progress_callback:
                progress_callback(i, total)
            continue

        try:
            title = get_title_in_the_file(pdf, llm_service)
        except Exception as e:
            print(f"⚠️ Title extraction failed for {pdf.name} ({e}); will process it.")
            title = None

        norm = _normalize_title(title)
        matched = None
        if _is_usable_title(title):
            # Match against registry titles first, then titles seen in this batch.
            for pool, source_label in ((known_titles, "registry"), (batch_titles, "this batch")):
                if not pool:
                    continue
                best = rf_process.extractOne(norm, list(pool.keys()), scorer=fuzz.token_sort_ratio)
                if best and best[1] >= threshold:
                    matched = (source_label, pool[best[0]] if pool is known_titles else best[0], best[1])
                    break

        if matched:
            source_label, matched_value, score = matched
            skipped.append({"filename": pdf.name, "path": str(pdf), "title": str(title),
                            "matched_against": source_label, "matched_value": str(matched_value),
                            "score": round(float(score), 1)})
            print(f"⏩ Skipping duplicate: {pdf.name} (title ~ '{matched_value}' in {source_label}, {score:.0f}%)")
        else:
            to_process.append(pdf)
            if _is_usable_title(title):
                batch_titles[norm] = pdf.name

        if progress_callback:
            progress_callback(i, total)

    if skipped:
        try:
            pd.DataFrame(skipped).to_excel(manager.duplicate_log_excel, index=False)
            print(f"📝 Logged {len(skipped)} skipped duplicate(s) to {manager.duplicate_log_excel}")
        except Exception as e:
            print(f"⚠️ Could not write duplicate log: {e}")

    return to_process, skipped


def batch_process_folder(
    source_path,
    storage_path="",
    skip_duplicates: bool = True,
    threshold: int = 88,
    mode: str = "a",
    llm_service: str = None,
    progress_callback=None,
    should_cancel=None,
):
    """
    Process every PDF under ``source_path`` with no page limit, optionally skipping
    fuzzy-title duplicates first. Returns a summary dict with counts and the list of
    skipped duplicates. Files are processed via
    :func:`Pdf_File_processor.process_pdf_mode_file`.
    """
    from alr.common.file_manager import DataAnalyzeManager
    from alr.data_analysis.Pdf_File_processor import process_pdf_mode_file

    manager = storage_path if isinstance(storage_path, DataAnalyzeManager) else DataAnalyzeManager(storage_path)
    svc = llm_service or manager.llm_service or "o"

    if skip_duplicates:
        to_process, skipped = find_new_and_duplicate_pdfs(
            source_path, manager, threshold=threshold, llm_service=svc,
            progress_callback=progress_callback, should_cancel=should_cancel,
        )
    else:
        to_process = sorted(Path(source_path).rglob("*.pdf"))
        skipped = []

    print(f"\n📦 Batch: {len(to_process)} file(s) to process, {len(skipped)} skipped as duplicates.")
    processed = 0
    for pdf in to_process:
        if should_cancel is not None and should_cancel():
            print("Batch processing cancelled by user.")
            break
        process_pdf_mode_file(str(pdf), str(manager.folder), mode)
        processed += 1

    return {"processed": processed, "skipped": skipped, "to_process": len(to_process)}

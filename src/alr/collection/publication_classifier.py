"""
Classify a collected publications list against the user's keyword tags.

This reuses the **custom classification** core (`classify_custom`, the same
LLM-tag workflow the Data-Analysis tab uses) but operates on the collection
space's publications workbook instead of analyzed documents, and writes the
result back into the **collection space** (``classified_publications/``) — not
into any Data-Analysis folder.

Each publication is classified twice: its ``Publication Name`` (title) and its
``Abstract``. The output workbook keeps the original publication columns and
adds, per keyword tag, a boolean column (true when the tag matched in the title
OR the abstract), plus readable ``Matched Keywords (Title)`` /
``Matched Keywords (Abstract)`` summary columns.
"""
from datetime import datetime
from pathlib import Path

import pandas as pd

TITLE_COLUMN = "Publication Name"
ABSTRACT_COLUMN = "Abstract"


def _valid_text(value):
    if value is None:
        return False
    text = str(value).strip()
    return bool(text) and text.lower() not in ("n/a", "nan", "none")


def classify_publications_list(CM, tags, service=None,
                               progress_callback=None, should_cancel=None):
    """
    Classify the publications in ``CM.publications_list_excel`` against ``tags``
    (the user's selected keywords) and write a dated classified workbook into
    the collection space's ``classified_publications/`` folder.

    Returns ``(count, output_path)`` where ``count`` is the number of
    publications classified; ``(0, None)`` when there is nothing to do.
    ``progress_callback(done, total)`` reports the per-publication loop;
    ``should_cancel()`` stops early but still writes what was classified.
    """
    from alr.analysis_evaluation.publication_classification.title_classifier import classify_custom

    # Clean the tags the same way classify_custom_space does (strip quotes).
    tags = [str(t).strip().strip('\'"“”‘’').strip() for t in (tags or [])]
    tags = [t for t in tags if t]
    if not tags:
        print("Publication classification: no keyword tags given; nothing to do.")
        return 0, None

    CM.ensure_folders()
    pub_path = Path(CM.publications_list_excel)
    if not pub_path.exists():
        print(f"Publication classification: no publications workbook at {pub_path}.")
        return 0, None

    df = pd.read_excel(pub_path)
    if df.empty:
        print("Publication classification: the publications list is empty.")
        return 0, None

    topic = getattr(CM, "Research_Area", None) or "the given taxonomy"
    rows = []
    total = len(df)
    for i, (_, pub) in enumerate(df.iterrows(), 1):
        if should_cancel is not None and should_cancel():
            print("Publication classification cancelled by user; saving what was done.")
            break

        title = pub.get(TITLE_COLUMN)
        abstract = pub.get(ABSTRACT_COLUMN)
        # Skip rows with nothing to classify (no usable title AND no abstract).
        if not _valid_text(title) and not _valid_text(abstract):
            if progress_callback:
                progress_callback(i, total)
            continue

        title_res = (classify_custom(title, tags, topic=topic, service=service,
                                     source_label="Title")
                     if _valid_text(title) else {})
        abstract_res = (classify_custom(abstract, tags, topic=topic, service=service,
                                        source_label="Abstract")
                        if _valid_text(abstract) else {})

        title_tags = [t for t in tags if title_res.get(t)]
        abstract_tags = [t for t in tags if abstract_res.get(t)]

        row = pub.to_dict()
        # One boolean column per tag: matched in the title OR the abstract.
        for t in tags:
            row[t] = bool(title_res.get(t) or abstract_res.get(t))
        row["Matched Keywords (Title)"] = ", ".join(title_tags)
        row["Matched Keywords (Abstract)"] = ", ".join(abstract_tags)
        rows.append(row)

        if progress_callback:
            progress_callback(i, total)

    if not rows:
        return 0, None

    current_date = datetime.now().strftime("%Y-%m-%d")
    base = Path(CM.classified_publications_excel).name if getattr(
        CM, "classified_publications_excel", None) else "classified_publications.xlsx"
    out_path = CM.classified_publications_folder / f"{current_date}_{base}"
    pd.DataFrame(rows).to_excel(out_path, index=False)

    print(f"Classified {len(rows)} publication(s) against {len(tags)} keyword tag(s) "
          f"-> {out_path}")
    return len(rows), str(out_path)

"""
Read previously generated keyword / search-phrase files back into plain Python
lists, so the Collect-Literature tab can re-display and re-use them.

Two artifact shapes are produced by the collection pipeline:

* Keywords  -> ``*_keywords_list.json`` (a list of timestamped entries, each with
  a ``generated_keywords`` list) written by ``log_Keyword_Json``; or any
  ``.xlsx``/``.csv`` with a keyword-like column.
* Phrases   -> ``*_search_phrase_list.xlsx`` (a ``Phrase`` column plus ``*_Rank``
  columns) written by ``Keywords_Processing_with_scope``; the sorted export has
  the same ``Phrase`` column.

The readers are deliberately tolerant: they accept the canonical files but fall
back to sensible column guesses so a hand-made list still imports.
"""
import json
from pathlib import Path

import pandas as pd

# Column names we will accept as "the keyword column" / "the phrase column",
# in priority order (case-insensitive).
_KEYWORD_COLUMNS = ("keyword", "keywords", "generated_keywords")
_PHRASE_COLUMNS = ("phrase", "phrases", "search phrase", "search_phrase")


def _dedupe_preserve_order(items):
    seen, out = set(), []
    for it in items:
        text = str(it).strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
    return out


def _pick_column(df, candidates):
    lower = {str(c).strip().lower(): c for c in df.columns}
    for name in candidates:
        if name in lower:
            return lower[name]
    return None


def read_keywords_file(path):
    """
    Return a de-duplicated list of keywords from a keywords JSON or a
    spreadsheet. For the canonical JSON (list of entries) the *most recent*
    entry's ``generated_keywords`` are used. Raises ValueError with a readable
    message when nothing keyword-like is found.
    """
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data if isinstance(data, list) else [data]
        # Walk newest-first so the latest generated set wins.
        for entry in reversed(entries):
            if isinstance(entry, dict) and entry.get("generated_keywords"):
                return _dedupe_preserve_order(entry["generated_keywords"])
        # A bare JSON list of strings is also acceptable.
        if entries and all(isinstance(e, str) for e in entries):
            return _dedupe_preserve_order(entries)
        raise ValueError("No 'generated_keywords' found in the JSON file.")

    if suffix in (".xlsx", ".xls", ".csv"):
        df = pd.read_csv(path) if suffix == ".csv" else pd.read_excel(path)
        col = _pick_column(df, _KEYWORD_COLUMNS)
        if col is None:
            raise ValueError(
                "No keyword column found (expected one of: "
                + ", ".join(_KEYWORD_COLUMNS) + ").")
        return _dedupe_preserve_order(df[col].dropna().tolist())

    raise ValueError(f"Unsupported keyword file type: {suffix or '(none)'}")


def read_phrases_file(path, rank_column=None):
    """
    Return ``[(rank, phrase), ...]`` from a search-phrase workbook/CSV.

    ``rank_column`` (e.g. ``'TOTAL_Rank'``) is used when present; otherwise the
    first ``*_Rank`` column is used, and failing that every phrase gets rank
    ``'-'``. Ranks are returned as strings (ints where whole) so the caller can
    drop them straight into the table. Rows are de-duplicated on the phrase.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in (".xlsx", ".xls", ".csv"):
        raise ValueError(f"Unsupported phrase file type: {suffix or '(none)'}")

    df = pd.read_csv(path) if suffix == ".csv" else pd.read_excel(path)
    phrase_col = _pick_column(df, _PHRASE_COLUMNS)
    if phrase_col is None:
        raise ValueError(
            "No phrase column found (expected one of: "
            + ", ".join(_PHRASE_COLUMNS) + ").")

    # Resolve the rank column: requested -> first *_Rank -> none.
    lower = {str(c).strip().lower(): c for c in df.columns}
    resolved_rank = None
    if rank_column and rank_column.lower() in lower:
        resolved_rank = lower[rank_column.lower()]
    else:
        for c in df.columns:
            if str(c).strip().lower().endswith("_rank"):
                resolved_rank = c
                break

    rows, seen = [], set()
    for _, row in df.iterrows():
        phrase = str(row[phrase_col]).strip()
        if not phrase or phrase.lower() == "nan" or phrase.lower() in seen:
            continue
        seen.add(phrase.lower())
        if resolved_rank is not None and pd.notna(row.get(resolved_rank)):
            val = row[resolved_rank]
            rank = str(int(val)) if float(val).is_integer() else str(val)
        else:
            rank = "-"
        rows.append((rank, phrase))
    return rows

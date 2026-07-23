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


# Keyword fields recorded per JSON entry (new schema), in the order they are
# merged into the unique universe.
_ENTRY_KEYWORD_FIELDS = ("suggested_keywords", "user_added_keywords",
                         "selected_keywords", "generated_keywords")
# The field naming the user's actual selection, newest-first preference.
_SELECTION_FIELDS = ("selected_keywords", "generated_keywords")


def read_keywords_record(path):
    """
    Return ``(unique_keywords, last_selected)`` from a keywords file:

    * ``unique_keywords`` -- every distinct keyword the file records (across all
      timestamped entries: LLM-suggested + user-added + selected), in first-seen
      order.
    * ``last_selected``   -- the set of keywords the user selected in the *most
      recent* entry (case-insensitive lookup against ``unique_keywords``).

    For a spreadsheet (no selection history) every keyword is treated as
    selected. Raises ValueError when nothing keyword-like is found.
    """
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data if isinstance(data, list) else [data]

        # A bare JSON list of strings -> all unique, all selected.
        if entries and all(isinstance(e, str) for e in entries):
            uniq = _dedupe_preserve_order(entries)
            return uniq, set(k.lower() for k in uniq)

        dict_entries = [e for e in entries if isinstance(e, dict)]
        if not dict_entries:
            raise ValueError("No keyword entries found in the JSON file.")

        # Union of every recorded keyword across all entries, first-seen order.
        universe = []
        for entry in dict_entries:
            for field in _ENTRY_KEYWORD_FIELDS:
                universe.extend(entry.get(field) or [])
        unique = _dedupe_preserve_order(universe)
        if not unique:
            raise ValueError("The JSON file records no keywords.")

        # Selection from the newest entry that actually names one.
        last_selected = []
        for entry in reversed(dict_entries):
            for field in _SELECTION_FIELDS:
                if entry.get(field):
                    last_selected = entry[field]
                    break
            if last_selected:
                break
        return unique, set(str(k).strip().lower() for k in last_selected)

    if suffix in (".xlsx", ".xls", ".csv"):
        df = pd.read_csv(path) if suffix == ".csv" else pd.read_excel(path)
        col = _pick_column(df, _KEYWORD_COLUMNS)
        if col is None:
            raise ValueError(
                "No keyword column found (expected one of: "
                + ", ".join(_KEYWORD_COLUMNS) + ").")
        unique = _dedupe_preserve_order(df[col].dropna().tolist())
        return unique, set(k.lower() for k in unique)

    raise ValueError(f"Unsupported keyword file type: {suffix or '(none)'}")


def read_keywords_file(path):
    """Backward-compatible helper: just the unique keyword list."""
    return read_keywords_record(path)[0]


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

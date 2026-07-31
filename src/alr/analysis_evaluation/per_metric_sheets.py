"""
alr.analysis_evaluation.per_metric_sheets
=========================================

One workbook per target holding **one sheet per individual metric** — the
adaptation of the data-extraction repo's ``column_evaluator.write_metric_sheets``
to this repo's data.

The established metric workbooks are organised the other way round: a workbook
per metric *kind* (lexical / distance / cosine), a sheet per *section*, and one
row per extracted item carrying every metric of that kind side by side. That is
the right shape for reading one document's attributes, and the wrong one for
answering "how does ROUGE-L behave across the whole space?" — the values are
spread over seven sheets in three files.

This module writes the transposed view into ONE dated workbook per target
(``{date}_Abstract_Per_Metric.xlsx`` and its Introduction / Results &
Conclusion counterparts), with a sheet per metric::

    grounding | jaccard | rouge1 | rouge2 | rougeL | bleu
    levenshtein_distance | similarity_ratio | word_error_rate | cosine_similarity

Every sheet has the same shape — ``UUID | Title | Filename | Section | Item |
Content`` plus that metric's value and the reference sentence that produced it
(the word tally, for grounding) — so one sheet is directly sortable,
filterable and chartable across every document and every section of the space.

Nothing here recomputes anything: the callers already hold these values.
:mod:`data_evaluator` contributes the ``grounding`` sheet from the substring
pass, :mod:`metric_evaluator` the nine metric sheets from its per-item rows.

**Written only by the Evaluation tab.** The per-document analysis pipeline
(Tab 1) runs the grounding evaluation too, one document at a time; it does not
open this workbook, because a whole-space comparison sheet assembled one row at
a time during analysis would be rewritten once per document for no benefit.
Both entry points therefore default to ``per_metric=False``.

Writes are **buffered**: rows accumulate in memory and reach disk on
:meth:`PerMetricWorkbook.flush` (the caller checkpoints every N documents) and
on :meth:`close`, so a ten-sheet workbook is rewritten a handful of times per
pass instead of once per document per section. A flush replaces exactly the
buffered documents' rows on each touched sheet and leaves every other row —
and every sheet it did not touch — alone, so the grounding pass and the metric
passes can fill the same workbook independently and in any order.
"""

from __future__ import annotations

from pathlib import Path

from colorama import Fore

# Sheet order in the workbook: grounding first (it is the one every pass can
# produce), then the metric columns in the order the kinds are evaluated.
GROUNDING_SHEET = "grounding"
METRIC_SHEETS = (
    GROUNDING_SHEET,
    "jaccard", "rouge1", "rouge2", "rougeL", "bleu",
    "levenshtein_distance", "similarity_ratio", "word_error_rate",
    "cosine_similarity",
)

# Columns every sheet opens with, in order.
BASE_COLUMNS = ("UUID", "Title", "Filename", "Section", "Item", "Content")


def _ordered(sheets):
    """Known sheets in canonical order, then anything unexpected, sorted."""
    known = [s for s in METRIC_SHEETS if s in sheets]
    return known + sorted(s for s in sheets if s not in METRIC_SHEETS)


class PerMetricWorkbook:
    """
    Buffered writer for one target's per-metric workbook.

    ``enabled=False`` makes every method a no-op, so callers can hold one
    unconditionally and let the flag decide whether the workbook exists at
    all — an unwanted workbook is never created, not even empty.
    """

    def __init__(self, path, enabled=True):
        self.path = Path(path) if path else None
        self.enabled = bool(enabled) and self.path is not None
        self._buffer = {}       # sheet -> [row dict, …]
        self._uuids = {}        # sheet -> {uuid, …} whose rows are being replaced
        self.written = False

    # ------------------------------------------------------------------ add --
    def add(self, sheet, uuid, rows):
        """Buffer ``rows`` as part of document ``uuid``'s content of ``sheet``.

        Repeated calls for the same (document, sheet) accumulate — the pass
        adds one section at a time — and the next flush replaces that
        document's rows on disk with everything buffered since the last one.
        The only rule is therefore: add **all** of a document's sections
        before flushing, or the flush would drop the sections added earlier.
        """
        if not self.enabled or not rows:
            return
        self._buffer.setdefault(sheet, []).extend(rows)
        self._uuids.setdefault(sheet, set()).add(str(uuid))

    # ---------------------------------------------------------------- write --
    def flush(self):
        """Write every buffered sheet in ONE workbook rewrite and clear the buffer."""
        if not self.enabled or not self._buffer:
            return
        import pandas as pd

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            sheets = {}
            if self.path.exists() and self.path.stat().st_size > 0:
                with pd.ExcelFile(self.path, engine="openpyxl") as xls:
                    for name in xls.sheet_names:
                        sheets[name] = pd.read_excel(self.path, sheet_name=name,
                                                     engine="openpyxl")

            for sheet, rows in self._buffer.items():
                df_new = pd.DataFrame(rows, columns=_row_columns(rows))
                existing = sheets.get(sheet)
                if existing is not None and not existing.empty and "UUID" in existing.columns:
                    # Replace exactly this pass's documents; everything else stays.
                    keep = ~existing["UUID"].astype(str).isin(self._uuids.get(sheet, set()))
                    existing = existing[keep]
                    df_new = pd.concat([existing, df_new], ignore_index=True)
                sheets[sheet] = df_new

            with pd.ExcelWriter(self.path, engine="openpyxl", mode="w") as writer:
                for name in _ordered(sheets):
                    sheets[name].to_excel(writer, sheet_name=name, index=False)
            self.written = True
        except Exception as e:  # noqa: BLE001 - a reporting workbook never fails a pass
            print(Fore.RED + f"❌ Could not write the per-metric workbook {self.path}: {e}")
        finally:
            self._buffer.clear()
            self._uuids.clear()

    def close(self):
        """Final flush; returns the workbook path if anything was written."""
        self.flush()
        if self.written:
            print(Fore.GREEN + f"   📄 {self.path}")
            return str(self.path)
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _row_columns(rows):
    """Union of the rows' keys, base columns first, then first-seen order."""
    seen = list(BASE_COLUMNS)
    for row in rows:
        for key in row:
            if key not in seen:
                seen.append(key)
    return [c for c in seen if any(c in row for row in rows)]


def open_for(vdb, target, enabled=True):
    """The per-metric workbook of one target, from the workbook registry."""
    from alr.common.sections import build_metric_workbooks_map

    path = build_metric_workbooks_map(vdb, target).get("per_metric")
    return PerMetricWorkbook(path, enabled=enabled)

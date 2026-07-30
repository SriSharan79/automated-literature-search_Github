"""
alr.common.skiplog
==================

One shared shape for the "could not be processed" logs every long pass writes.

A failure on a single document must never abort a pass: the document is skipped,
one row describing the failure is recorded, and the pass continues with the next
file. Each pass keeps its own log file (``Common_DB_not_added.xlsx``,
``Master_Excel_not_added.xlsx``, ``Evaluation_not_added.xlsx``,
``Query_not_added.xlsx``, ``Analysis_not_added.xlsx``), all written through
:func:`append_skiplog`, which is **append-only** — a re-run adds to the history
instead of erasing the previous run's evidence.

The RAG builders re-export these helpers under their old private names, so the
existing ``from alr.rag_builders.master_excel_db_builder import _skip_row``
imports keep working.
"""

from datetime import datetime
from pathlib import Path

import pandas as pd
from colorama import Fore, Style

# Documents the per-document analysis pass could not take through the pipeline.
ANALYSIS_SKIPPED_LOG = "Analysis_not_added.xlsx"


def skip_row(stage, error, space=None, uuid="", title="", filename="", sections=""):
    """One row of a 'not added' log: what failed, on which document, and when."""
    return {
        "UUID": uuid, "Title": title, "Filename": filename,
        "Source_Folder": str(space) if space else "",
        "Stage": stage, "Sections": sections,
        "Error": f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error),
        "Run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def append_skiplog(log_path, skipped_rows):
    """Append this run's failures to a skip log (never overwrites earlier
    runs). Returns the log path, or None when nothing was written."""
    if not skipped_rows:
        return None
    log_path = Path(log_path)
    try:
        rows = list(skipped_rows)
        if log_path.exists() and log_path.stat().st_size > 0:
            try:
                rows = pd.read_excel(log_path).to_dict("records") + rows
            except Exception:
                pass  # unreadable old log: start a fresh one from this run
        log_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_excel(log_path, index=False)
        return log_path
    except Exception as e:
        print(Fore.RED + f"❌ Could not write the skip log {log_path}: {e}" + Style.RESET_ALL)
        return None

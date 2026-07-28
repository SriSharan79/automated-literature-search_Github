"""
alr.common.excel_session
========================

Batched read/modify/write for the multi-sheet Excel workbooks this app keeps
updating one row at a time (the master DB workbook, the evaluation overviews).

Without batching every single entry re-reads EVERY sheet of the workbook and
writes the whole file back. That is quadratic in the size of the workbook: a
pass gets slower the longer it runs and, on a few thousand documents, looks
like the application has frozen.

Two ways to use it:

* ``with workbook_session(path):`` around a whole pass — every write inside
  goes to one in-memory image, written out once when the session closes
  (``session.flush()`` checkpoints it in between).
* :func:`acquire` / :func:`release` inside a writer — it joins the active
  session when there is one, and otherwise behaves exactly as before
  (read the file, change it, write it back immediately).

Sessions are reference-counted per workbook path, so a writer may open one
without knowing whether an outer one already exists.
"""

from pathlib import Path

import pandas as pd
from colorama import Fore

_SESSIONS = {}   # str(path) -> [Book, refcount]


class Book:
    """In-memory image of one workbook: ``sheets`` {name: DataFrame} + order."""

    def __init__(self, path, first_sheet=None):
        self.path = Path(path)
        self.first_sheet = first_sheet
        self.sheets = {}
        self.order = []
        self.dirty = False
        self._load()

    def _load(self):
        if self.path.exists() and self.path.stat().st_size > 0:
            try:
                with pd.ExcelFile(self.path, engine="openpyxl") as xls:
                    self.order = list(xls.sheet_names)
                    for name in self.order:
                        self.sheets[name] = pd.read_excel(
                            self.path, sheet_name=name, engine="openpyxl")
            except Exception as e:
                print(Fore.YELLOW + f"⚠️ Workbook read error for {self.path.name} "
                                    f"(will attempt overwrite): {e}")
                self.sheets, self.order = {}, []

    def sheet(self, name, columns=None):
        """The named sheet, created empty (with ``columns``) if it is new."""
        df = self.sheets.get(name)
        if df is None:
            df = pd.DataFrame(columns=list(columns or []))
            self.sheets[name] = df
            if name not in self.order:
                self.order.append(name)
        return df

    def set_sheet(self, name, df):
        self.sheets[name] = df
        if name not in self.order:
            self.order.append(name)
        self.dirty = True

    def flush(self):
        """Write the workbook out if anything changed."""
        if not self.dirty:
            return
        order = [s for s in self.order if s in self.sheets]
        if self.first_sheet and self.first_sheet in self.sheets:
            order = [self.first_sheet] + [s for s in order if s != self.first_sheet]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with pd.ExcelWriter(self.path, engine="openpyxl", mode="w") as writer:
            for name in order:
                self.sheets[name].to_excel(writer, sheet_name=name, index=False)
        self.dirty = False


class workbook_session:
    """Context manager batching every write to one workbook (see module doc)."""

    def __init__(self, path, first_sheet=None):
        self.key = str(Path(path))
        self.path = path
        self.first_sheet = first_sheet

    def __enter__(self) -> Book:
        entry = _SESSIONS.get(self.key)
        if entry is None:
            entry = [Book(self.path, first_sheet=self.first_sheet), 0]
            _SESSIONS[self.key] = entry
        entry[1] += 1
        return entry[0]

    def __exit__(self, exc_type, exc, tb):
        entry = _SESSIONS.get(self.key)
        if entry is None:
            return False
        entry[1] -= 1
        if entry[1] <= 0:
            del _SESSIONS[self.key]
            try:
                entry[0].flush()
            except Exception as e:  # noqa: BLE001 - report, never mask the body's error
                print(Fore.RED + f"❌ Failed to write {entry[0].path}: {e}")
        return False


def active(path):
    """The open session's Book for ``path``, or None."""
    entry = _SESSIONS.get(str(Path(path)))
    return entry[0] if entry else None


def acquire(path, first_sheet=None):
    """
    ``(book, owned)`` for a writer: the active session's book (owned=False) or
    a freshly read one (owned=True) that :func:`release` writes out at once.
    """
    book = active(path)
    if book is not None:
        return book, False
    return Book(path, first_sheet=first_sheet), True


def release(book, owned):
    """Write a book acquired with ``owned=True``; session books stay in memory."""
    if owned:
        book.flush()

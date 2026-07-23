"""
alr.ui.desktop.review_app
========================

Standalone Review application. A separate window (launched from the main app's
"Open Review Tool" button, or run on its own via ``review_main.py``) with three
tabs:

  1. Storage Spaces  - recognize DataAnalyzeManager storage spaces under a chosen
     folder (complete/partial), link them into the SQLite DB, run DOI/metadata
     and publication-classification enrichment, and import download-log data.
  2. Documents       - the existing editable document review (ReviewDataView).
  3. Overviews       - build custom overviews (field-picker + filters) and export.
"""

import csv
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from alr.common import crash_logger

from alr.common.document_inspector import (
    SEARCH_MODES,
    assemble_document_view,
    document_filename,
    find_pdf,
    find_registry_documents,
    is_storage_space,
    load_space_payloads,
    lookup_sql_documents,
    missing_after_merge,
    search_pdf_recursive,
)
from alr.common.sql_store import AnalyzedDataStore, sync_storage_to_sql, COLUMNS
from alr.common.storage_scanner import detect_storage_spaces, find_download_logs
from alr.common.file_manager import ALR_overviews_folder
from alr.common.LLM_Config import get_stored_api_key
from alr.ui.desktop.review_view import ReviewDataView, open_path

# Sensible default columns to show pre-checked in the overview builder.
DEFAULT_OVERVIEW_FIELDS = [
    "title", "filename", "publication_year", "publication_type",
    "classification", "abstract_classification", "first_author", "doi_link",
    "research_areas", "source_folder",
]


def nl_to_overview_spec(description, fields):
    """
    Turn a plain-English overview request into a spec dict via the LLM:
    {"fields": [...], "group_by": <col|None>, "filters": {...}}.
    Only known columns/filters are kept.
    """
    import json
    import re
    from alr.common.llm_utils import llm_call

    sys_prompt = (
        "You convert a user's request into a JSON spec for an overview over a table of "
        "analyzed research publications. Respond with ONLY a JSON object of the form "
        '{"fields": ["col", ...], "group_by": "col or null", '
        '"filters": {"publication_year": "", "publication_type": "", "research_area": "", "source_folder": "", "topic": ""}}. '
        f"Valid columns are: {', '.join(fields)}. "
        "Use group_by (a single column) when the user asks for a count/summary/distribution; "
        "otherwise set it to null and choose relevant fields. The 'topic' filter matches a "
        "classification topic (e.g. 'Large Language Models') against the title/abstract tags. "
        "Leave a filter empty if not requested. Do not invent columns."
    )
    resp = llm_call(description, sys_prompt, "b")
    match = re.search(r"\{.*\}", resp or "", re.DOTALL)
    spec = json.loads(match.group(0) if match else resp)

    valid = set(fields)
    spec["fields"] = [c for c in (spec.get("fields") or []) if c in valid]
    gb = spec.get("group_by")
    spec["group_by"] = gb if gb in valid else None
    spec["filters"] = {k: v for k, v in (spec.get("filters") or {}).items()
                       if k in ("publication_year", "publication_type", "research_area", "source_folder", "topic") and v}
    return spec


# ---------------------------------------------------------------------------
# Data Files tab — discovery / merge helpers (pure functions, no Tk).
# ---------------------------------------------------------------------------

# Ordered categories, each identified separately in the Data Files tab.
DATA_FILE_CATEGORIES = (
    "Publication classification",
    "DOI metadata",
    "Processed registry",
    "Failed registry",
    "Abstract log",
    "Introduction log",
    "Results/Conclusion log",
    "Evaluation",
)

# Columns that are never classification topics when summarising true tags.
_NON_TOPIC_COLS = {
    "filename", "title", "uuid", "file_name", "original_uuid",
    "count", "content", "__source_file", "result", "score",
}


def _df_truthy(val) -> bool:
    """True for affirmative classification cells (bool / 1 / 'True' / 'yes')."""
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    text = str(val).strip().lower()
    return text in ("true", "1", "1.0", "yes", "y", "t")


def discover_space_data_files(manager) -> dict:
    """
    Return an ordered ``{category: [file paths]}`` mapping for one storage space
    (a DataAnalyzeManager). Only files that exist are listed. Categories mirror
    the on-disk enrichment/log layout so each kind can be identified separately.
    """
    import glob

    folder = str(manager.folder)
    cls = getattr(manager, "classification_subfolder",
                  os.path.join(folder, "Publication_Classification_Files"))
    doi = getattr(manager, "doi_metadata_subfolder",
                  os.path.join(folder, "DOI_Metadata_Files"))

    def _existing(paths):
        return [p for p in paths if p and os.path.exists(p)]

    files = {c: [] for c in DATA_FILE_CATEGORIES}
    files["Publication classification"] = sorted(
        glob.glob(os.path.join(str(cls), "*_Classification.xlsx")))
    files["DOI metadata"] = sorted(
        glob.glob(os.path.join(str(doi), "*_DOI_Metadata.xlsx")))
    files["Processed registry"] = _existing([str(manager.excel_success)])
    files["Failed registry"] = _existing([str(manager.excel_failed)])
    files["Abstract log"] = _existing([str(manager.AD_Abstract_log_path)])
    files["Introduction log"] = _existing([str(manager.AD_Intro_log_path)])
    files["Results/Conclusion log"] = _existing([getattr(manager, "AD_ResCon_log_path", None)])
    ev = glob.glob(os.path.join(folder, "Abstract_DB", "Abstract_Overview_folder",
                                "*_Abstract_Eval_Overview.xlsx"))
    ev += glob.glob(os.path.join(folder, "Introduction_DB", "*_Introduction_Eval_Overview.xlsx"))
    files["Evaluation"] = sorted(ev)
    return files


def data_file_type_key(path) -> str:
    """
    Fine-grained 'same type' key for a data file, used to group files that may
    safely merge together. Strips a leading date prefix from the filename stem,
    e.g. '2025-06-12_Title_Classification.xlsx' -> 'Title_Classification', while
    single, undated files (Processed_file_registry, Abstract_log, …) keep theirs.
    """
    import re
    stem = os.path.splitext(os.path.basename(str(path)))[0]
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}[_-]", "", stem)
    stem = re.sub(r"^\d{8}[_-]", "", stem)
    return stem


def _read_table(path):
    """Read the primary per-document sheet of a workbook as a DataFrame."""
    import pandas as pd
    try:
        xls = pd.ExcelFile(path)
        for pref in ("Overview", "Summary_Main"):
            if pref in xls.sheet_names:
                return pd.read_excel(xls, sheet_name=pref)
        return pd.read_excel(xls, sheet_name=xls.sheet_names[0])
    except Exception:
        return pd.read_excel(path)


def pick_key_column(df):
    """Pick the per-document key column (UUID > File_Name > filename), or None."""
    lower = {str(c).strip().lower(): c for c in df.columns}
    for cand in ("uuid", "file_name", "filename"):
        if cand in lower:
            return lower[cand]
    return None


def merge_data_files(paths):
    """
    Merge several same-type workbooks into one per-document table: concatenate
    (oldest -> newest) and keep the newest row per document key, so repeated
    dated runs collapse to one row each -- the SQL-like shape. Adds a
    ``__source_file`` column. Returns a (possibly empty) DataFrame with string
    column names.
    """
    import pandas as pd
    frames = []
    for p in sorted(paths, key=lambda x: os.path.getmtime(x)):
        try:
            df = _read_table(p).copy()
        except Exception:
            continue
        df["__source_file"] = os.path.basename(str(p))
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    merged = pd.concat(frames, ignore_index=True, sort=False)
    merged.columns = [str(c) for c in merged.columns]
    key = pick_key_column(merged)
    if key is not None:
        merged = merged.drop_duplicates(subset=[key], keep="last").reset_index(drop=True)
    return merged


def classification_topic_columns(df) -> list:
    """Columns of a classification table that represent topics (booleans)."""
    return [c for c in df.columns if str(c).strip().lower() not in _NON_TOPIC_COLS]


def classification_tags(row, topic_cols) -> str:
    """Comma-joined list of true topics for one classification row (SQL summary)."""
    return ", ".join(str(t) for t in topic_cols if _df_truthy(row.get(t)))


def sql_target_for_type(type_key):
    """
    Map a fine type key to how its merged data lands in SQL:
      ('metadata', None)      -> DOI/publication metadata (merge_metadata_workbook)
      ('classification', col) -> a classification summary column (fill-if-empty)
      ('sync', None)          -> reflected by syncing the space (registry/log/eval)
      ('skip', None)          -> not a per-document table (question-scored)
    """
    from alr.common.sql_store import sanitize_column_name

    low = type_key.lower()
    if low == "doi_metadata":
        return ("metadata", None)
    if low.endswith("classification"):
        if low == "title_classification":
            return ("classification", "classification")
        if low == "abstract_classification":
            return ("classification", "abstract_classification")
        if low == "question_scored_classification":
            return ("skip", None)
        base = type_key[: -len("_Classification")] if low.endswith("_classification") else type_key
        try:
            return ("classification", sanitize_column_name(base))
        except Exception:
            return ("skip", None)
    return ("sync", None)


class ProgressDialog:
    """A small modal dialog with a status message, progress bar and Cancel."""

    def __init__(self, master, title="Working…", on_cancel=None):
        self.top = tk.Toplevel(master)
        self.top.title(title)
        self.top.geometry("440x160")
        self.top.transient(master)
        self.top.grab_set()
        self.top.resizable(False, False)
        # Closing the window acts as Cancel (if cancellable), else no-op.
        self.top.protocol("WM_DELETE_WINDOW", (lambda: self._cancel()) if on_cancel else (lambda: None))

        self._on_cancel = on_cancel
        self.label = ttk.Label(self.top, text="Starting…", wraplength=410, anchor="w", justify="left")
        self.label.pack(padx=16, pady=(18, 8), fill="x")
        self.bar = ttk.Progressbar(self.top, mode="indeterminate", length=406)
        self.bar.pack(padx=16, pady=8)
        self.bar.start(12)

        if on_cancel:
            self.cancel_btn = ttk.Button(self.top, text="Cancel", command=self._cancel)
            self.cancel_btn.pack(pady=(4, 8))

    def _cancel(self):
        if self._on_cancel:
            self._on_cancel()
        self.label.config(text="Cancelling — finishing the current item…")
        if hasattr(self, "cancel_btn"):
            self.cancel_btn.config(state="disabled", text="Cancelling…")

    def apply(self, done=None, total=None, text=None):
        if text is not None:
            self.label.config(text=text)
        if done is not None and total:
            # Switch to a determinate bar once we know the item count, and reset
            # the maximum whenever the total changes (e.g. across phases).
            if str(self.bar.cget("mode")) != "determinate" or int(self.bar.cget("maximum")) != int(total):
                self.bar.stop()
                self.bar.config(mode="determinate", maximum=total)
            self.bar.config(value=done)

    def close(self):
        try:
            self.bar.stop()
            self.top.grab_release()
            self.top.destroy()
        except tk.TclError:
            pass


class ReviewApp:
    """Builds the whole review tool inside a given Tk container (window)."""

    def __init__(self, container):
        self.container = container
        self.store = AnalyzedDataStore()
        self.spaces = []       # current StorageSpace list
        self.download_logs = []
        self._field_vars = {}  # column -> BooleanVar

        self.notebook = ttk.Notebook(container)
        self.notebook.pack(fill="both", expand=True, padx=6, pady=6)

        self._build_spaces_tab()
        self._build_documents_tab()
        self._build_inspector_tab()
        self._build_database_tab()
        self._build_overviews_tab()
        self._build_data_files_tab()
        self._build_help_tab()

    # ================================================================ Spaces
    def _build_spaces_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Storage Spaces")

        bar = ttk.Frame(tab)
        bar.pack(fill="x", padx=8, pady=6)
        ttk.Button(bar, text="Select folder…", command=self._scan_folder).pack(side="left")
        self.spaces_status = ttk.Label(bar, text="Select a folder to scan for storage spaces.")
        self.spaces_status.pack(side="left", padx=10)

        # Spaces table
        sp_frame = ttk.LabelFrame(tab, text="Recognized storage spaces")
        sp_frame.pack(fill="both", expand=True, padx=8, pady=4)
        cols = ("status", "registry", "abstracts", "pdfs", "path")
        self.spaces_tree = ttk.Treeview(sp_frame, columns=cols, show="headings", selectmode="browse", height=8)
        for c, w in (("status", 90), ("registry", 70), ("abstracts", 80), ("pdfs", 60), ("path", 460)):
            self.spaces_tree.heading(c, text=c.capitalize())
            self.spaces_tree.column(c, width=w, anchor="w")
        vsb = ttk.Scrollbar(sp_frame, orient="vertical", command=self.spaces_tree.yview)
        self.spaces_tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.spaces_tree.pack(side="left", fill="both", expand=True)

        act = ttk.Frame(tab)
        act.pack(fill="x", padx=8, pady=4)
        ttk.Button(act, text="Link to database", command=self._link_selected).pack(side="left", padx=3)
        ttk.Button(act, text="Link ALL", command=self._link_all).pack(side="left", padx=3)
        ttk.Button(act, text="Extract DOI/metadata", command=self._doi_selected).pack(side="left", padx=3)
        ttk.Button(act, text="Classify (title + abstract)", command=self._classify_selected).pack(side="left", padx=3)
        ttk.Button(act, text="Evaluate data", command=self._evaluate_selected).pack(side="left", padx=3)
        ttk.Button(act, text="Open folder", command=self._open_selected_space).pack(side="right", padx=3)

        # Download logs & metadata workbooks
        dl_frame = ttk.LabelFrame(
            tab, text="Bibliographic workbooks (*_download_log / *_DOI_Metadata / publications_metadata .xlsx)")
        dl_frame.pack(fill="both", expand=True, padx=8, pady=4)
        self.logs_list = tk.Listbox(dl_frame, height=4)
        self.logs_list.pack(side="left", fill="both", expand=True, padx=(0, 4))
        ttk.Button(dl_frame, text="Import bibliographic data", command=self._import_download_log).pack(side="right", padx=4, pady=4)
        
    def on_link_database_clicked(self):
        # 1. Disable the button so the user can't click it twice
        self.link_db_button.config(state="disabled")

        # 2. Create the Progress Bar and Label UI elements
        self.progress_var = tk.DoubleVar()
        self.status_var = tk.StringVar(value="Preparing to sync...")
        
        self.progress_bar = ttk.Progressbar(self.parent_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill="x", pady=(10, 2))
        
        self.status_label = ttk.Label(self.parent_frame, textvariable=self.status_var)
        self.status_label.pack(pady=(0, 10))

        # 3. Create a thread-safe Queue
        self.progress_queue = queue.Queue()

        # 4. Define the callback that sql_store.py will trigger
        def progress_callback(current, total, current_uuid):
            # Push progress to the queue (don't update Tkinter directly from the thread!)
            self.progress_queue.put(("progress", current, total, current_uuid))

        # 5. Define the worker function that runs in the background
        def worker():
            try:
                total_synced = sync_storage_to_sql(self.manager, progress_callback=progress_callback)
                self.progress_queue.put(("done", total_synced))
            except Exception as e:
                self.progress_queue.put(("error", str(e)))

        # 6. Start the background thread
        threading.Thread(target=worker, daemon=True).start()
        
        # 7. Start polling the queue on the main Tkinter thread
        self._poll_progress_queue()

    def _poll_progress_queue(self):
        """Check the queue for updates every 100ms and update the Tkinter UI."""
        try:
            while True:
                msg = self.progress_queue.get_nowait()
                
                if msg[0] == "progress":
                    _, current, total, uuid = msg
                    pct = (current / total * 100) if total > 0 else 0
                    self.progress_var.set(pct)
                    self.status_var.set(f"Syncing: {current} / {total} (UUID: {uuid})")
                    
                elif msg[0] == "done":
                    total_synced = msg[1]
                    self.progress_var.set(100)
                    self.status_var.set(f"Success! Synced {total_synced} documents.")
                    self.link_db_button.config(state="normal")
                    
                    # Optional: Destroy the progress bar after 3 seconds
                    self.parent_frame.after(3000, self._cleanup_progress_ui)
                    return  # Stop polling
                    
                elif msg[0] == "error":
                    self.status_var.set(f"Sync failed: {msg[1]}")
                    self.link_db_button.config(state="normal")
                    return  # Stop polling
                    
        except queue.Empty:
            pass

        # If not done/error, schedule this function to run again in 100ms
        self.parent_frame.after(100, self._poll_progress_queue)

    def _cleanup_progress_ui(self):
        """Hide the progress elements once finished."""
        if hasattr(self, 'progress_bar') and self.progress_bar.winfo_exists():
            self.progress_bar.pack_forget()
        if hasattr(self, 'status_label') and self.status_label.winfo_exists():
            self.status_label.pack_forget()

    def _scan_folder(self):
        folder = filedialog.askdirectory(title="Select a folder to scan for storage spaces")
        if not folder:
            return
        self.container.config(cursor="watch"); self.container.update()
        try:
            self.spaces = detect_storage_spaces(folder)
            self.download_logs = find_download_logs(folder)
        finally:
            self.container.config(cursor="")

        self.spaces_tree.delete(*self.spaces_tree.get_children())
        for i, s in enumerate(self.spaces):
            self.spaces_tree.insert("", "end", iid=str(i),
                                    values=(s.status, s.n_registry, s.n_abstracts, s.n_pdfs, s.path))
        self.logs_list.delete(0, tk.END)
        for log in self.download_logs:
            self.logs_list.insert(tk.END, str(log))
        n_complete = sum(1 for s in self.spaces if s.status == "complete")
        self.spaces_status.config(
            text=f"Found {len(self.spaces)} space(s) ({n_complete} complete, "
                 f"{len(self.spaces) - n_complete} partial); "
                 f"{len(self.download_logs)} bibliographic workbook(s).")

    def _selected_space(self):
        sel = self.spaces_tree.selection()
        if not sel:
            messagebox.showinfo("No selection", "Select a storage space first.")
            return None
        return self.spaces[int(sel[0])]

    def _run_threaded(self, work, title, result_word="processed", on_success=None):
        """
        Run ``work(progress, should_cancel)`` on a background thread with a modal
        progress dialog (with a Cancel button). The worker only communicates
        through a thread-safe queue; all Tk access happens on the main thread via
        a poller scheduled with ``after`` (calling Tk from a worker thread is
        unsafe). ``progress(done=?, total=?, text=?)`` enqueues an update;
        ``should_cancel()`` returns True once Cancel is pressed; ``work`` returns
        an int count. On completion a result/cancel/error message is shown and
        the views are refreshed; ``on_success(count)`` runs on the main thread
        after an uncancelled, error-free completion.
        """
        cancel_event = threading.Event()
        dlg = ProgressDialog(self.container, title, on_cancel=cancel_event.set)
        q = queue.Queue()
        outcome = {}

        def progress(**kw):
            q.put(("progress", kw))

        def worker():
            try:
                q.put(("done", work(progress, cancel_event.is_set)))
            except Exception as e:  # noqa: BLE001 - surface any failure to the UI
                log_path = crash_logger.write_crash_log(
                    *sys.exc_info(), origin=f"background task: {title}")
                q.put(("error", (e, log_path)))

        def finish():
            dlg.close()
            if "error" in outcome:
                msg = str(outcome["error"])
                if outcome.get("error_log"):
                    msg += f"\n\nA full traceback was saved to:\n{outcome['error_log']}"
                messagebox.showerror(title, msg)
            elif cancel_event.is_set():
                messagebox.showinfo(title, f"{title}: cancelled after {result_word} "
                                           f"{outcome.get('n', 0)} document(s).")
            else:
                messagebox.showinfo(title, f"{title}: {result_word} {outcome.get('n', 0)} document(s).")
                if on_success:
                    on_success(outcome.get("n", 0))
            self._refresh_all()

        def poll():
            try:
                while True:
                    kind, payload = q.get_nowait()
                    if kind == "progress":
                        dlg.apply(**payload)
                    elif kind == "done":
                        outcome["n"] = payload
                        finish()
                        return
                    elif kind == "error":
                        outcome["error"], outcome["error_log"] = payload
                        finish()
                        return
            except queue.Empty:
                pass
            self.container.after(80, poll)

        threading.Thread(target=worker, daemon=True).start()
        self.container.after(80, poll)

    def _link_selected(self):
        s = self._selected_space()
        if not s:
            return

        def work(progress, should_cancel):
            progress(text=f"Linking '{os.path.basename(s.path)}' into the database…")
            return sync_storage_to_sql(s.path, db_path=self.store.db_path)

        self._run_threaded(work, "Link to database", "linked")

    def _link_all(self):
        if not self.spaces:
            return

        def work(progress, should_cancel):
            total = 0
            n = len(self.spaces)
            for i, sp in enumerate(self.spaces, 1):
                if should_cancel():
                    break
                progress(done=i - 1, total=n, text=f"Linking '{os.path.basename(sp.path)}'  ({i}/{n})…")
                total += sync_storage_to_sql(sp.path, db_path=self.store.db_path)
            progress(done=n, total=n)
            return total

        self._run_threaded(work, "Link ALL", "linked")

    def _doi_selected(self):
        s = self._selected_space()
        if not s:
            return
        from alr.data_analysis.doi_metadata import enrich_space_with_doi

        def work(progress, should_cancel):
            progress(text=f"Extracting DOI / metadata from PDFs in '{os.path.basename(s.path)}'…\n"
                          "This looks up Crossref/arXiv and may take a while.")
            return enrich_space_with_doi(s.path, db_path=self.store.db_path, should_cancel=should_cancel)

        self._run_threaded(work, "Extract DOI/metadata", "updated")

    def _classify_selected(self):
        s = self._selected_space()
        if not s:
            return
        if not get_stored_api_key("BlaBla Door"):
            messagebox.showwarning("API key required",
                                   "Publication classification needs a BlaBla Door API key. "
                                   "Set it in the main application (API Keys…) first.")
            return
        from alr.analysis_evaluation.publication_classification.classify_runner import (
            classify_space, classify_abstract_space,
        )

        def work(progress, should_cancel):
            # Make sure the space's documents exist in SQL first, otherwise
            # classification finds nothing to work on.
            progress(text=f"Syncing '{os.path.basename(s.path)}' into the database…")
            sync_storage_to_sql(s.path, db_path=self.store.db_path)

            progress(text="Classifying publications by title…")
            n = classify_space(
                s.path, db_path=self.store.db_path, should_cancel=should_cancel,
                progress_callback=lambda done, total: progress(
                    done=done, total=total, text=f"Classifying by title…  {done}/{total}"),
            )
            if should_cancel():
                return n
            progress(text="Classifying publications by abstract text…")
            n += classify_abstract_space(
                s.path, db_path=self.store.db_path, should_cancel=should_cancel,
                progress_callback=lambda done, total: progress(
                    done=done, total=total, text=f"Classifying by abstract…  {done}/{total}"),
            )
            return n

        self._run_threaded(work, "Classify publications", "classified")

    def _evaluate_selected(self):
        s = self._selected_space()
        if not s:
            return
        from alr.analysis_evaluation.data_evaluator import evaluate_space

        def work(progress, should_cancel):
            # Sync first so the evaluation scores land on existing SQL rows.
            progress(text=f"Syncing '{os.path.basename(s.path)}' into the database…")
            sync_storage_to_sql(s.path, db_path=self.store.db_path)
            progress(text="Evaluating analyzed data (section vs. abstract grounding)…")
            return evaluate_space(
                s.path, db_path=self.store.db_path, should_cancel=should_cancel,
                progress_callback=lambda done, total: progress(
                    done=done, total=total, text=f"Evaluating…  {done}/{total}"),
            )

        self._run_threaded(work, "Evaluate data", "evaluated")

    def _open_selected_space(self):
        s = self._selected_space()
        if s:
            open_path(s.path)

    def _import_download_log(self):
        """
        Merge the selected bibliographic workbook into the database. Download
        logs go through ``merge_download_log``; ``*_DOI_Metadata.xlsx`` and
        ``publications_metadata.xlsx`` files go through the wider
        ``merge_metadata_workbook`` (DOI/publisher/container/year/authors —
        fill-if-empty, matched by UUID or File_Name).
        """
        import pandas as pd
        sel = self.logs_list.curselection()
        if not sel:
            messagebox.showinfo("No selection", "Select a workbook first.")
            return
        path = self.download_logs[sel[0]]
        try:
            df = pd.read_excel(path)
            if "_download_log" in os.path.basename(str(path)).lower():
                n = self.store.merge_download_log(df)
            else:
                n = self.store.merge_metadata_workbook(df)
            messagebox.showinfo("Import bibliographic data",
                                f"Updated {n} document(s) with bibliographic data.")
        except Exception as e:
            messagebox.showerror("Import bibliographic data", str(e))
        self._refresh_all()

    # ============================================================= Documents
    def _build_documents_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Documents")
        self.review_view = ReviewDataView(tab)

    # ===================================================== Document Inspector
    def _build_inspector_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Document Inspector")

        top = ttk.LabelFrame(tab, text="Find a document (SQL first; the storage space fills whatever is missing)")
        top.pack(fill="x", padx=8, pady=6)

        row1 = ttk.Frame(top)
        row1.pack(fill="x", pady=3)
        ttk.Label(row1, text="Search by:").pack(side="left", padx=(6, 2))
        self.insp_mode_var = tk.StringVar(value=SEARCH_MODES[0])
        ttk.Combobox(row1, textvariable=self.insp_mode_var, values=list(SEARCH_MODES),
                     width=10, state="readonly").pack(side="left", padx=2)
        self.insp_search_entry = ttk.Entry(row1, width=52)
        self.insp_search_entry.pack(side="left", padx=4, fill="x", expand=True)
        self.insp_search_entry.bind("<Return>", lambda _e: self._inspector_search())
        ttk.Button(row1, text="Search", command=self._inspector_search).pack(side="left", padx=4)

        row2 = ttk.Frame(top)
        row2.pack(fill="x", pady=3)
        ttk.Label(row2, text="Fallback storage space:").pack(side="left", padx=(6, 2))
        self.insp_space_entry = ttk.Entry(row2, width=52)
        self.insp_space_entry.pack(side="left", padx=4, fill="x", expand=True)
        ttk.Button(row2, text="Browse…", command=self._inspector_pick_space).pack(side="left", padx=4)
        ttk.Label(row2, text="(used when the document isn't in SQL, or SQL data is incomplete)"
                  ).pack(side="left", padx=4)

        # Candidate matches (shown when the search hits more than one document).
        cand_frame = ttk.LabelFrame(tab, text="Matches")
        cand_frame.pack(fill="x", padx=8, pady=(0, 4))
        self.insp_candidates = ttk.Treeview(
            cand_frame, columns=("uuid", "title", "filename", "where"),
            show="headings", height=3)
        for col, width in (("uuid", 220), ("title", 380), ("filename", 220), ("where", 90)):
            self.insp_candidates.heading(col, text=col.capitalize())
            self.insp_candidates.column(col, width=width, stretch=(col == "title"))
        self.insp_candidates.pack(fill="x", padx=4, pady=4)
        self.insp_candidates.bind("<<TreeviewSelect>>", lambda _e: self._inspector_show_selected())

        # Document data, grouped, with per-field provenance.
        body = ttk.Frame(tab)
        body.pack(fill="both", expand=True, padx=8, pady=2)
        self.insp_tree = ttk.Treeview(body, columns=("source", "value"), show="tree headings")
        self.insp_tree.heading("#0", text="Field")
        self.insp_tree.column("#0", width=260, stretch=False)
        self.insp_tree.heading("source", text="Source")
        self.insp_tree.column("source", width=110, stretch=False)
        self.insp_tree.heading("value", text="Value")
        self.insp_tree.column("value", width=620, stretch=True)
        insp_vsb = ttk.Scrollbar(body, orient="vertical", command=self.insp_tree.yview)
        self.insp_tree.configure(yscrollcommand=insp_vsb.set)
        insp_vsb.pack(side="right", fill="y")
        self.insp_tree.pack(side="left", fill="both", expand=True)
        self.insp_tree.bind("<<TreeviewSelect>>", lambda _e: self._inspector_show_value())

        # Full value of the selected field (long JSON/text stays readable).
        value_frame = ttk.LabelFrame(tab, text="Selected field - full value")
        value_frame.pack(fill="x", padx=8, pady=4)
        self.insp_value_text = tk.Text(value_frame, height=5, wrap="word")
        self.insp_value_text.pack(fill="x", padx=4, pady=4)

        # PDF bar.
        pdf_frame = ttk.Frame(tab)
        pdf_frame.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(pdf_frame, text="PDF:").pack(side="left", padx=(2, 4))
        self.insp_pdf_var = tk.StringVar(value="—")
        ttk.Label(pdf_frame, textvariable=self.insp_pdf_var, foreground="#555"
                  ).pack(side="left", padx=4, fill="x", expand=True)
        self.insp_open_pdf_btn = ttk.Button(pdf_frame, text="Open PDF", state="disabled",
                                            command=self._inspector_open_pdf)
        self.insp_open_pdf_btn.pack(side="right", padx=4)
        self.insp_locate_pdf_btn = ttk.Button(pdf_frame, text="Locate PDF in folder…",
                                              state="disabled",
                                              command=self._inspector_locate_pdf)
        self.insp_locate_pdf_btn.pack(side="right", padx=4)

        # Current lookup state: candidates + assembled view + full values + pdf.
        self._insp_state = {"candidates": [], "values": {}, "pdf": None, "filename": ""}

    def _inspector_pick_space(self):
        folder = filedialog.askdirectory(title="Select the storage space folder for this document")
        if not folder:
            return
        if not is_storage_space(folder):
            messagebox.showerror("Document Inspector",
                                 "That folder has no Processed_file_registry.xlsx - "
                                 "it is not a storage space.")
            return
        self.insp_space_entry.delete(0, tk.END)
        self.insp_space_entry.insert(0, folder)

    def _inspector_search(self):
        mode = self.insp_mode_var.get()
        term = self.insp_search_entry.get().strip()
        if not term:
            messagebox.showerror("Document Inspector", "Enter a UUID, title or filename to search for.")
            return

        # Always start with the SQL database.
        matches = [("sql", row) for row in lookup_sql_documents(self.store, mode, term)]

        # Not synced to SQL -> ask for a storage space and search its registry.
        if not matches:
            space = self.insp_space_entry.get().strip()
            if not space:
                if messagebox.askyesno(
                        "Document Inspector",
                        f"No document matching this {mode.lower()} exists in the SQL "
                        "database.\n\nChoose a storage space to search its "
                        "processed-file registry instead?"):
                    self._inspector_pick_space()
                    space = self.insp_space_entry.get().strip()
            if space:
                if not is_storage_space(space):
                    messagebox.showerror("Document Inspector",
                                         "The fallback folder is not a storage space "
                                         "(no Processed_file_registry.xlsx).")
                    return
                matches = [("registry", row)
                           for row in find_registry_documents(space, mode, term)]

        self._insp_state["candidates"] = matches
        self.insp_candidates.delete(*self.insp_candidates.get_children())
        if not matches:
            self._inspector_clear_detail()
            messagebox.showinfo("Document Inspector",
                                f"No document matching this {mode.lower()} was found in the "
                                "SQL database or the chosen storage space.")
            return

        for i, (where, row) in enumerate(matches):
            uuid = row.get("uuid") or row.get("UUID") or ""
            self.insp_candidates.insert(
                "", "end", iid=str(i),
                values=(uuid, str(row.get("title") or ""), str(row.get("filename") or ""),
                        "SQL" if where == "sql" else "Registry"))
        self.insp_candidates.selection_set("0")  # triggers _inspector_show_selected

    def _inspector_show_selected(self):
        sel = self.insp_candidates.selection()
        if not sel:
            return
        try:
            where, row = self._insp_state["candidates"][int(sel[0])]
        except (IndexError, ValueError):
            return

        space = self.insp_space_entry.get().strip()
        sql_row = row if where == "sql" else None
        registry_row = row if where == "registry" else None
        uuid = str(row.get("uuid") or row.get("UUID") or "").strip()

        # SQL first; the space the document was synced from fills the gaps
        # (fallback: the user-chosen space).
        space_used = None
        if sql_row:
            source = str(sql_row.get("source_folder") or "").strip()
            if is_storage_space(source):
                space_used = source
            elif is_storage_space(space):
                space_used = space
        elif is_storage_space(space):
            space_used = space
        payloads = load_space_payloads(space_used, uuid) if space_used else {}

        view = assemble_document_view(sql_row=sql_row, registry_row=registry_row,
                                      payloads=payloads)
        self._inspector_fill_tree(view)

        gaps = missing_after_merge(view)
        if gaps and not space_used:
            self.insp_value_text.delete("1.0", tk.END)
            self.insp_value_text.insert(
                "1.0", f"No data found for: {', '.join(gaps)}. The document's synced "
                       "storage space was not reachable - choose the storage space "
                       "above and search again to fill these from disk.")

        # PDF: known locations first; recursive search stays available.
        pdf = find_pdf(sql_row=sql_row, registry_row=registry_row,
                       space_folder=space_used, payloads=payloads)
        filename = document_filename(sql_row, registry_row)
        self._insp_state.update(pdf=pdf, filename=filename)
        if pdf:
            self.insp_pdf_var.set(pdf)
        elif filename:
            self.insp_pdf_var.set(f"'{filename}' not found at its known locations - "
                                  "use 'Locate PDF in folder…' to search a folder tree.")
        else:
            self.insp_pdf_var.set("No filename recorded for this document.")
        self.insp_open_pdf_btn.configure(state="normal" if pdf else "disabled")
        self.insp_locate_pdf_btn.configure(state="normal" if filename else "disabled")

    def _inspector_fill_tree(self, view_rows):
        self.insp_tree.delete(*self.insp_tree.get_children())
        self._insp_state["values"] = {}
        self.insp_value_text.delete("1.0", tk.END)
        groups = {}
        for group, field, value, source in view_rows:
            if group not in groups:
                groups[group] = self.insp_tree.insert("", "end", text=group, open=True)
            preview = value if len(value) <= 200 else value[:200] + " …"
            iid = self.insp_tree.insert(groups[group], "end", text=field,
                                        values=(source, preview.replace("\n", " ")))
            self._insp_state["values"][iid] = value

    def _inspector_show_value(self):
        sel = self.insp_tree.selection()
        if not sel:
            return
        value = self._insp_state["values"].get(sel[0])
        if value is None:
            return
        self.insp_value_text.delete("1.0", tk.END)
        self.insp_value_text.insert("1.0", value)

    def _inspector_clear_detail(self):
        self.insp_tree.delete(*self.insp_tree.get_children())
        self.insp_value_text.delete("1.0", tk.END)
        self._insp_state.update(values={}, pdf=None, filename="")
        self.insp_pdf_var.set("—")
        self.insp_open_pdf_btn.configure(state="disabled")
        self.insp_locate_pdf_btn.configure(state="disabled")

    def _inspector_open_pdf(self):
        pdf = self._insp_state.get("pdf")
        if pdf and os.path.isfile(pdf):
            open_path(pdf)
        else:
            messagebox.showerror("Document Inspector", "The PDF file is no longer at the recorded path.")

    def _inspector_locate_pdf(self):
        filename = self._insp_state.get("filename")
        if not filename:
            return
        root = filedialog.askdirectory(
            title=f"Choose the folder to search (recursively) for '{filename}'")
        if not root:
            return

        holder = {"matches": []}

        def work(progress, should_cancel):
            progress(text=f"Searching for '{filename}' under {root}…")
            holder["matches"] = search_pdf_recursive(
                root, filename, should_cancel=should_cancel,
                progress_callback=lambda scanned, d: progress(
                    text=f"Scanned {scanned} folder(s)… {os.path.basename(d)}"))
            return len(holder["matches"])

        def on_success(_n):
            matches = holder["matches"]
            if not matches:
                self.insp_pdf_var.set(f"'{filename}' was not found anywhere under {root}.")
                return
            chosen = matches[0] if len(matches) == 1 else self._inspector_pick_match(matches)
            if not chosen:
                return
            self._insp_state["pdf"] = chosen
            self.insp_pdf_var.set(chosen)
            self.insp_open_pdf_btn.configure(state="normal")

        self._run_threaded(work, "PDF search", result_word="found", on_success=on_success)

    def _inspector_pick_match(self, matches):
        """Modal chooser when the recursive search finds several copies."""
        dialog = tk.Toplevel(self.container)
        dialog.title("Multiple PDFs found - pick one")
        dialog.transient(self.container)
        dialog.grab_set()
        ttk.Label(dialog, text="The filename exists in more than one place:").pack(padx=10, pady=6)
        box = tk.Listbox(dialog, width=100, height=min(len(matches), 12))
        for m in matches:
            box.insert(tk.END, m)
        box.selection_set(0)
        box.pack(fill="both", expand=True, padx=10, pady=4)
        chosen = {}

        def use_selected():
            sel = box.curselection()
            if sel:
                chosen["path"] = matches[sel[0]]
            dialog.destroy()

        ttk.Button(dialog, text="Use selected", command=use_selected).pack(pady=8)
        self.container.wait_window(dialog)
        return chosen.get("path")

    # ============================================================== Database
    def _build_database_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Database")

        # -- cross-space stats panel
        stats_frame = ttk.LabelFrame(tab, text="Database statistics")
        stats_frame.pack(fill="x", padx=8, pady=(8, 4))
        self.stats_label = ttk.Label(stats_frame, text="", justify="left", anchor="w")
        self.stats_label.pack(side="left", fill="x", expand=True, padx=8, pady=6)
        ttk.Button(stats_frame, text="Refresh", command=self._refresh_database_tab).pack(side="right", padx=8)

        # -- raw table browser
        browse_frame = ttk.LabelFrame(tab, text="All documents")
        browse_frame.pack(fill="both", expand=True, padx=8, pady=4)
        top = ttk.Frame(browse_frame)
        top.pack(fill="x", padx=4, pady=4)
        ttk.Label(top, text="Filter:").pack(side="left")
        self.db_search = tk.StringVar()
        e = ttk.Entry(top, textvariable=self.db_search, width=30)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda ev: self._load_db_table())
        ttk.Button(top, text="Apply", command=self._load_db_table).pack(side="left", padx=2)
        ttk.Button(top, text="Export columns to Excel…",
                   command=self._export_db_columns).pack(side="left", padx=12)
        self.db_browse_status = ttk.Label(top, text="")
        self.db_browse_status.pack(side="right")

        self.db_tree = ttk.Treeview(browse_frame, show="headings", height=8)
        dv = ttk.Scrollbar(browse_frame, orient="vertical", command=self.db_tree.yview)
        dh = ttk.Scrollbar(browse_frame, orient="horizontal", command=self.db_tree.xview)
        self.db_tree.configure(yscrollcommand=dv.set, xscrollcommand=dh.set)
        dv.pack(side="right", fill="y")
        dh.pack(side="bottom", fill="x")
        self.db_tree.pack(side="left", fill="both", expand=True)

        # -- ad-hoc SELECT query box
        query_frame = ttk.LabelFrame(tab, text="SQL query (read-only SELECT)")
        query_frame.pack(fill="both", expand=True, padx=8, pady=4)
        self.sql_text = tk.Text(query_frame, height=3, wrap="word")
        self.sql_text.pack(fill="x", padx=4, pady=4)
        self.sql_text.insert("1.0", "SELECT title, publication_year, publication_type, classification FROM documents")
        qbar = ttk.Frame(query_frame)
        qbar.pack(fill="x", padx=4)
        ttk.Button(qbar, text="Run query", command=self._run_sql_query).pack(side="left")
        self.sql_status = ttk.Label(qbar, text="Only a single SELECT is allowed.")
        self.sql_status.pack(side="left", padx=8)
        ttk.Button(qbar, text="Export result…", command=self._export_sql_result).pack(side="right")
        self.sql_tree = ttk.Treeview(query_frame, show="headings", height=6)
        sv = ttk.Scrollbar(query_frame, orient="vertical", command=self.sql_tree.yview)
        sh = ttk.Scrollbar(query_frame, orient="horizontal", command=self.sql_tree.xview)
        self.sql_tree.configure(yscrollcommand=sv.set, xscrollcommand=sh.set)
        sv.pack(side="right", fill="y")
        sh.pack(side="bottom", fill="x")
        self.sql_tree.pack(side="left", fill="both", expand=True)
        self._last_sql_result = ([], [])

        self._refresh_database_tab()

    @staticmethod
    def _fill_tree(tree, columns, rows, limit=2000):
        tree.delete(*tree.get_children())
        tree["columns"] = columns
        for c in columns:
            tree.heading(c, text=c)
            tree.column(c, width=max(70, min(240, len(str(c)) * 11)), anchor="w")
        for r in rows[:limit]:
            tree.insert("", "end", values=[r.get(c) for c in columns])

    def _refresh_database_tab(self):
        st = self.store.stats()
        spaces = ", ".join(f"{os.path.basename(p['source_folder'].rstrip('/')) or p['source_folder']}={p['count']}"
                           for p in st["per_space"][:6]) or "none"
        self.stats_label.config(text=(
            f"Total documents: {st['total']}    |    with abstract: {st['with_abstract']}    "
            f"with DOI: {st['with_doi']}    title-classified: {st['with_classification']}    "
            f"abstract-classified: {st.get('with_abstract_classification', 0)}    "
            f"evaluated: {st.get('with_evaluation', 0)}\n"
            f"distinct years: {st['distinct_years']}    distinct publication types: {st['distinct_types']}\n"
            f"storage spaces: {spaces}"))
        self._load_db_table()

    def _load_db_table(self):
        rows = self.store.list_documents(self.db_search.get().strip() or None)
        self._fill_tree(self.db_tree, list(COLUMNS), rows)
        self.db_browse_status.config(text=f"{len(rows)} row(s)")

    def _run_sql_query(self):
        sql = self.sql_text.get("1.0", tk.END)
        try:
            cols, rows = self.store.run_select(sql)
            self._fill_tree(self.sql_tree, cols, rows)
            self._last_sql_result = (cols, rows)
            self.sql_status.config(text=f"{len(rows)} row(s).")
        except Exception as e:
            self.sql_status.config(text=str(e))
            messagebox.showerror("Query error", str(e))

    def _export_sql_result(self):
        cols, rows = self._last_sql_result
        if not rows:
            messagebox.showinfo("Nothing to export", "Run a query that returns rows first.")
            return
        self._export_rows(cols, rows, "query_result")

    def _export_db_columns(self):
        """Export database documents to Excel by ticking columns — no SQL needed.

        Opens a dialog with one checkbox per database column, an optional
        storage-space filter, and an option to honor the browser's current
        filter text. The data comes straight from the SQLite store
        (``list_documents``), so what you export is what the browser shows.
        """
        dlg = tk.Toplevel(self.container)
        dlg.title("Export columns to Excel")
        dlg.geometry("620x460")
        dlg.transient(self.container.winfo_toplevel())
        dlg.grab_set()

        ttk.Label(dlg, text="Tick the columns to include in the Excel file:",
                  font=("Arial", 10, "bold")).pack(anchor="w", padx=10, pady=(10, 4))

        # Scrollable checkbox grid (same pattern as the Overviews field picker).
        picker = ttk.Frame(dlg)
        picker.pack(fill="both", expand=True, padx=10)
        canvas = tk.Canvas(picker, highlightthickness=0)
        vsb = ttk.Scrollbar(picker, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        col_vars = {}
        for i, col in enumerate(COLUMNS):
            var = tk.BooleanVar(value=col in DEFAULT_OVERVIEW_FIELDS)
            col_vars[col] = var
            ttk.Checkbutton(inner, text=col, variable=var).grid(
                row=i // 4, column=i % 4, sticky="w", padx=4, pady=1)

        selbar = ttk.Frame(dlg)
        selbar.pack(fill="x", padx=10, pady=(4, 0))
        ttk.Button(selbar, text="Select all",
                   command=lambda: [v.set(True) for v in col_vars.values()]).pack(side="left")
        ttk.Button(selbar, text="Clear all",
                   command=lambda: [v.set(False) for v in col_vars.values()]).pack(side="left", padx=6)

        opts = ttk.Frame(dlg)
        opts.pack(fill="x", padx=10, pady=6)
        ttk.Label(opts, text="Storage space (optional):").grid(row=0, column=0, sticky="w")
        space_var = tk.StringVar(value="")
        ttk.Combobox(opts, textvariable=space_var, width=48,
                     values=[""] + self.store.list_source_folders()).grid(
            row=0, column=1, sticky="w", padx=6, pady=2)
        current_search = self.db_search.get().strip()
        use_filter = tk.BooleanVar(value=bool(current_search))
        chk = ttk.Checkbutton(
            opts, variable=use_filter,
            text=f"Apply the browser's filter text ({current_search!r})" if current_search
                 else "Apply the browser's filter text (empty — no effect)")
        chk.grid(row=1, column=0, columnspan=2, sticky="w", pady=2)

        def _do_export():
            cols = [c for c in COLUMNS if col_vars[c].get()]
            if not cols:
                messagebox.showinfo("No columns", "Tick at least one column to export.",
                                    parent=dlg)
                return
            search = current_search if (use_filter.get() and current_search) else None
            rows = self.store.list_documents(search)
            space = space_var.get().strip()
            if space:
                rows = [r for r in rows if (r.get("source_folder") or "") == space]
            if not rows:
                messagebox.showinfo("Nothing to export",
                                    "No documents match the chosen filters.", parent=dlg)
                return
            rows = [{c: r.get(c) for c in cols} for r in rows]
            dlg.destroy()
            self._export_rows(cols, rows, "database_export")

        btns = ttk.Frame(dlg)
        btns.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Button(btns, text="Export…", command=_do_export).pack(side="left")
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="left", padx=8)

    # ============================================================= Overviews
    def _build_overviews_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Overviews")

        # Field picker (scrollable checkboxes)
        picker = ttk.LabelFrame(tab, text="Columns to include")
        picker.pack(fill="x", padx=8, pady=6)
        canvas = tk.Canvas(picker, height=90, highlightthickness=0)
        inner = ttk.Frame(canvas)
        hsb = ttk.Scrollbar(picker, orient="horizontal", command=canvas.xview)
        canvas.configure(xscrollcommand=hsb.set)
        canvas.pack(side="top", fill="x", expand=True)
        hsb.pack(side="bottom", fill="x")
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        for i, col in enumerate(COLUMNS):
            var = tk.BooleanVar(value=col in DEFAULT_OVERVIEW_FIELDS)
            self._field_vars[col] = var
            ttk.Checkbutton(inner, text=col, variable=var).grid(
                row=i // 6, column=i % 6, sticky="w", padx=4, pady=1)

        # Filters
        filt = ttk.LabelFrame(tab, text="Filters (optional)")
        filt.pack(fill="x", padx=8, pady=4)
        ttk.Label(filt, text="Storage folder:").grid(row=0, column=0, sticky="w", padx=4, pady=3)
        self.filter_source = ttk.Combobox(filt, width=44, values=[""] + self.store.list_source_folders())
        self.filter_source.grid(row=0, column=1, padx=4, pady=3)
        ttk.Label(filt, text="Year:").grid(row=0, column=2, sticky="w", padx=4)
        self.filter_year = ttk.Entry(filt, width=8); self.filter_year.grid(row=0, column=3, padx=4)
        ttk.Label(filt, text="Pub type:").grid(row=1, column=0, sticky="w", padx=4, pady=3)
        self.filter_type = ttk.Entry(filt, width=24); self.filter_type.grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(filt, text="Research area:").grid(row=1, column=2, sticky="w", padx=4)
        self.filter_ra = ttk.Entry(filt, width=20); self.filter_ra.grid(row=1, column=3, padx=4)
        ttk.Label(filt, text="Classification topic:").grid(row=2, column=0, sticky="w", padx=4, pady=3)
        self.filter_topic = ttk.Entry(filt, width=24); self.filter_topic.grid(row=2, column=1, sticky="w", padx=4)
        ttk.Label(filt, text="(matches title or abstract tags)").grid(row=2, column=2, columnspan=2, sticky="w", padx=4)

        # Grouping + chart + templates + natural language
        adv = ttk.LabelFrame(tab, text="Grouping, templates & natural language")
        adv.pack(fill="x", padx=8, pady=4)
        ttk.Label(adv, text="Group by:").grid(row=0, column=0, sticky="w", padx=4, pady=3)
        self.group_by_var = tk.StringVar(value="(none)")
        ttk.Combobox(adv, textvariable=self.group_by_var, width=22, state="readonly",
                     values=["(none)"] + list(COLUMNS)).grid(row=0, column=1, padx=4, pady=3, sticky="w")
        ttk.Label(adv, text="Chart:").grid(row=0, column=2, sticky="w", padx=4)
        self.chart_type_var = tk.StringVar(value="bar")
        ttk.Combobox(adv, textvariable=self.chart_type_var, width=8, state="readonly",
                     values=["bar", "pie"]).grid(row=0, column=3, padx=4, sticky="w")

        ttk.Label(adv, text="Template:").grid(row=1, column=0, sticky="w", padx=4, pady=3)
        self.template_var = tk.StringVar()
        self.template_combo = ttk.Combobox(adv, textvariable=self.template_var, width=22, state="readonly")
        self.template_combo.grid(row=1, column=1, padx=4, pady=3, sticky="w")
        ttk.Button(adv, text="Load", command=self._load_template).grid(row=1, column=2, padx=2)
        ttk.Button(adv, text="Save…", command=self._save_template).grid(row=1, column=3, padx=2)
        ttk.Button(adv, text="Delete", command=self._delete_template).grid(row=1, column=4, padx=2)

        ttk.Label(adv, text="Describe:").grid(row=2, column=0, sticky="w", padx=4, pady=3)
        self.nl_entry = ttk.Entry(adv, width=60)
        self.nl_entry.grid(row=2, column=1, columnspan=3, padx=4, pady=3, sticky="w")
        ttk.Button(adv, text="Build from description", command=self._build_from_description).grid(row=2, column=4, padx=2)

        # Per-topic counts (splits the comma-joined classification tags).
        topic_row = ttk.Frame(adv)
        topic_row.grid(row=3, column=0, columnspan=5, sticky="w", padx=4, pady=(2, 3))
        ttk.Label(topic_row, text="Topic counts from:").pack(side="left")
        self.topic_col_var = tk.StringVar(value="Title classification")
        ttk.Combobox(topic_row, textvariable=self.topic_col_var, width=22, state="readonly",
                     values=["Title classification", "Abstract classification"]).pack(side="left", padx=4)
        ttk.Button(topic_row, text="Show topic counts", command=self._show_topic_counts).pack(side="left", padx=4)
        ttk.Label(topic_row, text="(one row/bar per topic; honours filters above)").pack(side="left", padx=4)

        btns = ttk.Frame(tab)
        btns.pack(fill="x", padx=8, pady=4)
        ttk.Button(btns, text="Preview", command=self._preview_overview).pack(side="left", padx=3)
        ttk.Button(btns, text="Show chart", command=self._show_chart).pack(side="left", padx=3)
        ttk.Button(btns, text="Export Excel", command=lambda: self._export_overview("xlsx")).pack(side="left", padx=3)
        ttk.Button(btns, text="Export CSV", command=lambda: self._export_overview("csv")).pack(side="left", padx=3)
        ttk.Button(btns, text="Export chart…", command=self._export_chart).pack(side="left", padx=3)
        self.overview_status = ttk.Label(btns, text="")
        self.overview_status.pack(side="right", padx=6)

        # Preview table (left) + chart (right)
        split = ttk.PanedWindow(tab, orient="horizontal")
        split.pack(fill="both", expand=True, padx=8, pady=4)
        prev = ttk.Frame(split)
        split.add(prev, weight=3)
        self.overview_tree = ttk.Treeview(prev, show="headings")
        ovsb = ttk.Scrollbar(prev, orient="vertical", command=self.overview_tree.yview)
        ovhsb = ttk.Scrollbar(prev, orient="horizontal", command=self.overview_tree.xview)
        self.overview_tree.configure(yscrollcommand=ovsb.set, xscrollcommand=ovhsb.set)
        ovsb.pack(side="right", fill="y")
        ovhsb.pack(side="bottom", fill="x")
        self.overview_tree.pack(side="left", fill="both", expand=True)
        self.chart_frame = ttk.Frame(split)
        split.add(self.chart_frame, weight=2)

        self._reload_templates()

    def _selected_fields(self):
        return [c for c in COLUMNS if self._field_vars[c].get()] or ["uuid"]

    def _current_filters(self):
        f = {}
        if self.filter_source.get().strip():
            f["source_folder"] = self.filter_source.get().strip()
        if self.filter_year.get().strip():
            f["publication_year"] = self.filter_year.get().strip()
        if self.filter_type.get().strip():
            f["publication_type"] = self.filter_type.get().strip()
        if self.filter_ra.get().strip():
            f["research_area"] = self.filter_ra.get().strip()
        if self.filter_topic.get().strip():
            f["topic"] = self.filter_topic.get().strip()
        return f

    def _group_by(self):
        g = self.group_by_var.get()
        return g if g and g != "(none)" else None

    def _overview_data(self):
        """Return (columns, rows) for the current overview (grouped or flat)."""
        group_by = self._group_by()
        if group_by:
            rows = self.store.grouped_overview(group_by, self._current_filters())
            return [group_by, "count"], rows
        fields = self._selected_fields()
        return fields, self.store.build_overview(fields, self._current_filters())

    def _preview_overview(self):
        cols, rows = self._overview_data()
        self._fill_tree(self.overview_tree, cols, rows, limit=2000)
        self._last_overview = (cols, rows)
        self._preview_is_topic_counts = False
        self.overview_status.config(text=f"{len(rows)} row(s)" + (" (showing first 2000)" if len(rows) > 2000 else ""))
        if self._group_by():
            self._show_chart()

    def _export_overview(self, kind):
        # When the preview currently shows topic counts, export exactly that --
        # otherwise recomputing the flat/grouped overview would silently export
        # a different table than the one on screen.
        if getattr(self, "_preview_is_topic_counts", False) and getattr(self, "_last_overview", None):
            cols, rows = self._last_overview
            default_name = "topic_counts"
        else:
            cols, rows = self._overview_data()
            default_name = "overview"
        if not rows:
            messagebox.showinfo("Nothing to export", "The overview is empty.")
            return
        self._export_rows(cols, rows, default_name, kind)

    def _export_rows(self, cols, rows, default_name, kind="xlsx"):
        os.makedirs(ALR_overviews_folder, exist_ok=True)
        ext = ".xlsx" if kind == "xlsx" else ".csv"
        path = filedialog.asksaveasfilename(
            title="Export", defaultextension=ext, initialdir=str(ALR_overviews_folder),
            initialfile=f"{default_name}{ext}",
            filetypes=[("Excel files", "*.xlsx")] if kind == "xlsx" else [("CSV files", "*.csv")])
        if not path:
            return
        try:
            if kind == "xlsx" or path.lower().endswith(".xlsx"):
                import pandas as pd
                pd.DataFrame(rows, columns=cols).to_excel(path, index=False)
            else:
                with open(path, "w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
                    w.writeheader()
                    w.writerows(rows)
            messagebox.showinfo("Exported", f"Exported {len(rows)} row(s) to:\n{path}")
        except Exception as e:
            messagebox.showerror("Export failed", str(e))

    # -- charts -------------------------------------------------------------
    def _render_chart(self, labels, values, title):
        """Draw a bar/pie chart of ``labels``/``values`` into the chart frame."""
        try:
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        except Exception as e:
            messagebox.showerror("Chart", f"matplotlib is required for charts: {e}")
            return
        for w in self.chart_frame.winfo_children():
            w.destroy()
        fig = Figure(figsize=(4.0, 3.2), dpi=100)
        ax = fig.add_subplot(111)
        if self.chart_type_var.get() == "pie":
            ax.pie(values, labels=labels, autopct="%1.0f%%", textprops={"fontsize": 7})
        else:
            ax.bar(range(len(values)), values, color="#378ADD")
            ax.set_xticks(range(len(labels)))
            ax.set_xticklabels(labels, rotation=40, ha="right", fontsize=7)
            ax.set_ylabel("count", fontsize=8)
        ax.set_title(title, fontsize=9)
        fig.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=self.chart_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)
        self._chart_fig = fig

    def _show_chart(self):
        group_by = self._group_by()
        if not group_by:
            messagebox.showinfo("Chart", "Choose a 'Group by' column to chart an overview.")
            return
        data = self.store.grouped_overview(group_by, self._current_filters())
        if not data:
            messagebox.showinfo("Chart", "No data to chart.")
            return
        data = data[:20]  # keep charts readable
        labels = [str(r[group_by]) for r in data]
        values = [r["count"] for r in data]
        self._render_chart(labels, values, f"documents by {group_by}")

    def _show_topic_counts(self):
        """Per-topic overview: split the classification tags and count per topic."""
        column = "classification" if self.topic_col_var.get().startswith("Title") else "abstract_classification"
        data = self.store.topic_counts(column, self._current_filters())
        cols = ["topic", "count"]
        self._fill_tree(self.overview_tree, cols, data, limit=2000)
        self._last_overview = (cols, data)
        self._preview_is_topic_counts = True
        label = "title" if column == "classification" else "abstract"
        self.overview_status.config(text=f"{len(data)} topic(s) from {label} classification")
        if not data:
            messagebox.showinfo("Topic counts", "No classification tags found. Run classification first.")
            return
        top = data[:20]
        self._render_chart([r["topic"] for r in top], [r["count"] for r in top],
                           f"documents per {label} topic")

    def _export_chart(self):
        if not getattr(self, "_chart_fig", None):
            messagebox.showinfo("Export chart", "Show a chart first.")
            return
        os.makedirs(ALR_overviews_folder, exist_ok=True)
        path = filedialog.asksaveasfilename(
            title="Export chart", defaultextension=".png", initialdir=str(ALR_overviews_folder),
            initialfile="overview_chart.png", filetypes=[("PNG image", "*.png")])
        if not path:
            return
        try:
            self._chart_fig.savefig(path, dpi=150, bbox_inches="tight")
            messagebox.showinfo("Exported", f"Chart saved to:\n{path}")
        except Exception as e:
            messagebox.showerror("Export chart", str(e))

    # -- templates ----------------------------------------------------------
    def _current_spec(self):
        return {
            "fields": self._selected_fields(),
            "filters": self._current_filters(),
            "group_by": self._group_by(),
            "chart": self.chart_type_var.get(),
        }

    def _apply_spec(self, spec):
        fields = set(spec.get("fields") or [])
        for col, var in self._field_vars.items():
            var.set(col in fields)
        f = spec.get("filters") or {}
        self.filter_source.set(f.get("source_folder", "") or "")
        for entry, key in ((self.filter_year, "publication_year"),
                           (self.filter_type, "publication_type"),
                           (self.filter_ra, "research_area"),
                           (self.filter_topic, "topic")):
            entry.delete(0, tk.END)
            entry.insert(0, str(f.get(key, "") or ""))
        self.group_by_var.set(spec.get("group_by") or "(none)")
        if spec.get("chart") in ("bar", "pie"):
            self.chart_type_var.set(spec["chart"])

    def _reload_templates(self):
        names = self.store.list_templates()
        self.template_combo["values"] = names
        if names and not self.template_var.get():
            self.template_var.set(names[0])

    def _save_template(self):
        from tkinter import simpledialog
        name = simpledialog.askstring("Save template", "Template name:", parent=self.container)
        if not name:
            return
        self.store.save_template(name, self._current_spec())
        self.template_var.set(name)
        self._reload_templates()
        messagebox.showinfo("Template", f"Saved overview template '{name}'.")

    def _load_template(self):
        name = self.template_var.get()
        if not name:
            return
        spec = self.store.get_template(name)
        if spec:
            self._apply_spec(spec)
            self._preview_overview()

    def _delete_template(self):
        name = self.template_var.get()
        if name and messagebox.askyesno("Delete template", f"Delete template '{name}'?"):
            self.store.delete_template(name)
            self.template_var.set("")
            self._reload_templates()

    # -- natural language ---------------------------------------------------
    def _build_from_description(self):
        text = self.nl_entry.get().strip()
        if not text:
            return
        if not get_stored_api_key("BlaBla Door"):
            messagebox.showwarning("API key required",
                                   "Building an overview from a description uses the LLM and needs a "
                                   "BlaBla Door API key. Set it in the main application (API Keys…) first.")
            return
        try:
            spec = nl_to_overview_spec(text, list(COLUMNS))
        except Exception as e:
            messagebox.showerror("Natural language", f"Could not interpret the request:\n{e}")
            return
        self._apply_spec(spec)
        self._preview_overview()

    # ================================================================== Guide
    # (title, body) blocks rendered into the Guide tab. Kept as data so the
    # help text is easy to extend when features change.
    HELP_SECTIONS = [
        ("Quick start (typical workflow)",
         "1. Storage Spaces tab -> 'Select folder…' and pick the folder that holds your "
         "analysis results (e.g. ~/ALR DATA). Every recognized storage space is listed.\n"
         "2. Select a space -> 'Link to database' (or 'Link ALL') to import it into the "
         "shared SQLite database.\n"
         "3. Optionally enrich: 'Extract DOI/metadata', 'Classify (title + abstract)', "
         "'Evaluate data'.\n"
         "4. Browse/edit in the Documents tab; inspect in the Database tab; build tables "
         "and charts in the Overviews tab and export them."),

        ("Storage Spaces — Select folder…",
         "Recursively scans the chosen folder for analysis storage spaces (folders created "
         "by a previous 'Analyze Literature' run). Status 'complete' means the space has a "
         "processed-file registry AND at least one analyzed abstract; 'partial' means some "
         "markers are missing. It also lists any bibliographic workbooks found "
         "(*_download_log.xlsx, *_DOI_Metadata.xlsx, publications_metadata.xlsx).\n"
         "Example: select 'D:/ALR DATA' -> 3 spaces found (2 complete, 1 partial), "
         "1 download log."),

        ("Storage Spaces — Link to database / Link ALL",
         "Imports a space's registry + analyzed JSON files into the SQLite database so the "
         "Documents/Database/Overviews tabs can see them. It also merges the space's "
         "recorded DOI/publication metadata workbooks and evaluation overviews into the "
         "database (fill-if-empty). Safe to repeat: re-linking updates "
         "rows to the latest data and never wipes enrichment (DOI/classification/evaluation) "
         "already stored.\n"
         "Example: select the 'LLM_Safety_Results' row -> 'Link to database' -> "
         "'linked 42 document(s)'."),

        ("Storage Spaces — Extract DOI/metadata",
         "Reads each PDF's first pages, finds a DOI or arXiv id and looks up bibliographic "
         "metadata (Crossref/arXiv): link, year, publisher, authors. Results go to the "
         "space's dated DOI_Metadata workbook and into the database. Files whose DOI data "
         "already exists (in the database or a previous dated file) are skipped and their "
         "data is carried forward — only new PDFs cost network lookups.\n"
         "Example: run it once -> 40/42 found; run it again next week after adding 3 PDFs "
         "-> only the 3 new files are looked up."),

        ("Storage Spaces — Classify (title + abstract)",
         "Runs the publication classifier twice per document — once on the title, once on "
         "the identified abstract text — against the aerospace/safety taxonomy (Systems "
         "Engineering, Safety Engineering, Risk Assessment, Large Language Models, …). "
         "Needs a BlaBla Door API key (set it in the main application). Results are stored "
         "in the 'classification' and 'abstract_classification' columns and in today's "
         "dated classification workbooks inside the space.\n"
         "Example: after classifying, the Overviews tab filter 'Classification topic: "
         "Large Language Models' returns every matching paper."),

        ("Storage Spaces — Evaluate data",
         "Checks, per analyzed section (Objective, Methodology, Results, …), whether the "
         "extracted content is actually grounded in (a subset of) the abstract text, and "
         "stores per-section true/false counts plus an overall percentage score "
         "('evaluation_score') per document.\n"
         "Example: a paper whose extracted sections all match its abstract scores 100.0; "
         "check scores in the Database tab with:  SELECT title, evaluation_score FROM "
         "documents ORDER BY evaluation_score."),

        ("Storage Spaces — Import bibliographic data",
         "Select one of the discovered workbooks and click 'Import bibliographic data'. "
         "Three kinds are recognized: *_download_log.xlsx (fills link / authors / "
         "publication year / first author), *_DOI_Metadata.xlsx and "
         "publications_metadata.xlsx (fill DOI link / publisher / container / year / "
         "authors / first author). Rows are matched by UUID when the workbook has one, "
         "else by File_Name — and values are only filled where the document has no value "
         "yet, so nothing already in the database is overwritten.\n"
         "Example: '2025-06-12_DOI_Metadata.xlsx' -> 'Updated 17 document(s)'.\n"
         "Note: 'Link to database' also merges these workbooks (plus the evaluation "
         "overview) automatically when it syncs a space."),

        ("Documents — browse & edit",
         "Left: all documents in the database (Search matches title, filename or UUID — "
         "e.g. type 'safety' and press Enter). Right: the selected document's sections, "
         "editable. List sections (Results, Research Areas, Key Concepts) use ONE ITEM PER "
         "LINE. 'Save Changes' writes edits back to the database. 'Open PDF' / 'Open "
         "Abstract JSON' open the underlying files; 'Delete' removes only the database row "
         "(files on disk stay). 'Sync from storage folder…' imports a space directly, and "
         "'Export CSV/Excel…' saves the currently filtered list.\n"
         "Example: search 'MBSE' -> select a paper -> fix a typo in Objective -> Save "
         "Changes."),

        ("Database — statistics & browser",
         "The statistics panel shows totals (documents, with abstract, with DOI, "
         "title/abstract-classified, evaluated) and per-space counts. The 'All documents' "
         "table shows every column; the Filter box matches title/filename/UUID.\n"
         "Example: after running 'Evaluate data', hit Refresh and watch the 'evaluated' "
         "count rise."),

        ("Database — Export columns to Excel…",
         "Exports database documents to an Excel file without writing any SQL: tick the "
         "columns you want (defaults match the overview builder), optionally restrict to "
         "one storage space or reuse the browser's filter text, then choose where to save. "
         "The data comes straight from the SQLite database, so it always reflects the "
         "latest linked/enriched state.\n"
         "Example: tick title + publication_year + classification + evaluation_score, "
         "pick storage space 'LLM_Safety_Results' -> Export… -> "
         "database_export.xlsx with one row per document."),

        ("Database — SQL query (read-only)",
         "Run a single SELECT over the 'documents' table (writes are blocked). "
         "'Export result…' saves the result table.\n"
         "Examples:\n"
         "  SELECT title, publication_year FROM documents WHERE classification LIKE "
         "'%Large Language Models%'\n"
         "  SELECT publication_year, COUNT(*) AS n FROM documents GROUP BY publication_year "
         "ORDER BY n DESC\n"
         "  SELECT title, evaluation_score FROM documents WHERE evaluation_score <> '' "
         "ORDER BY CAST(evaluation_score AS REAL) DESC"),

        ("Overviews — columns & filters",
         "Tick the columns you want, optionally set filters, then 'Preview'. Filters: "
         "Storage folder (exact space), Year (e.g. 2023), Pub type (e.g. Journal Article), "
         "Research area (substring), Classification topic (matches the title OR abstract "
         "classification tags).\n"
         "Example: columns title + publication_year + classification, filter Year=2024, "
         "topic=Risk Assessment -> Preview -> Export Excel."),

        ("Overviews — Group by & charts",
         "Choose a 'Group by' column to turn the overview into value/count rows, drawn as "
         "a bar or pie chart next to the table. 'Export chart…' saves it as PNG.\n"
         "Example: Group by = publication_year, Chart = bar -> Preview -> a papers-per-year "
         "bar chart appears."),

        ("Overviews — Show topic counts",
         "Classification tags are stored comma-joined per document ('Systems Engineering, "
         "Risk Assessment'), so grouping by the classification column would count whole "
         "combinations. 'Show topic counts' splits the tags and counts each topic once per "
         "document — one row/bar per topic — from either the title or the abstract "
         "classification, honouring the filters above. Export then saves exactly the "
         "topic-count table shown.\n"
         "Example: 'Topic counts from: Abstract classification' -> Show topic counts -> "
         "Large Language Models 12, Safety Engineering 9, …"),

        ("Overviews — templates",
         "'Save…' stores the current column/filter/group-by/chart setup under a name; "
         "'Load' restores it (and previews); 'Delete' removes it.\n"
         "Example: save your monthly report layout as 'per-year LLM overview' and reload "
         "it next month."),

        ("Overviews — Build from description (LLM)",
         "Type a plain-English request and let the LLM pick columns, filters and grouping "
         "(needs a BlaBla Door API key). The result is applied to the controls, so you can "
         "inspect/adjust it before exporting.\n"
         "Examples:\n"
         "  'count of documents per publication year for LLM papers'\n"
         "  'title, year and authors of all Risk Assessment papers from 2023'"),

        ("Data Files — identify, merge, export & sync",
         "Pick a storage space ('Select storage space…', or reuse the one selected in the "
         "Storage Spaces tab) — or 'Select folder (scan for spaces)…' to point at a folder "
         "holding SEVERAL storage spaces: they are recognized exactly like in the Storage "
         "Spaces tab and the data files of all of them are listed together, each file "
         "prefixed with its space name. Every Excel data file is listed separately by "
         "kind: publication classification, DOI metadata, processed registry, failed "
         "registry, abstract/introduction/results-conclusion logs, and evaluation overviews.\n"
         "Tick the files you want and 'Merge selected → folder…'. Files of the SAME type "
         "(e.g. several dated Title_Classification workbooks) are merged into one "
         "per-document table — newest run kept per document, the same shape as the SQL "
         "database — and written as 'merged_<type>.xlsx' into the folder you choose. Pick a "
         "group to preview it, then 'Export group…' to save it anywhere as Excel or CSV.\n"
         "'Check & update SQL…' compares each merged group with the database and, where the "
         "data isn't there yet, offers to add it: DOI and classification data fill their "
         "columns (fill-if-empty), while registry/log/evaluation data is offered as a full "
         "space sync. Nothing already in the database is overwritten.\n"
         "Example: tick two dated DOI_Metadata files + the Title_Classification files -> "
         "Merge -> merged_DOI_Metadata.xlsx and merged_Title_Classification.xlsx -> "
         "Check & update SQL -> 'Applied 15 update(s)'."),

        ("Document Inspector (single-document deep dive)",
         "Pick 'Search by' (UUID, Title or Filename), type the term and press Search. "
         "The lookup ALWAYS starts in the SQL database; every value shown carries its "
         "source. Fields that are empty in SQL are filled automatically from the storage "
         "space the document was synced from (Results & Conclusion content only lives "
         "there), each marked 'Storage space'. If the document was never synced to SQL "
         "you are asked to choose a storage space and its Processed_file_registry.xlsx "
         "is searched instead (rows marked 'Registry'). Multiple matches appear in the "
         "Matches list - click one to inspect it; click any field to read its full value "
         "in the pane below. The PDF is located from its known paths (relative_path, the "
         "space's pdf_files folder); when it isn't there, 'Locate PDF in folder…' asks "
         "for a folder and searches it and ALL nested subfolders for the recorded "
         "filename (with progress + cancel), letting you pick when several copies "
         "exist, then 'Open PDF' opens it."),
    ]

    def _build_help_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Guide")

        txt = tk.Text(tab, wrap="word", padx=14, pady=10, borderwidth=0,
                      background=ttk.Style().lookup("TFrame", "background") or "#f5f5f5")
        vsb = ttk.Scrollbar(tab, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)

        txt.tag_configure("h1", font=("TkDefaultFont", 13, "bold"), spacing3=6)
        txt.tag_configure("h2", font=("TkDefaultFont", 10, "bold"), spacing1=10, spacing3=3)
        txt.tag_configure("body", font=("TkDefaultFont", 9), spacing3=2, lmargin1=6, lmargin2=6)

        txt.insert(tk.END, "Review Tool — feature guide\n", "h1")
        txt.insert(tk.END, "How to use each feature, with examples. All data lives in the shared "
                           "SQLite review database; storage spaces on disk stay untouched unless "
                           "a feature says otherwise.\n", "body")
        for title, body in self.HELP_SECTIONS:
            txt.insert(tk.END, f"\n{title}\n", "h2")
            txt.insert(tk.END, body + "\n", "body")
        txt.config(state="disabled")
        self.help_text = txt

    # ============================================================= Data Files
    def _build_data_files_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Data Files")

        self._df_managers = []      # one DataAnalyzeManager per loaded space
        self._df_space_folders = [] # their folder paths (as str)
        self._df_scan_root = None   # folder the spaces were scanned from (multi-space mode)
        self._df_path_space = {}    # file path -> space name (for multi-space labels)
        self._df_file_vars = {}     # path -> BooleanVar
        self._df_files = {}         # category -> [paths]
        self._df_merged = {}        # type_key -> DataFrame
        self._df_out_dir = None

        bar = ttk.Frame(tab)
        bar.pack(fill="x", padx=8, pady=6)
        ttk.Button(bar, text="Select storage space…", command=self._df_select_space).pack(side="left")
        ttk.Button(bar, text="Select folder (scan for spaces)…",
                   command=self._df_select_folder).pack(side="left", padx=6)
        ttk.Button(bar, text="Use space selected in 'Storage Spaces'",
                   command=self._df_use_selected_space).pack(side="left", padx=6)
        self.df_status = ttk.Label(bar, text="Select a storage space, or scan a folder holding several.")
        self.df_status.pack(side="left", padx=10)

        # Scrollable, per-category checkbox list (each kind identified separately).
        files_frame = ttk.LabelFrame(tab, text="Data files in the loaded storage space(s) (tick the ones to merge)")
        files_frame.pack(fill="both", expand=True, padx=8, pady=4)
        canvas = tk.Canvas(files_frame, highlightthickness=0, height=200)
        vsb = ttk.Scrollbar(files_frame, orient="vertical", command=canvas.yview)
        self._df_inner = ttk.Frame(canvas)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self._df_canvas_window = canvas.create_window((0, 0), window=self._df_inner, anchor="nw")
        self._df_inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(self._df_canvas_window, width=e.width))

        selbar = ttk.Frame(tab)
        selbar.pack(fill="x", padx=8)
        ttk.Button(selbar, text="Select all", command=lambda: self._df_toggle_all(True)).pack(side="left")
        ttk.Button(selbar, text="Clear all", command=lambda: self._df_toggle_all(False)).pack(side="left", padx=6)
        ttk.Button(selbar, text="Refresh", command=self._df_scan).pack(side="left", padx=6)

        act = ttk.Frame(tab)
        act.pack(fill="x", padx=8, pady=(6, 2))
        ttk.Button(act, text="Merge selected → folder…", command=self._df_merge).pack(side="left")
        ttk.Label(act, text="Preview group:").pack(side="left", padx=(12, 2))
        self.df_group_var = tk.StringVar()
        self.df_group_combo = ttk.Combobox(act, textvariable=self.df_group_var, width=28,
                                            state="readonly", values=[])
        self.df_group_combo.pack(side="left")
        self.df_group_combo.bind("<<ComboboxSelected>>", lambda e: self._df_preview_group())
        ttk.Button(act, text="Export group…", command=self._df_export).pack(side="left", padx=6)
        ttk.Button(act, text="Check & update SQL…", command=self._df_check_sql).pack(side="left", padx=6)

        prev = ttk.LabelFrame(tab, text="Merged preview (one row per document, newest run kept)")
        prev.pack(fill="both", expand=True, padx=8, pady=4)
        self.df_tree = ttk.Treeview(prev, show="headings", height=8)
        pv = ttk.Scrollbar(prev, orient="vertical", command=self.df_tree.yview)
        ph = ttk.Scrollbar(prev, orient="horizontal", command=self.df_tree.xview)
        self.df_tree.configure(yscrollcommand=pv.set, xscrollcommand=ph.set)
        pv.pack(side="right", fill="y")
        ph.pack(side="bottom", fill="x")
        self.df_tree.pack(side="left", fill="both", expand=True)
        self.df_preview_status = ttk.Label(tab, text="")
        self.df_preview_status.pack(anchor="w", padx=10, pady=(0, 6))

    def _df_set_spaces(self, folders, scan_root=None):
        """Load one or several storage spaces into the Data Files tab."""
        from alr.common.file_manager import DataAnalyzeManager
        self._df_managers = [DataAnalyzeManager(f) for f in folders]
        self._df_space_folders = [str(m.folder) for m in self._df_managers]
        self._df_scan_root = str(scan_root) if scan_root else None
        self._df_scan()

    def _df_set_space(self, folder):
        self._df_set_spaces([folder])

    def _df_select_space(self):
        folder = filedialog.askdirectory(title="Select a storage space folder")
        if folder:
            self._df_set_space(folder)

    def _df_select_folder(self):
        """
        Scan a selected folder for storage spaces — same recognition as the
        Storage Spaces tab — and list the data files of ALL spaces found.
        """
        folder = filedialog.askdirectory(title="Select a folder to scan for storage spaces")
        if not folder:
            return
        self.container.config(cursor="watch")
        self.container.update()
        try:
            spaces = detect_storage_spaces(folder)
        finally:
            self.container.config(cursor="")
        if not spaces:
            messagebox.showinfo(
                "No storage spaces found",
                "No storage spaces were recognized under the selected folder.\n"
                "Use 'Select storage space…' to load a single space directly.")
            return
        self._df_set_spaces([s.path for s in spaces], scan_root=folder)

    def _df_use_selected_space(self):
        s = self._selected_space()
        if s:
            self._df_set_space(s.path)

    def _df_scan(self):
        if not self._df_managers:
            return
        self.container.config(cursor="watch")
        self.container.update()
        try:
            self._df_files = {c: [] for c in DATA_FILE_CATEGORIES}
            self._df_path_space = {}
            for manager in self._df_managers:
                space_name = os.path.basename(str(manager.folder).rstrip("/\\")) or str(manager.folder)
                found = discover_space_data_files(manager)
                for cat in DATA_FILE_CATEGORIES:
                    for p in found.get(cat, []):
                        self._df_files[cat].append(p)
                        self._df_path_space[p] = space_name
        finally:
            self.container.config(cursor="")

        multi = len(self._df_managers) > 1
        for w in self._df_inner.winfo_children():
            w.destroy()
        self._df_file_vars = {}
        total = 0
        for cat in DATA_FILE_CATEGORIES:
            paths = self._df_files.get(cat, [])
            lf = ttk.LabelFrame(self._df_inner, text=f"{cat}  ({len(paths)})")
            lf.pack(fill="x", expand=True, padx=4, pady=3)
            if not paths:
                ttk.Label(lf, text="— none found —", foreground="#888").pack(anchor="w", padx=6, pady=2)
                continue
            for p in paths:
                var = tk.BooleanVar(value=True)
                self._df_file_vars[p] = var
                note = ("   (question-scored; not merged)"
                        if data_file_type_key(p).lower() == "question_scored_classification" else "")
                label = os.path.basename(p) + note
                if multi:
                    label = f"[{self._df_path_space.get(p, '?')}]  {label}"
                ttk.Checkbutton(lf, text=label, variable=var).pack(anchor="w", padx=6)
                total += 1

        # Clear any stale merged state from a previous space.
        self._df_merged = {}
        self.df_group_combo["values"] = []
        self.df_group_var.set("")
        self.df_tree.delete(*self.df_tree.get_children())
        self.df_tree["columns"] = ()
        self.df_preview_status.config(text="")
        if multi:
            root = os.path.basename((self._df_scan_root or "").rstrip("/\\")) or self._df_scan_root or ""
            where = f"{len(self._df_managers)} storage space(s)" + (f" under '{root}'" if root else "")
        else:
            where = os.path.basename(self._df_space_folders[0].rstrip("/\\")) or self._df_space_folders[0]
        self.df_status.config(text=f"{where}: {total} data file(s) found across {len(DATA_FILE_CATEGORIES)} categories.")

    def _df_toggle_all(self, value):
        for var in self._df_file_vars.values():
            var.set(value)

    def _df_selected_paths(self):
        return [p for p, v in self._df_file_vars.items() if v.get()]

    def _df_merge(self):
        """Merge selected files per fine 'same type' key into a user-chosen folder."""
        if not self._df_managers:
            messagebox.showinfo("No storage space", "Select a storage space first.")
            return
        selected = self._df_selected_paths()
        if not selected:
            messagebox.showinfo("Nothing selected", "Tick at least one data file to merge.")
            return

        groups = {}
        for p in selected:
            groups.setdefault(data_file_type_key(p), []).append(p)

        out_dir = filedialog.askdirectory(title="Choose a folder to write the merged file(s) into")
        if not out_dir:
            return
        self._df_out_dir = out_dir

        self.container.config(cursor="watch")
        self.container.update()
        merged, skipped, written = {}, [], []
        try:
            for type_key, paths in groups.items():
                if type_key.lower() == "question_scored_classification":
                    skipped.append(type_key)
                    continue
                df = merge_data_files(paths)
                if df is None or df.empty:
                    continue
                merged[type_key] = df
                out_path = os.path.join(out_dir, f"merged_{type_key}.xlsx")
                try:
                    df.to_excel(out_path, index=False)
                    written.append(out_path)
                except Exception as e:
                    messagebox.showerror("Merge", f"Could not write {out_path}:\n{e}")
        finally:
            self.container.config(cursor="")

        self._df_merged = merged
        self.df_group_combo["values"] = list(merged.keys())
        if merged:
            self.df_group_var.set(next(iter(merged)))
            self._df_preview_group()
        msg = f"Merged {len(merged)} group(s) of same-type files; wrote {len(written)} file(s) to:\n{out_dir}"
        if skipped:
            msg += f"\n\nSkipped (question-scored, multi-sheet — not per-document): {', '.join(skipped)}"
        if not merged and not skipped:
            msg = "No mergeable per-document tables were produced from the selected files."
        messagebox.showinfo("Merge complete", msg)

    def _df_preview_group(self):
        key = self.df_group_var.get()
        df = self._df_merged.get(key)
        if df is None:
            return
        cols = [str(c) for c in df.columns]
        rows = df.to_dict("records")
        self._fill_tree(self.df_tree, cols, rows, limit=2000)
        self.df_preview_status.config(
            text=f"{key}: {len(rows)} document row(s)"
                 + (" (showing first 2000)" if len(rows) > 2000 else ""))

    def _df_export(self):
        """Export the currently-previewed merged group (user picks path + format)."""
        key = self.df_group_var.get()
        df = self._df_merged.get(key)
        if df is None or df.empty:
            messagebox.showinfo("Nothing to export", "Merge some files and pick a group first.")
            return
        cols = [str(c) for c in df.columns]
        rows = df.to_dict("records")
        self._export_rows(cols, rows, f"merged_{key}")

    def _df_check_sql(self):
        """
        Compare each merged group against the SQL database and, where it isn't
        already reflected, offer to update it. DOI/classification data fills the
        matching columns (fill-if-empty); registry/log/evaluation data is offered
        as a full space sync (the canonical path for those).
        """
        if not self._df_merged:
            messagebox.showinfo("Nothing merged", "Merge some files first, then check against SQL.")
            return
        if not self._df_space_folders:
            return
        from alr.common.sql_store import register_custom_column

        store = self.store
        spaces = set(self._df_space_folders)
        docs = [d for d in store.list_documents() if (d.get("source_folder") or "") in spaces]
        by_uuid = {str(d.get("uuid")): d for d in docs}
        by_fname = {str(d.get("filename")): d for d in docs}

        def _match(val, key_kind):
            return by_uuid.get(str(val)) if key_kind == "uuid" else by_fname.get(str(val))

        report, pending_meta, pending_class, need_sync = [], [], [], []
        for type_key, df in self._df_merged.items():
            kind, col = sql_target_for_type(type_key)
            if kind == "skip" or df is None or df.empty:
                continue
            key_col = pick_key_column(df)
            key_kind = "uuid" if (key_col and str(key_col).strip().lower() == "uuid") else "filename"

            if kind == "metadata":
                missing = sum(
                    1 for _, r in df.iterrows()
                    if key_col and (_match(r.get(key_col), key_kind) or {}).get("doi_link", "") in (None, "", "nan"))
                report.append(f"• {type_key}: {missing} document(s) missing DOI/metadata in SQL")
                if missing:
                    pending_meta.append(df)
            elif kind == "classification":
                topic_cols = classification_topic_columns(df)
                missing = 0
                for _, r in df.iterrows():
                    d = _match(r.get(key_col), key_kind) if key_col else None
                    if d and classification_tags(r, topic_cols) and not str(d.get(col) or "").strip():
                        missing += 1
                report.append(f"• {type_key}: {missing} document(s) missing '{col}' in SQL")
                if missing:
                    pending_class.append((df, key_col, key_kind, col, topic_cols))
            else:  # sync
                report.append(f"• {type_key}: reflected in SQL by syncing the storage space")
                need_sync.append(type_key)

        if not report:
            messagebox.showinfo("Check SQL", "Nothing comparable was found in the merged groups.")
            return

        body = "Comparison of merged data against the SQL database:\n\n" + "\n".join(report)

        if pending_meta or pending_class:
            if messagebox.askyesno(
                    "Update SQL",
                    body + "\n\nApply the DOI/classification updates above to SQL now?\n"
                           "(fill-if-empty — values already in the database are never overwritten)"):
                updated = 0
                for (_df, _kc, _kk, col, _tc) in pending_class:
                    try:
                        register_custom_column(col, db_path=store.db_path)  # no-op for built-ins
                    except Exception:
                        pass
                for df in pending_meta:
                    try:
                        updated += store.merge_metadata_workbook(df)
                    except Exception as e:
                        messagebox.showerror("Update SQL", f"Metadata merge failed: {e}")
                for (df, key_col, key_kind, col, topic_cols) in pending_class:
                    for _, r in df.iterrows():
                        d = _match(r.get(key_col), key_kind) if key_col else None
                        if not d:
                            continue
                        tags = classification_tags(r, topic_cols)
                        if tags and not str(d.get(col) or "").strip():
                            store.update_document(d["uuid"], {col: tags})
                            updated += 1
                messagebox.showinfo("Update SQL", f"Applied {updated} update(s) to the SQL database.")
                self._refresh_all()
        else:
            messagebox.showinfo("Check SQL", body)

        folders = list(self._df_space_folders)
        if need_sync and messagebox.askyesno(
                "Sync storage space(s)",
                "Registry / log / evaluation data is reflected in SQL by syncing the whole "
                "storage space (it upserts document rows and fills DOI/evaluation/classification, "
                "never overwriting existing values).\n\n"
                f"Sync the {len(folders)} loaded space(s) now?"):

            def work(progress, should_cancel):
                synced = 0
                for i, folder in enumerate(folders, 1):
                    if should_cancel():
                        break
                    progress(done=i, total=len(folders),
                             text=f"[{i}/{len(folders)}] Syncing '{os.path.basename(folder)}' into the database…")
                    synced += sync_storage_to_sql(folder, db_path=store.db_path)
                return synced

            self._run_threaded(work, "Sync storage space(s)", "synced")

    # ================================================================ shared
    def _refresh_all(self):
        if hasattr(self, "review_view"):
            self.review_view.refresh()
        if hasattr(self, "filter_source"):
            self.filter_source["values"] = [""] + self.store.list_source_folders()
        if hasattr(self, "stats_label"):
            self._refresh_database_tab()


def open_review_app(master=None):
    """Open the Review tool in its own window (Toplevel child, or standalone root)."""
    win = tk.Toplevel(master) if master is not None else tk.Tk()
    if master is None:
        # Standalone root: no main window installed the crash hooks for us.
        # (As a Toplevel, callback exceptions route to the main app's root.)
        crash_logger.install("Automated Literature Review — Review Tool")
        crash_logger.attach_to_tk(win)
    win.title("Automated Literature Review — Review Tool")
    win.geometry("1050x760")
    # Same clam theme as the main tool (the theme is interpreter-wide, so a
    # Toplevel opened from the main app already has it; this covers the
    # standalone review_main.py launch).
    try:
        ttk.Style(win).theme_use("clam")
    except tk.TclError:
        pass
    ReviewApp(win)
    return win
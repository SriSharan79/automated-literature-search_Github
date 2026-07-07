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
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

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
        self._build_database_tab()
        self._build_overviews_tab()
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

        # Download logs
        dl_frame = ttk.LabelFrame(tab, text="Download logs (*_download_log.xlsx)")
        dl_frame.pack(fill="both", expand=True, padx=8, pady=4)
        self.logs_list = tk.Listbox(dl_frame, height=4)
        self.logs_list.pack(side="left", fill="both", expand=True, padx=(0, 4))
        ttk.Button(dl_frame, text="Import bibliographic data", command=self._import_download_log).pack(side="right", padx=4, pady=4)

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
                 f"{len(self.spaces) - n_complete} partial); {len(self.download_logs)} download log(s).")

    def _selected_space(self):
        sel = self.spaces_tree.selection()
        if not sel:
            messagebox.showinfo("No selection", "Select a storage space first.")
            return None
        return self.spaces[int(sel[0])]

    def _run_threaded(self, work, title, result_word="processed"):
        """
        Run ``work(progress, should_cancel)`` on a background thread with a modal
        progress dialog (with a Cancel button). The worker only communicates
        through a thread-safe queue; all Tk access happens on the main thread via
        a poller scheduled with ``after`` (calling Tk from a worker thread is
        unsafe). ``progress(done=?, total=?, text=?)`` enqueues an update;
        ``should_cancel()`` returns True once Cancel is pressed; ``work`` returns
        an int count. On completion a result/cancel/error message is shown and
        the views are refreshed.
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
                q.put(("error", e))

        def finish():
            dlg.close()
            if "error" in outcome:
                messagebox.showerror(title, str(outcome["error"]))
            elif cancel_event.is_set():
                messagebox.showinfo(title, f"{title}: cancelled after {result_word} "
                                           f"{outcome.get('n', 0)} document(s).")
            else:
                messagebox.showinfo(title, f"{title}: {result_word} {outcome.get('n', 0)} document(s).")
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
                        outcome["error"] = payload
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
        import pandas as pd
        sel = self.logs_list.curselection()
        if not sel:
            messagebox.showinfo("No selection", "Select a download log first.")
            return
        path = self.download_logs[sel[0]]
        try:
            df = pd.read_excel(path)
            n = self.store.merge_download_log(df)
            messagebox.showinfo("Import download log", f"Updated {n} document(s) with bibliographic data.")
        except Exception as e:
            messagebox.showerror("Import download log", str(e))
        self._refresh_all()

    # ============================================================= Documents
    def _build_documents_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Documents")
        self.review_view = ReviewDataView(tab)

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
         "markers are missing. It also lists any *_download_log.xlsx files found.\n"
         "Example: select 'D:/ALR DATA' -> 3 spaces found (2 complete, 1 partial), "
         "1 download log."),

        ("Storage Spaces — Link to database / Link ALL",
         "Imports a space's registry + analyzed JSON files into the SQLite database so the "
         "Documents/Database/Overviews tabs can see them. Safe to repeat: re-linking updates "
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
         "Select one of the discovered *_download_log.xlsx files and click 'Import "
         "bibliographic data'. Rows are matched by File_Name to documents already in the "
         "database and fill link / authors / publication year / first author — only where "
         "the document has no value yet.\n"
         "Example: '2025-06-12_download_log.xlsx' -> 'Updated 17 document(s)'."),

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
    win.title("Automated Literature Review — Review Tool")
    win.geometry("1050x760")
    ReviewApp(win)
    return win

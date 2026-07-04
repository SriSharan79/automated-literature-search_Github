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
from alr.common.file_manager import DataAnalyzeManager, ALR_overviews_folder
from alr.common.LLM_Config import get_stored_api_key
from alr.ui.desktop.review_view import ReviewDataView, open_path

# Sensible default columns to show pre-checked in the overview builder.
DEFAULT_OVERVIEW_FIELDS = [
    "title", "filename", "publication_year", "publication_type",
    "classification", "first_author", "doi_link", "research_areas", "source_folder",
]


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
            # Switch to a determinate bar once we know the item count.
            if str(self.bar.cget("mode")) != "determinate":
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
        self._build_overviews_tab()

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
        ttk.Button(act, text="Classify publications", command=self._classify_selected).pack(side="left", padx=3)
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
        from alr.analysis_evaluation.publication_classification.classify_runner import classify_space

        def work(progress, should_cancel):
            progress(text=f"Classifying publications in '{os.path.basename(s.path)}'…")
            return classify_space(
                s.path, db_path=self.store.db_path, should_cancel=should_cancel,
                progress_callback=lambda done, total: progress(
                    done=done, total=total, text=f"Classifying publications…  {done}/{total}"),
            )

        self._run_threaded(work, "Classify publications", "classified")

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

        btns = ttk.Frame(tab)
        btns.pack(fill="x", padx=8, pady=4)
        ttk.Button(btns, text="Preview", command=self._preview_overview).pack(side="left", padx=3)
        ttk.Button(btns, text="Export Excel", command=lambda: self._export_overview("xlsx")).pack(side="left", padx=3)
        ttk.Button(btns, text="Export CSV", command=lambda: self._export_overview("csv")).pack(side="left", padx=3)
        self.overview_status = ttk.Label(btns, text="")
        self.overview_status.pack(side="right", padx=6)

        # Preview table
        prev = ttk.Frame(tab)
        prev.pack(fill="both", expand=True, padx=8, pady=4)
        self.overview_tree = ttk.Treeview(prev, show="headings")
        ovsb = ttk.Scrollbar(prev, orient="vertical", command=self.overview_tree.yview)
        ovhsb = ttk.Scrollbar(prev, orient="horizontal", command=self.overview_tree.xview)
        self.overview_tree.configure(yscrollcommand=ovsb.set, xscrollcommand=ovhsb.set)
        ovsb.pack(side="right", fill="y")
        ovhsb.pack(side="bottom", fill="x")
        self.overview_tree.pack(side="left", fill="both", expand=True)

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
        return f

    def _preview_overview(self):
        fields = self._selected_fields()
        rows = self.store.build_overview(fields, self._current_filters())
        self.overview_tree.delete(*self.overview_tree.get_children())
        self.overview_tree["columns"] = fields
        for c in fields:
            self.overview_tree.heading(c, text=c)
            self.overview_tree.column(c, width=max(80, min(240, len(c) * 12)), anchor="w")
        for r in rows[:1000]:
            self.overview_tree.insert("", "end", values=[r.get(c) for c in fields])
        self.overview_status.config(text=f"{len(rows)} row(s)" + (" (showing first 1000)" if len(rows) > 1000 else ""))
        self._last_overview = (fields, rows)

    def _export_overview(self, kind):
        fields = self._selected_fields()
        rows = self.store.build_overview(fields, self._current_filters())
        if not rows:
            messagebox.showinfo("Nothing to export", "The overview is empty.")
            return
        os.makedirs(ALR_overviews_folder, exist_ok=True)
        default_ext = ".xlsx" if kind == "xlsx" else ".csv"
        path = filedialog.asksaveasfilename(
            title="Export overview", defaultextension=default_ext,
            initialdir=str(ALR_overviews_folder), initialfile=f"overview{default_ext}",
            filetypes=[("Excel files", "*.xlsx")] if kind == "xlsx" else [("CSV files", "*.csv")])
        if not path:
            return
        try:
            if kind == "xlsx":
                import pandas as pd
                pd.DataFrame(rows, columns=fields).to_excel(path, index=False)
            else:
                with open(path, "w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                    w.writeheader()
                    w.writerows(rows)
            messagebox.showinfo("Exported", f"Exported {len(rows)} row(s) to:\n{path}")
        except Exception as e:
            messagebox.showerror("Export failed", str(e))

    # ================================================================ shared
    def _refresh_all(self):
        if hasattr(self, "review_view"):
            self.review_view.refresh()
        if hasattr(self, "filter_source"):
            self.filter_source["values"] = [""] + self.store.list_source_folders()


def open_review_app(master=None):
    """Open the Review tool in its own window (Toplevel child, or standalone root)."""
    win = tk.Toplevel(master) if master is not None else tk.Tk()
    win.title("Automated Literature Review — Review Tool")
    win.geometry("1050x760")
    ReviewApp(win)
    return win

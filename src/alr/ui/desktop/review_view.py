"""
alr.ui.desktop.review_view
==========================

An editable "Review Data" view for browsing and curating analyzed documents
stored in the SQLite database (:mod:`alr.common.sql_store`). Built as a
self-contained widget class (like ``section_rewriter_view.JSONRestructurerUI``)
so it can be embedded as a notebook tab or popped out into its own window.
"""

import json
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from alr.common.sql_store import (
    AnalyzedDataStore,
    SECTION_COLUMNS,
    LIST_SECTIONS,
    sync_storage_to_sql,
)
from alr.common.file_manager import DataAnalyzeManager, ALR_data_analyze_folder

# Section (key, column) pairs in canonical order.
SECTION_ORDER = list(SECTION_COLUMNS.items())


def open_path(path):
    """Open a file or folder with the OS default application."""
    path = str(path or "")
    if not path or not os.path.exists(path):
        messagebox.showwarning("Not found", f"Path does not exist:\n{path}")
        return
    try:
        if sys.platform.startswith("darwin"):
            subprocess.run(["open", path], check=False)
        elif os.name == "nt":
            os.startfile(path)  # noqa: S606 (Windows default open)
        else:
            subprocess.run(["xdg-open", path], check=False)
    except Exception as e:
        messagebox.showerror("Open failed", str(e))


class ReviewDataView:
    """Treeview list + editable detail panel over the analyzed-document store."""

    def __init__(self, parent):
        self.parent = parent
        self.store = AnalyzedDataStore()
        self.current_doc = None
        self._section_widgets = {}  # column -> Text widget
        self._build()
        self.refresh()

    # ------------------------------------------------------------------ UI
    def _build(self):
        # Toolbar
        toolbar = ttk.Frame(self.parent)
        toolbar.pack(fill="x", padx=8, pady=6)
        ttk.Label(toolbar, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(toolbar, textvariable=self.search_var, width=30)
        search_entry.pack(side="left", padx=4)
        search_entry.bind("<Return>", lambda e: self.refresh())
        ttk.Button(toolbar, text="Search", command=self.refresh).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Clear", command=self._clear_search).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Sync from storage folder...",
                   command=self._sync_action).pack(side="left", padx=8)
        self.count_lbl = ttk.Label(toolbar, text="")
        self.count_lbl.pack(side="right")

        # Bottom action bar (stays at bottom)
        actions = ttk.Frame(self.parent)
        actions.pack(side="bottom", fill="x", padx=8, pady=6)
        ttk.Button(actions, text="Save Changes", command=self._save).pack(side="left", padx=3)
        ttk.Button(actions, text="Open PDF", command=self._open_pdf).pack(side="left", padx=3)
        ttk.Button(actions, text="Open Abstract JSON", command=self._open_json).pack(side="left", padx=3)
        ttk.Button(actions, text="Delete", command=self._delete).pack(side="right", padx=3)

        # Main split: left table / right editable detail
        paned = ttk.PanedWindow(self.parent, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=8, pady=4)

        # -- left: documents table
        left = ttk.Frame(paned)
        paned.add(left, weight=1)
        cols = ("uuid", "title", "filename", "timestamp", "abstract", "intro")
        headers = {"uuid": "UUID", "title": "Title", "filename": "File",
                   "timestamp": "Processed", "abstract": "Abs", "intro": "Intro"}
        widths = {"uuid": 90, "title": 220, "filename": 130, "timestamp": 130,
                  "abstract": 45, "intro": 45}
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        for c in cols:
            self.tree.heading(c, text=headers[c])
            self.tree.column(c, width=widths[c], anchor="w")
        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # -- right: detail
        right = ttk.Frame(paned)
        paned.add(right, weight=2)

        title_frame = ttk.Frame(right)
        title_frame.pack(fill="x", pady=3)
        ttk.Label(title_frame, text="Title:", width=12).pack(side="left")
        self.title_var = tk.StringVar()
        ttk.Entry(title_frame, textvariable=self.title_var).pack(side="left", fill="x", expand=True)

        # Scrollable section editors
        canvas = tk.Canvas(right, highlightthickness=0)
        sform = ttk.Frame(canvas)
        sb = ttk.Scrollbar(right, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        cwin = canvas.create_window((0, 0), window=sform, anchor="nw")
        sform.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(cwin, width=e.width))

        for key, col in SECTION_ORDER:
            suffix = " (one per line)" if key in LIST_SECTIONS else ""
            lf = ttk.LabelFrame(sform, text=key + suffix)
            lf.pack(fill="x", expand=True, padx=4, pady=3)
            txt = tk.Text(lf, height=3, wrap="word")
            txt.pack(fill="x", expand=True)
            self._section_widgets[col] = txt

        abs_lf = ttk.LabelFrame(sform, text="Abstract Text (read-only)")
        abs_lf.pack(fill="x", expand=True, padx=4, pady=3)
        self.abstract_view = tk.Text(abs_lf, height=4, wrap="word", background="#f0f0f0")
        self.abstract_view.pack(fill="x", expand=True)

    # --------------------------------------------------------------- data
    def refresh(self):
        """Reload the document list from the DB (respecting the search box)."""
        search = self.search_var.get().strip() or None
        for item in self.tree.get_children():
            self.tree.delete(item)
        docs = self.store.list_documents(search)
        for d in docs:
            self.tree.insert(
                "", "end", iid=d["uuid"],
                values=(
                    (d["uuid"] or "")[:8],
                    d.get("title") or "",
                    d.get("filename") or "",
                    d.get("timestamp") or "",
                    d.get("status_abstract") or "",
                    d.get("status_introduction") or "",
                ),
            )
        self.count_lbl.config(text=f"{len(docs)} document(s)")

    def _clear_search(self):
        self.search_var.set("")
        self.refresh()

    def _on_select(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        doc = self.store.get_document(sel[0])
        if not doc:
            return
        self.current_doc = doc
        self.title_var.set(doc.get("title") or "")
        for key, col in SECTION_ORDER:
            widget = self._section_widgets[col]
            widget.delete("1.0", tk.END)
            widget.insert("1.0", self._decode_section(key, doc.get(col)))
        self.abstract_view.config(state="normal")
        self.abstract_view.delete("1.0", tk.END)
        self.abstract_view.insert("1.0", doc.get("abstract_text") or "")
        self.abstract_view.config(state="disabled")

    @staticmethod
    def _decode_section(key, value):
        """DB value -> text shown in the editor (lists become one-per-line)."""
        if value is None:
            return ""
        if key in LIST_SECTIONS:
            try:
                items = json.loads(value)
                if isinstance(items, list):
                    return "\n".join(str(i) for i in items)
            except (json.JSONDecodeError, TypeError):
                pass
        return str(value)

    @staticmethod
    def _encode_section(key, text):
        """Editor text -> DB value (list sections become a JSON array)."""
        if key in LIST_SECTIONS:
            items = [line.strip() for line in text.splitlines() if line.strip()]
            return json.dumps(items)
        return text.strip()

    # ------------------------------------------------------------ actions
    def _save(self):
        if not self.current_doc:
            messagebox.showinfo("No selection", "Select a document to edit first.")
            return
        fields = {"title": self.title_var.get().strip()}
        for key, col in SECTION_ORDER:
            fields[col] = self._encode_section(key, self._section_widgets[col].get("1.0", tk.END))
        self.store.update_document(self.current_doc["uuid"], fields)
        messagebox.showinfo("Saved", "Changes saved to the database.")
        # keep selection, refresh row text
        uuid = self.current_doc["uuid"]
        self.refresh()
        if self.tree.exists(uuid):
            self.tree.selection_set(uuid)

    def _sync_action(self):
        folder = filedialog.askdirectory(
            title="Select an Analyzed-Data storage folder to import",
            initialdir=str(ALR_data_analyze_folder) if os.path.isdir(ALR_data_analyze_folder) else None,
        )
        if not folder:
            return
        try:
            n = sync_storage_to_sql(folder, db_path=self.store.db_path)
            messagebox.showinfo("Sync complete", f"Synced {n} document(s) into the database.")
        except Exception as e:
            messagebox.showerror("Sync failed", str(e))
        self.refresh()

    def _open_pdf(self):
        if not self.current_doc:
            return
        open_path(self.current_doc.get("relative_path"))

    def _open_json(self):
        if not self.current_doc:
            return
        source = self.current_doc.get("source_folder")
        uuid = self.current_doc.get("uuid")
        if not source:
            messagebox.showwarning("Unavailable", "No source folder recorded for this document.")
            return
        mf = DataAnalyzeManager(source)
        open_path(os.path.join(mf.AD_Abstract, f"{uuid}_Abstract.json"))

    def _delete(self):
        if not self.current_doc:
            return
        if not messagebox.askyesno("Confirm delete",
                                   f"Remove '{self.current_doc.get('title')}' from the database?\n"
                                   "(This does not delete any files on disk.)"):
            return
        self.store.delete_document(self.current_doc["uuid"])
        self.current_doc = None
        self.refresh()


def open_review_window(master=None):
    """Open the review view in its own top-level window (for side-by-side use)."""
    win = tk.Toplevel(master) if master is not None else tk.Tk()
    win.title("Review Analyzed Data")
    win.geometry("1000x700")
    ReviewDataView(win)
    return win

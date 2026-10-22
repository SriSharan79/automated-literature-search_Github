"""
alr.ui.desktop.query_result_viewer
==================================

Three-column interactive viewer for exploring documents returned by a RAG query:
  Column 1 (Left)   : Sortable table of matched documents (UUID, Title, Year, Link).
  Column 2 (Middle) : Section selector buttons (Abstract, Introduction,
                      Results & Conclusion, DOI Metadata) + formatted data view.
  Column 3 (Right)  : Toggle buttons (Tables, Images) + file list + live preview
                      (tabular/text view for tables, scaled canvas preview for images).
"""

import json
import os
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox
import pandas as pd

from alr.common.sql_store import (
    AnalyzedDataStore,
    SECTION_COLUMNS,
    INTRO_SECTION_COLUMNS,
    RESCON_SECTION_COLUMNS,
    LIST_SECTIONS,
)
from alr.common.file_manager import DataAnalyzeManager
from alr.ui.desktop.review_view import open_path, resolve_doc_target_path


class QueryResultViewer:
    """Three-pane vertical-split window for inspecting RAG query results."""

    def __init__(self, parent, docs_or_excel_path, title="Query Results Explorer"):
        self.win = tk.Toplevel(parent) if parent is not None else tk.Tk()
        self.win.title(title)
        self.win.geometry("1380x820")
        self.win.minsize(1050, 650)

        self.store = AnalyzedDataStore()
        self.docs_by_uuid = {}
        self.current_doc = None
        self.current_section_mode = tk.StringVar(value="abstract")
        self.current_media_mode = tk.StringVar(value="tables")
        self._media_files = []  # List of Path objects for current media list
        self._tk_img_ref = None  # Keep reference to prevent GC of PhotoImage

        self._load_records(docs_or_excel_path)
        self._build_ui()
        self._populate_doc_table()

    # ------------------------------------------------------------------ Data Loading
    def _load_records(self, source):
        """
        Accept either:
          - A path to the generated *_SQL_Database_Export.xlsx / *_query_Overview_report.xlsx
          - A list of document dicts / UUID strings
        Hydrates full records from AnalyzedDataStore so all sections are available.
        """
        all_db_docs = self.store.list_documents()

        def _clean(val):
            s = str(val or "").strip()
            return s[:-2] if s.endswith(".0") else s

        by_uuid = {_clean(d.get("uuid")): d for d in all_db_docs if d.get("uuid")}
        by_fname = {_clean(d.get("filename")): d for d in all_db_docs if d.get("filename")}

        if isinstance(source, (str, Path)) and os.path.exists(source):
            try:
                df = pd.read_excel(source)
            except Exception as e:
                messagebox.showerror("Error loading Excel", f"Could not read {source}:\n{e}", parent=self.win)
                return

            uuid_col = next((c for c in ("uuid", "Original_UUID", "UUID") if c in df.columns), None)
            fname_col = next((c for c in ("filename", "Filename", "File_Name") if c in df.columns), None)

            for _, row in df.iterrows():
                uid = _clean(row.get(uuid_col)) if uuid_col else ""
                fname = _clean(row.get(fname_col)) if fname_col else ""
                doc = by_uuid.get(uid) or by_fname.get(fname)
                if doc:
                    self.docs_by_uuid[doc["uuid"]] = doc
                elif uid:
                    # Fallback to whatever columns the Excel row itself holds
                    row_dict = {str(k).lower(): v for k, v in row.to_dict().items()}
                    row_dict["uuid"] = uid
                    self.docs_by_uuid[uid] = row_dict

        elif isinstance(source, list):
            for item in source:
                if isinstance(item, dict):
                    uid = _clean(item.get("uuid") or item.get("Original_UUID"))
                    doc = by_uuid.get(uid) or item
                    if uid:
                        self.docs_by_uuid[uid] = doc
                else:
                    uid = _clean(item)
                    if uid in by_uuid:
                        self.docs_by_uuid[uid] = by_uuid[uid]

    # ------------------------------------------------------------------ UI Construction
    def _build_ui(self):
        # Top header bar
        top_bar = ttk.Frame(self.win)
        top_bar.pack(fill="x", padx=10, pady=(8, 4))
        ttk.Label(top_bar, text="Query Results Explorer", font=("TkDefaultFont", 11, "bold")).pack(side="left")
        self.status_lbl = ttk.Label(top_bar, text=f"{len(self.docs_by_uuid)} document(s) loaded", foreground="#555")
        self.status_lbl.pack(side="left", padx=12)
        ttk.Button(top_bar, text="Open Selected PDF", command=self._open_selected_pdf).pack(side="right", padx=4)

        # 3-Column Horizontal PanedWindow (Vertical splitters)
        self.paned = ttk.PanedWindow(self.win, orient="horizontal")
        self.paned.pack(fill="both", expand=True, padx=8, pady=6)

        # ==============================================================
        # COLUMN 1 (1/3): SQL Report Table (UUID, Title, Year, Link)
        # ==============================================================
        col1 = ttk.LabelFrame(self.paned, text="1. Matched Publications (SQL Report)")
        self.paned.add(col1, weight=1)

        search_row = ttk.Frame(col1)
        search_row.pack(fill="x", padx=6, pady=4)
        ttk.Label(search_row, text="Filter:").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._populate_doc_table())
        ttk.Entry(search_row, textvariable=self.filter_var).pack(side="left", fill="x", expand=True, padx=4)

        tree_frame = ttk.Frame(col1)
        tree_frame.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        cols = ("uuid", "title", "year", "link")
        headers = {"uuid": "UUID", "title": "Title", "year": "Year", "link": "DOI / Link"}
        widths = {"uuid": 85, "title": 210, "year": 55, "link": 140}

        self.doc_tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="browse")
        for c in cols:
            self.doc_tree.heading(c, text=headers[c], command=lambda _c=c: self._sort_tree(self.doc_tree, _c, False))
            self.doc_tree.column(c, width=widths[c], anchor="w", stretch=(c in ("title", "link")))

        vsb1 = ttk.Scrollbar(tree_frame, orient="vertical", command=self.doc_tree.yview)
        hsb1 = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.doc_tree.xview)
        self.doc_tree.configure(yscrollcommand=vsb1.set, xscrollcommand=hsb1.set)
        vsb1.pack(side="right", fill="y")
        hsb1.pack(side="bottom", fill="x")
        self.doc_tree.pack(side="left", fill="both", expand=True)

        self.doc_tree.bind("<<TreeviewSelect>>", self._on_doc_select)
        self.doc_tree.bind("<Double-1>", self._on_doc_double_click)

        # ==============================================================
        # COLUMN 2 (2/3): Section Buttons + Section Data Display
        # ==============================================================
        col2 = ttk.LabelFrame(self.paned, text="2. Section Data Review")
        self.paned.add(col2, weight=1)

        btn_bar2 = ttk.Frame(col2)
        btn_bar2.pack(fill="x", padx=6, pady=6)

        sections = [
            ("Abstract", "abstract"),
            ("Introduction", "intro"),
            ("Res & Con", "rescon"),
            ("DOI Metadata", "doi"),
        ]
        for label, mode in sections:
            ttk.Radiobutton(
                btn_bar2,
                text=label,
                value=mode,
                variable=self.current_section_mode,
                command=self._render_section_data,
            ).pack(side="left", padx=4)

        self.sec_title_lbl = ttk.Label(col2, text="Select a publication in Column 1", font=("TkDefaultFont", 9, "bold"))
        self.sec_title_lbl.pack(anchor="w", padx=8, pady=(0, 4))

        txt_frame2 = ttk.Frame(col2)
        txt_frame2.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        self.section_text = tk.Text(txt_frame2, wrap="word", padx=8, pady=8, state="disabled", font=("TkDefaultFont", 10))
        vsb2 = ttk.Scrollbar(txt_frame2, orient="vertical", command=self.section_text.yview)
        self.section_text.configure(yscrollcommand=vsb2.set)
        vsb2.pack(side="right", fill="y")
        self.section_text.pack(side="left", fill="both", expand=True)

        self.section_text.tag_configure("h1", font=("TkDefaultFont", 11, "bold"), foreground="#1f4e78", spacing1=6, spacing3=4)
        self.section_text.tag_configure("h2", font=("TkDefaultFont", 10, "bold"), foreground="#2e75b6", spacing1=8, spacing3=2)
        self.section_text.tag_configure("body", font=("TkDefaultFont", 10), lmargin1=8, lmargin2=8, spacing3=4)
        self.section_text.tag_configure("muted", font=("TkDefaultFont", 9, "italic"), foreground="#666")

        # ==============================================================
        # COLUMN 3 (3/3): Tables & Images Buttons + List + Viewer
        # ==============================================================
        col3 = ttk.LabelFrame(self.paned, text="3. Extracted Tables & Images")
        self.paned.add(col3, weight=1)

        btn_bar3 = ttk.Frame(col3)
        btn_bar3.pack(fill="x", padx=6, pady=6)

        ttk.Radiobutton(
            btn_bar3, text="Tables", value="tables",
            variable=self.current_media_mode, command=self._refresh_media_list
        ).pack(side="left", padx=4)
        ttk.Radiobutton(
            btn_bar3, text="Images", value="images",
            variable=self.current_media_mode, command=self._refresh_media_list
        ).pack(side="left", padx=4)

        ttk.Button(btn_bar3, text="Open File Externally", command=self._open_selected_media_external).pack(side="right", padx=4)
        ttk.Button(
            btn_bar3,
            text="Locate Folder…",
            command=self._manual_locate_media_folder,
        ).pack(side="right", padx=2)

        # Vertical split inside Column 3: Top = List of items, Bottom = Selected item viewer
        col3_split = ttk.PanedWindow(col3, orient="vertical")
        col3_split.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        # Top pane: Listbox of tables or images
        list_frame = ttk.Frame(col3_split)
        col3_split.add(list_frame, weight=1)

        self.media_status_lbl = ttk.Label(list_frame, text="No document selected.", foreground="#555")
        self.media_status_lbl.pack(anchor="w", pady=(0, 2))

        lb_holder = ttk.Frame(list_frame)
        lb_holder.pack(fill="both", expand=True)
        self.media_listbox = tk.Listbox(lb_holder, height=6, exportselection=False)
        vsb3_list = ttk.Scrollbar(lb_holder, orient="vertical", command=self.media_listbox.yview)
        self.media_listbox.configure(yscrollcommand=vsb3_list.set)
        vsb3_list.pack(side="right", fill="y")
        self.media_listbox.pack(side="left", fill="both", expand=True)
        self.media_listbox.bind("<<ListboxSelect>>", self._on_media_select)

        # Bottom pane: Dynamic preview area (holds either a Table/Text preview or an Image Canvas)
        self.preview_container = ttk.LabelFrame(col3_split, text="Preview")
        col3_split.add(self.preview_container, weight=3)

    # ------------------------------------------------------------------ Column 1 Logic
    def _populate_doc_table(self):
        self.doc_tree.delete(*self.doc_tree.get_children())
        flt = self.filter_var.get().strip().lower()

        for uuid, d in self.docs_by_uuid.items():
            title = str(d.get("title") or "")
            year = str(d.get("publication_year") or d.get("year") or "")
            if year.endswith(".0"):
                year = year[:-2]
            link = str(d.get("doi_link") or d.get("link") or "")

            if flt and not any(flt in s.lower() for s in (uuid, title, year, link)):
                continue

            self.doc_tree.insert("", "end", iid=uuid, values=(uuid[:10], title, year, link))

        children = self.doc_tree.get_children()
        if children and self.current_doc is None:
            self.doc_tree.selection_set(children[0])
            self._on_doc_select()
            
    def _manual_locate_media_folder(self):
        """Prompt user with the SQL source_folder and resolve Tables/Images or Section JSONs."""
        if not self.current_doc:
            messagebox.showinfo("No selection", "Select a publication in Column 1 first.", parent=self.win)
            return
        mode = self.current_media_mode.get()
        folder = self._resolve_media_folder(self.current_doc, mode, prompt_if_missing=True)
        if folder:
            self._render_section_data()
            self._refresh_media_list()

    def _sort_tree(self, tv, col, reverse):
        items = [(tv.set(k, col), k) for k in tv.get_children("")]
        try:
            items.sort(key=lambda t: float(t[0]), reverse=reverse)
        except ValueError:
            items.sort(key=lambda t: str(t[0]).lower(), reverse=reverse)
        for idx, (_, k) in enumerate(items):
            tv.move(k, "", idx)
        tv.heading(col, command=lambda: self._sort_tree(tv, col, not reverse))

    def _on_doc_select(self, _event=None):
        sel = self.doc_tree.selection()
        if not sel:
            return
        uuid = sel[0]
        self.current_doc = self.docs_by_uuid.get(uuid)
        if not self.current_doc:
            return
        self._render_section_data()
        self._refresh_media_list()

    def _on_doc_double_click(self, _event=None):
        """Double-clicking a row with a link opens the DOI/URL in the browser."""
        if not self.current_doc:
            return
        link = str(self.current_doc.get("doi_link") or self.current_doc.get("link") or "").strip()
        if link and link.lower() not in ("none", "nan", "n/a"):
            import webbrowser
            webbrowser.open(link if link.startswith("http") else f"https://doi.org/{link}")

    def _open_selected_pdf(self):
        if not self.current_doc:
            messagebox.showinfo("No selection", "Select a publication in Column 1 first.", parent=self.win)
            return
        path = resolve_doc_target_path(self.current_doc, "pdf", store=self.store, parent=self.win)
        if path:
            open_path(path)

    def _load_fallback_json(self, doc, kind, prompt_if_missing=False):
        """
        Read section JSON from disk if SQL columns are empty.
        If `prompt_if_missing` is True and the file path cannot be identified,
        alerts the user with the SQL `source_folder` and asks them to choose it manually.
        """
        source = doc.get("source_folder")
        uuid = doc.get("uuid")
        if source and uuid and os.path.isdir(source):
            mf = DataAnalyzeManager(source)
            mf.update_id_files(uuid)
            path_map = {
                "abstract": mf.abstract_json_path,
                "intro": mf.intro_json_path,
                "rescon": mf.rescon_json_path,
            }
            p = path_map.get(kind)
            if p and os.path.isfile(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    return {}

        if prompt_if_missing:
            resolved = resolve_doc_target_path(doc, kind, store=self.store, parent=self.win)
            if resolved and os.path.isfile(resolved):
                try:
                    with open(resolved, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception as e:
                    messagebox.showerror("Read Error", f"Could not parse JSON file:\n{resolved}\n\n{e}", parent=self.win)
        return {}

    def _resolve_media_folder(self, doc, mode, prompt_if_missing=False):
        """
        Locate the document's per-ID Tables or Images folder via DataAnalyzeManager.
        Prompts the user to relocate the storage space if `prompt_if_missing` is True.
        """
        source = doc.get("source_folder")
        uuid = doc.get("uuid")
        if source and uuid and os.path.isdir(source):
            mf = DataAnalyzeManager(source)
            mf.update_id_files(uuid)
            target = mf.tables_storage_path if mode == "tables" else mf.image_storage_path
            if target and Path(target).is_dir():
                return Path(target)

        if prompt_if_missing:
            resolved = resolve_doc_target_path(doc, mode, store=self.store, parent=self.win)
            if resolved and Path(resolved).is_dir():
                return Path(resolved)
        return None

    @staticmethod
    def _format_val(val):
        if val is None:
            return ""
        s = str(val).strip()
        if not s or s.lower() in ("none", "nan"):
            return ""
        if s.startswith("[") and s.endswith("]"):
            try:
                arr = json.loads(s)
                if isinstance(arr, list):
                    return "\n".join(f"• { item }" for item in arr if str(item).strip())
            except Exception:
                pass
        return s

    def _render_section_data(self):
        self.section_text.configure(state="normal")
        self.section_text.delete("1.0", tk.END)

        doc = self.current_doc
        if not doc:
            self.section_text.insert(tk.END, "Select a publication from Column 1.", "muted")
            self.section_text.configure(state="disabled")
            return

        title = doc.get("title") or doc.get("filename") or doc.get("uuid")
        self.sec_title_lbl.config(text=f"{title[:75]}{'…' if len(str(title)) > 75 else ''}")

        mode = self.current_section_mode.get()

        if mode == "abstract":
            self.section_text.insert(tk.END, "Abstract Analysis\n", "h1")
            disk_json = self._load_fallback_json(doc, "abstract")
            for key, col in SECTION_COLUMNS.items():
                val = self._format_val(doc.get(col)) or self._format_val(disk_json.get(key))
                self.section_text.insert(tk.END, f"{key}\n", "h2")
                self.section_text.insert(tk.END, f"{val or '— Not available —'}\n", "body" if val else "muted")

            raw_abs = self._format_val(doc.get("abstract_text")) or self._format_val(disk_json.get("Abstract Text identified:"))
            self.section_text.insert(tk.END, "Identified Abstract Text\n", "h2")
            self.section_text.insert(tk.END, f"{raw_abs or '— Not available —'}\n", "body" if raw_abs else "muted")

        elif mode == "intro":
            self.section_text.insert(tk.END, "Introduction Analysis\n", "h1")
            disk_json = self._load_fallback_json(doc, "intro")
            for key, col in INTRO_SECTION_COLUMNS.items():
                val = self._format_val(doc.get(col)) or self._format_val(disk_json.get(key))
                self.section_text.insert(tk.END, f"{key}\n", "h2")
                self.section_text.insert(tk.END, f"{val or '— Not available —'}\n", "body" if val else "muted")

            raw_intro = self._format_val(doc.get("introduction_text"))
            if raw_intro:
                self.section_text.insert(tk.END, "Identified Introduction Text\n", "h2")
                self.section_text.insert(tk.END, f"{raw_intro}\n", "body")

        elif mode == "rescon":
            self.section_text.insert(tk.END, "Results & Conclusion Analysis\n", "h1")
            disk_json = self._load_fallback_json(doc, "rescon")
            for key, col in RESCON_SECTION_COLUMNS.items():
                val = self._format_val(doc.get(col)) or self._format_val(disk_json.get(key))
                self.section_text.insert(tk.END, f"{key}\n", "h2")
                self.section_text.insert(tk.END, f"{val or '— Not available —'}\n", "body" if val else "muted")

            raw_rc = self._format_val(doc.get("rescon_text"))
            if raw_rc:
                self.section_text.insert(tk.END, "Identified Results & Conclusion Text\n", "h2")
                self.section_text.insert(tk.END, f"{raw_rc}\n", "body")

        elif mode == "doi":
            self.section_text.insert(tk.END, "DOI & Bibliographic Metadata\n", "h1")
            meta_fields = [
                ("UUID", "uuid"),
                ("Title", "title"),
                ("Filename", "filename"),
                ("Publication Year", "publication_year"),
                ("First Author", "first_author"),
                ("All Authors", "authors"),
                ("Publisher", "publisher"),
                ("Journal / Container", "container"),
                ("DOI Link", "doi_link"),
                ("Alternate Link", "link"),
                ("Title Classification", "classification"),
                ("Abstract Classification", "abstract_classification"),
                ("Evaluation Score (Abstract)", "evaluation_score"),
                ("Storage Space", "source_folder"),
                ("PDF Path", "relative_path"),
            ]
            for label, col in meta_fields:
                val = self._format_val(doc.get(col))
                self.section_text.insert(tk.END, f"{label}\n", "h2")
                self.section_text.insert(tk.END, f"{val or '—'}\n", "body" if val else "muted")

        self.section_text.configure(state="disabled")

    # ------------------------------------------------------------------ Column 3 Logic
    def _resolve_media_folder(self, doc, mode):
        """Locate the document's per-ID Tables or Images folder via DataAnalyzeManager."""
        source = doc.get("source_folder")
        uuid = doc.get("uuid")
        if not source or not uuid or not os.path.isdir(source):
            return None
        mf = DataAnalyzeManager(source)
        mf.update_id_files(uuid)
        target = mf.tables_storage_path if mode == "tables" else mf.image_storage_path
        return Path(target) if target and Path(target).is_dir() else None

    def _refresh_media_list(self):
        self.media_listbox.delete(0, tk.END)
        self._media_files = []
        self._clear_preview()

        if not self.current_doc:
            self.media_status_lbl.config(text="No document selected.")
            return

        mode = self.current_media_mode.get()
        folder = self._resolve_media_folder(self.current_doc, mode)

        if not folder:
            self.media_status_lbl.config(text=f"No {mode} folder found on disk for this document.")
            return

        if mode == "tables":
            exts = {".csv", ".xlsx", ".xls", ".json", ".txt", ".md", ".html"}
        else:
            exts = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff"}

        files = sorted(
            f for f in folder.rglob("*")
            if f.is_file() and (f.suffix.lower() in exts or not exts)
        )
        # Fallback: if no extension matched in tables_storage_path, list all files inside it
        if not files:
            files = sorted(f for f in folder.rglob("*") if f.is_file())

        self._media_files = files
        for f in files:
            self.media_listbox.insert(tk.END, f.name)

        self.media_status_lbl.config(text=f"Found {len(files)} {mode[:-1]}(s) in {folder.name}")
        if files:
            self.media_listbox.selection_set(0)
            self._on_media_select()

    def _clear_preview(self):
        for w in self.preview_container.winfo_children():
            w.destroy()
        self._tk_img_ref = None

    def _on_media_select(self, _event=None):
        sel = self.media_listbox.curselection()
        if not sel or sel[0] >= len(self._media_files):
            return
        file_path = self._media_files[sel[0]]
        mode = self.current_media_mode.get()

        self._clear_preview()
        self.preview_container.config(text=f"Preview — {file_path.name}")

        if mode == "tables":
            self._render_table_preview(file_path)
        else:
            self._render_image_preview(file_path)

    def _render_table_preview(self, file_path: Path):
        ext = file_path.suffix.lower()

        # CSV / Excel -> Render inside a scrollable Treeview grid
        if ext in (".csv", ".xlsx", ".xls"):
            try:
                df = pd.read_csv(file_path) if ext == ".csv" else pd.read_excel(file_path)
                cols = [str(c) for c in df.columns]
                tree = ttk.Treeview(self.preview_container, columns=cols, show="headings")
                for c in cols:
                    tree.heading(c, text=c)
                    tree.column(c, width=110, anchor="w")
                for _, row in df.head(500).iterrows():
                    tree.insert("", "end", values=[row.get(c, "") for c in df.columns])

                vsb = ttk.Scrollbar(self.preview_container, orient="vertical", command=tree.yview)
                hsb = ttk.Scrollbar(self.preview_container, orient="horizontal", command=tree.xview)
                tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
                vsb.pack(side="right", fill="y")
                hsb.pack(side="bottom", fill="x")
                tree.pack(side="left", fill="both", expand=True)
                return
            except Exception:
                pass  # Fallback to plain-text/JSON rendering below

        # JSON / Markdown / Text fallback
        txt = tk.Text(self.preview_container, wrap="none", padx=6, pady=6, font=("Courier New", 9))
        vsb = ttk.Scrollbar(self.preview_container, orient="vertical", command=txt.yview)
        hsb = ttk.Scrollbar(self.preview_container, orient="horizontal", command=txt.xview)
        txt.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        txt.pack(side="left", fill="both", expand=True)

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            if ext == ".json":
                try:
                    parsed = json.loads(content)
                    content = json.dumps(parsed, indent=2, ensure_ascii=False)
                except Exception:
                    pass
            txt.insert("1.0", content)
        except Exception as e:
            txt.insert("1.0", f"Could not read table file:\n{e}")
        txt.configure(state="disabled")

    def _render_image_preview(self, file_path: Path):
        canvas = tk.Canvas(self.preview_container, background="#222", highlightthickness=0)
        canvas.pack(fill="both", expand=True)

        def _draw(_event=None):
            cw = max(canvas.winfo_width(), 200)
            ch = max(canvas.winfo_height(), 200)
            canvas.delete("all")
            try:
                from PIL import Image, ImageTk
                img = Image.open(file_path)
                img.thumbnail((cw - 20, ch - 20), Image.Resampling.LANCZOS)
                self._tk_img_ref = ImageTk.PhotoImage(img)
                canvas.create_image(cw // 2, ch // 2, image=self._tk_img_ref, anchor="center")
            except ImportError:
                # Native Tkinter fallback if Pillow is not installed (supports PNG/GIF)
                try:
                    self._tk_img_ref = tk.PhotoImage(file=str(file_path))
                    canvas.create_image(cw // 2, ch // 2, image=self._tk_img_ref, anchor="center")
                except Exception as e:
                    canvas.create_text(
                        cw // 2, ch // 2,
                        text=f"Cannot preview {file_path.name} (install Pillow for full image support):\n{e}",
                        fill="white", width=cw - 40
                    )
            except Exception as e:
                canvas.create_text(cw // 2, ch // 2, text=f"Error loading image:\n{e}", fill="white", width=cw - 40)

        canvas.bind("<Configure>", _draw)

    def _open_selected_media_external(self):
        sel = self.media_listbox.curselection()
        if not sel or sel[0] >= len(self._media_files):
            messagebox.showinfo("No selection", "Select a table or image from the list first.", parent=self.win)
            return
        open_path(self._media_files[sel[0]])


def open_query_result_viewer(parent, docs_or_excel_path, title="Query Results Explorer"):
    """Helper to launch the 3-column Query Result Viewer window."""
    return QueryResultViewer(parent, docs_or_excel_path, title=title)
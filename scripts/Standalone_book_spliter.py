import os
import fitz  # PyMuPDF
import pandas as pd
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import sys
from pathlib import Path

# Add the parent project root directory to sys.path
project_root = Path(__file__).resolve().parents[1]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Now import your module functions
from archive.data_extraction_trials.Pdf_split_from_book2 import (
    _clean_text,
    get_metadata_from_llm,
    save_to_excel,
    split_pdf_by_excel,
)
from alr.common.LLM_Config import BLABLADOR_BASE_URL, check_api_key
from alr.common.excel_utils import*
import os
import fitz  # PyMuPDF
import pandas as pd
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# --- Include your existing utility functions & imports here ---
# (blabla_ask_llm_test, get_metadata_from_llm, _clean_text, 
#  repair_and_load_json, save_to_excel, etc.)


class CheckboxTreeview(ttk.Treeview):
    """Custom Treeview widget supporting checkable rows and header select-all."""
    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        
        # Checkbox Unicode symbols
        self.checked_char = "☑"
        self.unchecked_char = "☐"
        
        self.bind("<Button-1>", self._on_click)

    def populate(self, entries):
        """Populate treeview with checkboxes default set to Checked (True)."""
        self.delete(*self.get_children())
        for entry in entries:
            cat = entry.get("category", "")
            name = entry.get("publication_name", entry.get("name", ""))
            auth = entry.get("authors", "")
            page = entry.get("page_num", entry.get("page_number", ""))
            
            # Values: [Selected_State, Category, Name, Authors, Page]
            self.insert("", "end", values=(self.checked_char, cat, name, auth, page))

    def _on_click(self, event):
        region = self.identify_region(event.x, event.y)
        
        # 1. Clicked on Header -> Select / Deselect All
        if region == "heading":
            col = self.identify_column(event.x)
            if col == "#1":  # First column (#select)
                children = self.get_children()
                if not children:
                    return
                # Determine target state based on first item
                first_val = self.item(children[0], "values")[0]
                target_char = self.unchecked_char if first_val == self.checked_char else self.checked_char
                
                for item in children:
                    vals = list(self.item(item, "values"))
                    vals[0] = target_char
                    self.item(item, values=vals)

        # 2. Clicked on Cell -> Toggle individual row checkbox
        elif region == "cell":
            col = self.identify_column(event.x)
            if col == "#1":  # Checkbox column clicked
                item = self.identify_row(event.y)
                if item:
                    vals = list(self.item(item, "values"))
                    vals[0] = self.unchecked_char if vals[0] == self.checked_char else self.checked_char
                    self.item(item, values=vals)

    def get_selected_entries(self):
        """Returns a list of dict entries that are currently checked."""
        selected_data = []
        for item in self.get_children():
            vals = self.item(item, "values")
            if vals[0] == self.checked_char:
                selected_data.append({
                    "category": vals[1],
                    "name": vals[2],
                    "authors": vals[3],
                    "page_number": vals[4]
                })
        return selected_data


class TOCExtractorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("PDF TOC Extractor & Folder-Based Splitter Tool")
        self.root.geometry("950x700")

        self.pdf_path = ""
        self.pdf_stem_name = ""  # Stores PDF name without extension
        self.extracted_entries = []

        self._build_ui()

    def _build_ui(self):
        # 1. File Selection Frame
        file_frame = ttk.LabelFrame(self.root, text=" 1. Select PDF Book ", padding=10)
        file_frame.pack(fill="x", padx=10, pady=5)

        self.btn_select_file = ttk.Button(file_frame, text="Browse PDF", command=self.select_pdf)
        self.btn_select_file.pack(side="left", padx=5)

        self.lbl_file_path = ttk.Label(file_frame, text="No PDF selected", relief="sunken", anchor="w")
        self.lbl_file_path.pack(side="left", fill="x", expand=True, padx=5)

        # 2. Process Control Frame
        process_frame = ttk.LabelFrame(self.root, text=" 2. Extract Table of Contents ", padding=10)
        process_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(process_frame, text="TOC Start Page:").grid(row=0, column=0, sticky="w", padx=5)
        self.ent_start_page = ttk.Entry(process_frame, width=8)
        self.ent_start_page.insert(0, "10")
        self.ent_start_page.grid(row=0, column=1, padx=5)

        ttk.Label(process_frame, text="TOC End Page:").grid(row=0, column=2, sticky="w", padx=5)
        self.ent_end_page = ttk.Entry(process_frame, width=8)
        self.ent_end_page.insert(0, "15")
        self.ent_end_page.grid(row=0, column=3, padx=5)

        self.btn_extract = ttk.Button(process_frame, text="Extract TOC Data", command=self.process_toc)
        self.btn_extract.grid(row=0, column=4, padx=15)

        # 3. Data Display / Review Frame
        review_frame = ttk.LabelFrame(self.root, text=" 3. Select Items to Include & Review ", padding=10)
        review_frame.pack(fill="both", expand=True, padx=10, pady=5)

        columns = ("select", "category", "name", "authors", "page_number")
        self.tree = CheckboxTreeview(review_frame, columns=columns, show="headings")
        
        self.tree.heading("select", text="☑ / ☐")
        self.tree.heading("category", text="Category / Workshop")
        self.tree.heading("name", text="Publication Name")
        self.tree.heading("authors", text="Authors")
        self.tree.heading("page_number", text="Start Page")

        self.tree.column("select", width=50, anchor="center")
        self.tree.column("category", width=220)
        self.tree.column("name", width=300)
        self.tree.column("authors", width=200)
        self.tree.column("page_number", width=80, anchor="center")

        scrollbar = ttk.Scrollbar(review_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 4. Export & Splitting Actions Frame
        action_frame = ttk.Frame(self.root, padding=10)
        action_frame.pack(fill="x", padx=10, pady=5)

        self.btn_save_excel = ttk.Button(action_frame, text="Export Excel & Split PDFs", command=self.save_and_split, state="disabled")
        self.btn_save_excel.pack(side="right", padx=5)

    def select_pdf(self):
        file_path = filedialog.askopenfilename(filetypes=[("PDF Files", "*.pdf")])
        if file_path:
            self.pdf_path = file_path
            # Store the PDF filename without extension
            self.pdf_stem_name = os.path.splitext(os.path.basename(file_path))[0]
            self.lbl_file_path.config(text=file_path)

    def process_toc(self):
        if not self.pdf_path:
            messagebox.showwarning("Warning", "Please select a PDF file first.")
            return

        doc = fitz.open(self.pdf_path)
        toc = doc.get_toc()
        doc.close()

        # Step A: Check PyMuPDF Native TOC
        if toc:
            messagebox.showinfo("TOC Found", f"Found {len(toc)} bookmark entries via PyMuPDF native TOC.")
            self.extracted_entries = []
            current_category = "General"

            for item in toc:
                level, title, page = item[0], item[1], item[2]
                if level == 1:
                    current_category = title
                else:
                    self.extracted_entries.append({
                        "category": current_category,
                        "name": title,
                        "authors": "",
                        "page_number": str(page)
                    })
        # Step B: Fallback to Page Range + LLM extraction
        else:
            messagebox.showinfo("Fallback", "No embedded TOC found. Processing via fallback LLM extraction...")
            try:
                start_p = int(self.ent_start_page.get()) - 1
                end_p = int(self.ent_end_page.get())
            except ValueError:
                messagebox.showerror("Error", "Invalid page range values.")
                return

            self.extracted_entries = self._run_llm_fallback(start_p, end_p)

        self.tree.populate(self.extracted_entries)
        if self.extracted_entries:
            self.btn_save_excel.config(state="normal")
        else:
            messagebox.showwarning("No Data", "No entries were detected or extracted.")

    def _run_llm_fallback(self, start_page, end_page):
        entries = []
        current_category = "Unknown"
        doc = fitz.open(self.pdf_path)
        actual_end = min(end_page, len(doc))

        for i in range(start_page, actual_end):
            page = doc[i]
            raw_text = page.get_text("text")
            clean_page_text = _clean_text(raw_text)

            if clean_page_text:
                page_data = get_metadata_from_llm(clean_page_text, current_category)
                if page_data:
                    entries.extend(page_data)
                    current_category = page_data[-1].get("category", current_category)

        doc.close()
        return entries

    def save_and_split(self):
        selected_entries = self.tree.get_selected_entries()
        if not selected_entries:
            messagebox.showwarning("Warning", "No entries were selected with checkboxes.")
            return

        # 1. Ask user for a base folder location
        base_dir = filedialog.askdirectory(title="Select Destination Folder")
        if not base_dir:
            return

        # 2. Create target folder named after the PDF file
        target_folder = os.path.join(base_dir, self.pdf_stem_name)
        os.makedirs(target_folder, exist_ok=True)

        # 3. Save Excel inside the target folder
        excel_save_path = os.path.join(target_folder, f"{self.pdf_stem_name}.xlsx")
        save_to_excel(selected_entries, excel_save_path)

        # 4. Ask user if they want to proceed with PDF splitting
        split_confirm = messagebox.askyesno(
            "Split PDF", 
            f"Excel file saved!\n\nDo you also want to split and save the categorized PDFs in:\n{target_folder}?"
        )
        
        if split_confirm:
            try:
                self.split_pdf_by_category(selected_entries, self.pdf_path, target_folder)
                messagebox.showinfo("Complete", f"Successfully saved Excel and split PDFs to:\n{target_folder}")
            except Exception as e:
                messagebox.showerror("Splitting Error", f"Failed to split PDF: {e}")
        else:
            messagebox.showinfo("Complete", f"Excel saved to:\n{excel_save_path}")

    def split_pdf_by_category(self, entries: list, pdf_path: str, output_root: str):
        """Groups all selected papers by Category into a single merged PDF per Category."""
        import re

        def sanitize(name: str) -> str:
            return re.sub(r'[<>:"/\\|?*\n\r]', '', str(name)).strip()

        os.makedirs(output_root, exist_ok=True)
        doc = fitz.open(pdf_path)
        total_pages = len(doc)

        # 1. Group entries by category
        category_groups = {}
        for idx, entry in enumerate(entries):
            cat = sanitize(entry.get("category", "Uncategorized"))
            
            # Determine page range boundaries
            start_page = int(entry["page_number"])
            if idx < len(entries) - 1:
                end_page = int(entries[idx + 1]["page_number"]) - 1
            else:
                end_page = total_pages

            if cat not in category_groups:
                category_groups[cat] = []
            
            category_groups[cat].append((start_page, end_page))

        # 2. Build one consolidated PDF per category
        for category_name, page_ranges in category_groups.items():
            category_pdf = fitz.open()

            for start_page, end_page in page_ranges:
                for page_num in range(start_page - 1, end_page):
                    if page_num < total_pages:
                        category_pdf.insert_pdf(doc, from_page=page_num, to_page=page_num)

            out_filename = f"{category_name}.pdf"
            output_filepath = os.path.join(output_root, out_filename)
            category_pdf.save(output_filepath)
            category_pdf.close()

        doc.close()


if __name__ == "__main__":
    root = tk.Tk()
    app = TOCExtractorGUI(root)
    root.mainloop()
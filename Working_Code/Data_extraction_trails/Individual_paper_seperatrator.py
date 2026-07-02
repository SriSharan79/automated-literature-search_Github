import fitz  # PyMuPDF
import PyPDF2
import pypdf
import os
from pathlib import Path

def print_pdf_toc(pdf_path):
    try:
        # Open the PDF document
        doc = fitz.open(pdf_path)
        
        # Get the Table of Contents (returns a list of lists)
        # Format: [hierarchy_level, title, page_number]
        toc = doc.get_toc()
        
        if not toc:
            print("No embedded Table of Contents found in this PDF.")
            return

        print(f"Table of Contents for: {pdf_path}\n" + "-"*40)
        
        for entry in toc:
            level, title, page = entry
            # Indent based on the hierarchy level
            indent = "  " * (level - 1)
            print(f"{indent}Level {level}: {title} (Page {page})")
            
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        doc.close()
        
        
def print_toc_pypdf(pdf_path):
    try:
        reader = pypdf.PdfReader(pdf_path)
        # pypdf can take the string path directly, no need for 'with open'
        outline = reader.outline
        
        if not outline:
            print("No bookmarks found.")
            return

        def process_outline(entries, level=0):
            for entry in entries:
                if isinstance(entry, list):
                    process_outline(entry, level + 1)
                else:
                    try:
                        page_num = reader.get_destination_page_number(entry) + 1
                        print(f"{'  ' * level}• {entry.title} (Page {page_num})")
                    except Exception:
                        print(f"{'  ' * level}• {entry.title} (Page Link Broken)")

        process_outline(outline)
    except Exception as e:
        print(f"Error: {e}")
        

def extract_pdf_text_check(pdf_path: str, page_range: str) -> str:
    try:
        import fitz
    except ImportError:
        raise ImportError("Install with: pip install pymupdf")

    start, end = map(int, page_range.split(":"))

    # Open from bytes — bypasses the hanging _loadOutline() call
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    doc = fitz.open("pdf", pdf_bytes)

    total_pages = len(doc)
    start_idx = start - 1
    end_idx = min(end, total_pages)

    output = []
    for i in range(start_idx, end_idx):
        page = doc[i]
        output.append(f"{'='*60}\n--- Page {i + 1} ---\n{'='*60}")
        blocks = page.get_text("dict")["blocks"]

        for block in blocks:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    font_name  = span["font"]
                    font_size  = round(span["size"], 2)
                    flags      = span["flags"]
                    text       = span["text"].strip()

                    is_bold        = bool(flags & 2**4)
                    is_italic      = bool(flags & 2**1)
                    is_monospace   = bool(flags & 2**3)
                    is_superscript = bool(flags & 2**0)

                    styles = []
                    if is_bold:        styles.append("Bold")
                    if is_italic:      styles.append("Italic")
                    if is_monospace:   styles.append("Monospace")
                    if is_superscript: styles.append("Superscript")
                    style_str = ", ".join(styles) if styles else "Regular"

                    if text:
                        output.append(
                            f"[Font: {font_name} | Size: {font_size} | Style: {style_str}]\n{text}\n"
                        )

    doc.close()
    return "\n".join(output)



def extract_pdf_text(pdf_path: str, page_range: str) -> list[dict]:
    try:
        import fitz
    except ImportError:
        raise ImportError("Install with: pip install pymupdf")

    start, end = map(int, page_range.split(":"))

    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    doc = fitz.open("pdf", pdf_bytes)

    total_pages = len(doc)
    start_idx = start - 1
    end_idx = min(end, total_pages)

    spans = []
    for i in range(start_idx, end_idx):
        page = doc[i]
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = span["text"].strip()
                    if not text:
                        continue
                    flags = span["flags"]
                    font  = span["font"]
                    size  = round(span["size"], 2)

                    is_bold   = bool(flags & 2**4)
                    is_italic = bool(flags & 2**1)
                    style     = "Bold" if is_bold else ("Italic" if is_italic else "Regular")

                    spans.append({"font": font, "size": size, "style": style, "text": text})

    doc.close()
    return spans


def classify_span(span: dict) -> str:
    """Classify each span based on font, size, and style."""
    font  = span["font"]
    size  = span["size"]
    style = span["style"]
    text  = span["text"]

    # Title of the page: largest bold text
    if style == "Bold" and size > 12:
        return "title"

    # Category: bold, smaller size
    if style == "Bold" and size <= 12:
        return "category"

    # Page indicator: dotted lines
    if set(text.replace(" ", "")).issubset({".", "·", "•"}) or text.count(".") > 5:
        return "page_indicator"

    # Page number: short numeric text
    if text.strip().isdigit():
        return "page_number"

    # Authors: italic
    if style == "Italic":
        return "author"

    # Regular text = paper name
    return "name"

def build_table(spans: list[dict]) -> list[dict]:
    rows = []
    current = {"category": "", "name": "", "authors": "", "page_number": ""}
    current_category = ""
    last_label = None

    for span in spans:
        label = classify_span(span)
        text  = span["text"]

        if label == "title":
            pass

        elif label == "category":
            current_category = text
            # flush current if it has a name
            if current["name"]:
                rows.append(current)
                current = {"category": current_category, "name": "", "authors": "", "page_number": ""}

        elif label == "name":
            # New paper starts if we're coming from page_number or author
            if last_label in ("page_number", "author") and current["name"]:
                rows.append(current)
                current = {"category": current_category, "name": "", "authors": "", "page_number": ""}
            current["category"] = current_category
            current["name"] = (current["name"] + " " + text).strip()

        elif label == "page_indicator":
            pass

        elif label == "page_number":
            current["page_number"] = text

        elif label == "author":
            cleaned = text.strip().rstrip(",")
            current["authors"] = (current["authors"] + ", " + cleaned).strip(", ") if current["authors"] else cleaned

        last_label = label  # track previous label

    # flush last entry
    if current["name"]:
        rows.append(current)

    return rows


def print_table(rows: list[dict]):
    try:
        import pandas as pd
        df = pd.DataFrame(rows, columns=["category", "name", "authors", "page_number"])
        df.columns = ["Category", "Paper Name", "Authors", "Page"]
        print(df.to_string(index=False))
        return df
    except ImportError:
        for r in rows:
            print(r)
def save_table_to_excel(rows: list[dict], output_path: str):
    """Save the parsed table to an Excel file."""
    try:
        import pandas as pd
    except ImportError:
        raise ImportError("Install with: pip install pandas openpyxl")

    df = pd.DataFrame(rows, columns=["category", "name", "authors", "page_number"])
    df.columns = ["Category", "Paper Name", "Authors", "Page"]
    
    df.to_excel(output_path, index=False, sheet_name="Papers")
    print(f"✓ Table saved to: {output_path}")


import os
import re
import fitz
import pandas as pd

# def split_pdf_by_excel(excel_path: str, pdf_path: str, output_root: str):

#     def sanitize(name: str) -> str:
#         return re.sub(r'[<>:"/\\|?*\n\r]', '', name).strip()

#     def safe_makedirs(path: str):
#         """Use os.makedirs which avoids pathlib stat() hanging on network drives."""
#         try:
#             os.makedirs(path, exist_ok=True)
#         except FileExistsError:
#             # A file (not folder) exists at this path — remove it
#             os.remove(path)
#             os.makedirs(path, exist_ok=True)

#     # ── Create output root ────────────────────────────────────────────────────
#     safe_makedirs(output_root)

#     # ── Load Excel ────────────────────────────────────────────────────────────
#     df = pd.read_excel(excel_path)
#     print(f"✓ Loaded {len(df)} papers from {excel_path}")

#     # ── Open PDF ──────────────────────────────────────────────────────────────
#     with open(pdf_path, "rb") as f:
#         pdf_bytes = f.read()
#     doc = fitz.open("pdf", pdf_bytes)
#     total_pages = len(doc)
#     print(f"✓ Original PDF has {total_pages} pages")

#     # ── Split papers ──────────────────────────────────────────────────────────
#     for idx, row in df.iterrows():
#         category   = sanitize(str(row["Category"]))
#         paper_name = sanitize(str(row["Paper Name"]))
#         start_page = int(row["Page"])
#         end_page   = int(df.iloc[idx + 1]["Page"]) - 1 if idx < len(df) - 1 else total_pages

#         # Create category subfolder
#         category_folder = os.path.join(output_root, category)
#         safe_makedirs(category_folder)

#         # Extract pages (1-indexed → 0-indexed)
#         new_doc = fitz.open()
#         for page_num in range(start_page - 1, end_page):
#             if page_num < total_pages:
#                 new_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)

#         output_pdf = os.path.join(category_folder, f"{paper_name}.pdf")
#         new_doc.save(output_pdf)
#         new_doc.close()

#         print(f"  ✓ [{idx+1}/{len(df)}] {category}/{paper_name}.pdf  (pages {start_page}–{end_page})")

#     doc.close()
#     print(f"\n✓ Done — all papers saved to: {output_root}")

import os
import re
import shutil
import tempfile
import fitz
import pandas as pd
def split_pdf_by_excel(excel_path: str, pdf_path: str, output_root: str):

    def sanitize(name: str) -> str:
        return re.sub(r'[<>:"/\\|?*\n\r]', '', name).strip()

    def safe_makedirs(path: str):
        try:
            os.makedirs(path, exist_ok=True)
        except FileExistsError:
            os.remove(path)
            os.makedirs(path, exist_ok=True)

    # ── Copy source PDF locally ───────────────────────────────────────────────
    local_tmp = tempfile.mkdtemp()
    local_pdf = os.path.join(local_tmp, "source.pdf")
    print(f"⏳ Copying PDF to local temp ...")
    shutil.copy2(pdf_path, local_pdf)
    print(f"✓ Copy done — {local_pdf}")

    # ── Load Excel ────────────────────────────────────────────────────────────
    df = pd.read_excel(excel_path)
    print(f"✓ Loaded {len(df)} papers")

    # ── Open PDF locally ──────────────────────────────────────────────────────
    doc = fitz.open(local_pdf)
    total_pages = len(doc)
    print(f"✓ PDF opened — {total_pages} pages\n")

    # ── Create output root on network ─────────────────────────────────────────
    safe_makedirs(output_root)

    # ── Split papers ──────────────────────────────────────────────────────────
    for idx, row in df.iterrows():
        category   = sanitize(str(row["Category"]))
        paper_name = sanitize(str(row["Paper Name"]))
        start_page = int(row["Page"])
        end_page   = int(df.iloc[idx + 1]["Page"]) - 1 if idx < len(df) - 1 else total_pages

        # Extract pages into new doc
        new_doc = fitz.open()
        for page_num in range(start_page - 1, end_page):
            if page_num < total_pages:
                new_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)

        # ── Save locally first, then copy to network ──────────────────────────
        local_out = os.path.join(local_tmp, f"{paper_name}.pdf")
        new_doc.save(local_out)          # fast — local disk
        new_doc.close()

        # Create network category folder and copy file over
        network_folder = os.path.join(output_root, category)
        safe_makedirs(network_folder)
        network_out = os.path.join(network_folder, f"{paper_name}.pdf")
        shutil.copy2(local_out, network_out)   # OS-level block copy to network
        os.remove(local_out)                   # clean up local temp immediately

        print(f"  ✓ [{idx+1}/{len(df)}] {category}/{paper_name}.pdf  (pages {start_page}–{end_page})")

    doc.close()
    shutil.rmtree(local_tmp)
    print(f"\n✓ Temp files cleaned up")
    print(f"✓ Done — all papers saved to: {output_root}")


# ── Usage ─────────────────────────────────────────────────────────────────────
excel_path  = r"C:\Users\kata_du\Documents\MBSA\output_table_2020.xlsx"
pdf_path    = r"C:\Users\kata_du\Documents\MBSA\Model-Based Safety and Assessment (IMBSA 2020); 2020.pdf"
output_root = r"C:\Users\kata_du\Documents\MBSA\Papers_split"


# spans = extract_pdf_text(pdf_path, "9:11")
# rows  = build_table(spans)
# save_table_to_excel(rows, excel_path)


# text = extract_pdf_text_check(pdf_path, "9:11")
# print(text)

split_pdf_by_excel(excel_path, pdf_path, output_root)





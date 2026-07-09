"""
alr.data_analysis.doi_metadata
=============================

DOI / publication-metadata extraction (relocated from the archive into the
package). ``MetadataLogic`` reads the first pages of a PDF, finds a DOI or arXiv
id, and looks up bibliographic metadata (Crossref / arXiv). ``enrich_space_with_doi``
runs it over a DataAnalyzeManager storage space, writes the managed
``DOI_Metadata.xlsx`` and pushes the metadata into the SQLite store.
"""

import re
import pandas as pd
import requests
import json
import os
import time
import unicodedata
from collections import Counter
import xml.etree.ElementTree as ET
from alr.data_analysis.title_extracter import get_title_in_the_file

class MetadataLogic:
    def __init__(self):
        # Global-style variable within the class instance to track failed requests
        self.failed_arxiv_requests = []
        self.type_keywords = {
            "Journal Article": [
                "journal", "volume", "issue", "doi", "issn", "elsevier", "springer", 
                "wiley", "ieee transactions", "published in", "received", "accepted",
                "correspondence"
            ],
            "Preprint": [
                "arxiv", "biorxiv", "medrxiv", "preprint", "submitted to", "under review", 
                "draft", "working paper", "chemrxiv", "ssrn"
            ],
            "Conference Paper": [
                "conference", "proceedings", "symposium", "workshop", "presented at", 
                "association for computational linguistics", "icml", "neurips", 
                "cvpr", "eccv", "congress"
            ],
            "Report": [
                "technical report", "white paper", "deliverable", "final report", 
                "annual report", "commission", "briefing", "policy brief"
            ],
            "Book": [
                "isbn", "handbook", "monograph", "edited by", "preface"
            ],
            "Statute": [
                "regulation", "directive", "official journal", "parliament", 
                "council", "decree", "legislative act"
            ],
            "Standard": [
                "iso", "din", "en", "iec", "cen", "cenelec", "technical specification"
            ],
            "Presentation": [
                "presentation", "slides", "powerpoint", "agenda", "keynote", "webinar"
            ],
            "Thesis": [
                "thesis", "dissertation", "master", "phd", "degree", 
                "submitted in partial fulfillment", "faculty of", "advisor"
            ]
        }

    # def extract_pdf_info(self, file_path):
    #     """
    #     Liest Text aus der ersten Seite, prüft Format UND sucht nach Kommentaren.
    #     Rückgabe: text, is_landscape, num_pages, has_comments
    #     """
    #     text = ""
    #     is_landscape = False
    #     num_pages = 0
    #     has_comments = False # Default
        
    #     try:
    #         doc = fitz.open(file_path)
    #         num_pages = len(doc)
            
    #         if num_pages > 0:
    #             page = doc[0]
    #             text = page.get_text("text")
                
    #             rect = page.rect
    #             if rect.width > rect.height:
    #                 is_landscape = True

    #             # --- KOMMENTAR CHECK (NEU für AI Logic Synchronisation) ---
    #             # Relevante Typen: 0=Text, 3=FreeText, 8=Highlight, 9=Underline, 10=Squiggly, 11=StrikeOut
    #             for annot in page.annots():
    #                 if annot.type[0] in [0, 3, 8, 9, 10, 11]:
    #                     has_comments = True
    #                     break 
            
    #         doc.close()
    #     except Exception as e:
    #         print(f"Error reading PDF {file_path}: {e}")
    #         return "", False, 0, False
            
    #     return self._clean_text(text), is_landscape, num_pages, has_comments

    def extract_pdf_info(self, file_path):
        """
        Rückgabe: text (1st 3 pages), is_landscape (any page), 
        num_pages, has_comments (any page)
        """
        import fitz  # PyMuPDF (imported lazily)

        text_parts = []
        is_landscape = False
        num_pages = 0
        has_comments = False

        try:
            doc = fitz.open(file_path)
            num_pages = len(doc)
            
            for i, page in enumerate(doc):
                # 1. Text extraction (only for the first 3 pages: 0, 1, 2)
                if i < 3:
                    text_parts.append(page.get_text("text"))

                # 2. Layout Check: If ANY page is landscape, mark as True
                if not is_landscape:
                    rect = page.rect
                    if rect.width > rect.height:
                        is_landscape = True

                # 3. Comment Check: Search ALL pages until a comment is found
                if not has_comments:
                    for annot in page.annots():
                        # Types: 0=Text, 3=FreeText, 8=Highlight, 9=Underline, 10=Squiggly, 11=StrikeOut
                        if annot.type[0] in [0, 3, 8, 9, 10, 11]:
                            has_comments = True
                            break 
            
            doc.close()
        except Exception as e:
            print(f"Error reading PDF {file_path}: {e}")
            return "", False, 0, False
    
        full_text = "\n".join(text_parts)
        return self._clean_text(full_text), is_landscape, num_pages, has_comments

    def _clean_text(self, text):
        if not text: return ""
        text = unicodedata.normalize('NFKD', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    
    def fetch_metadata_by_doi(self, text):
        doi_pattern = r'\b(10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+)\b'
        matches = re.findall(doi_pattern, text)
        headers = {'User-Agent': 'DocumentsHub/1.0 (mailto:admin@localhost)'}

        for doi in matches:
            doi = doi.rstrip('.,;)]')
            print(f"   -> DOI gefunden: {doi}")
            try:
                url = f"https://api.crossref.org/works/{doi}"
                r = requests.get(url, headers=headers, timeout=5)
                
                if r.status_code == 200:
                    data = r.json()['message']
                    DOI_Authors = []
                    if 'author' in data:
                        for a in data['author']:
                            family = a.get('family', '')
                            given = a.get('given', '')
                            if family:
                                name = f"{family}, {given}".strip(', ')
                                DOI_Authors.append(name)
                    
                    c_type = data.get('type', '')
                    doc_type = "Journal Article"
                    
                    if 'book' in c_type: doc_type = "Book"
                    elif 'proceedings' in c_type: doc_type = "Conference Paper"
                    elif 'report' in c_type: doc_type = "Report"
                    elif 'dissertation' in c_type: doc_type = "Thesis"
                    elif 'standard' in c_type: doc_type = "Standard"
                    
                    title = data.get('title', [''])[0] if data.get('title') else ""
                    
                    date_parts = data.get('published-print', {}).get('date-parts')
                    if not date_parts: date_parts = data.get('created', {}).get('date-parts')
                    pub_date = str(date_parts[0][0]) if date_parts else ""
                    
                    publisher = data.get('publisher', '')
                    subtitle = data.get('subtitle', '')
                    container = data.get('container-title', [''])[0] if data.get('container-title') else ""
                    tags = data.get('subject', [])
                    
                    # EXTRACT URL
                    pub_url = data.get('URL', f"https://doi.org/{doi}")
                    
                    return {
                        "DOI_ID": doi,
                        "title": title, "DOI_Authors": DOI_Authors, "date": pub_date,
                        "doc_type": doc_type, "tags": tags,
                        "publisher": publisher ,
                        "container":container,
                        "subtitle":subtitle,
                        "abstract": "",
                        "url": pub_url # Added URL field
                    }
            except Exception as e:
                print(f"   -> DOI Error: {e}")
                continue
        return None


    def fetch_metadata_by_arxiv(self, text, filename="Unknown"):
        arxiv_pattern = r'arxiv[:\s/]*(\d{4}\.\d{4,5}(?:v\d+)?)'
        match = re.search(arxiv_pattern, text, re.IGNORECASE)
        
        if match:
            aid = match.group(1)
            print(f"   -> arXiv ID gefunden: {aid}")
            
            # Respect rate limits
            time.sleep(3)
            
            try:
                url = f"https://export.arxiv.org/api/query?id_list={aid}"
                headers = {'User-Agent': 'MetadataFetcher/1.0 (contact: your-email@example.com)'}
                
                r = requests.get(url, headers=headers, timeout=5)
                
                if r.status_code == 429:
                    print(f"   -> Rate limit hit for {aid}. Saving to retry list.")
                    self.failed_arxiv_requests.append((filename, aid))
                    return None

                r.raise_for_status()

                if r.status_code == 200:
                    # Define namespaces (atom is standard, arxiv is for comments/journal_ref)
                    ns = {
                        'atom': 'http://www.w3.org/2005/Atom',
                        'arxiv': 'http://arxiv.org/schemas/atom'
                    }
                    
                    root = ET.fromstring(r.content)
                    entry = root.find('atom:entry', ns)
                    
                    if entry:
                        title = entry.find('atom:title', ns).text.strip().replace('\n', ' ')
                        pub_raw = entry.find('atom:published', ns).text
                        pub_year = pub_raw[:4] if pub_raw else ""
                        summary = entry.find('atom:summary', ns).text.strip().replace('\n', ' ')
                        DOI_Authors = [a.find('atom:name', ns).text for a in entry.findall('atom:author', ns)]
                        
                        # EXTRACT COMMENT
                        # The comment field often contains "Accepted at CVPR" or "Published in..."
                        comment_node = entry.find('arxiv:comment', ns)
                        comment_text = comment_node.text.strip() if comment_node is not None else "arXiv"
                        
                        tags = ["arXiv", "Preprint"]
                        for cat in entry.findall('atom:category', ns):
                            term = cat.get('term')
                            if term: tags.append(term)
                        
                        return {
                            "DOI_ID": aid,
                            "title": title, 
                            "DOI_Authors": DOI_Authors, 
                            "date": pub_year,
                            "doc_type": "Preprint", 
                            "tags": tags, 
                            "abstract": summary,
                            "publisher": "arXiv",
                            # UPDATED: Use comment if available, else fallback to "arXiv"
                            "container": comment_text,
                            "subtitle": comment_text,
                            "url": f"https://arxiv.org/abs/{aid}"
                        }
            except Exception as e:
                print(f"   -> arXiv Error: {e}")
                
        return None

    def fetch_arxiv_batch(self, failed_list):
        """
        Takes a list of (filename, arxiv_id) and fetches them in one go.
        Returns a dictionary mapping filename -> metadata_dict
        """
        if not failed_list:
            return {}

        # Extract unique IDs for the URL
        ids = list(set(item[1] for item in failed_list))
        id_string = ",".join(ids)
        results_map = {}

        try:
            url = f"https://export.arxiv.org/api/query?id_list={id_string}"
            headers = {'User-Agent': 'MetadataFetcher/1.0 (contact: your-email@example.com)'}
            
            r = requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()

            # Define namespaces for standard Atom and arXiv-specific tags
            ns = {
                'atom': 'http://www.w3.org/2005/Atom',
                'arxiv': 'http://arxiv.org/schemas/atom'
            }
            
            root = ET.fromstring(r.content)
            entries = root.findall('atom:entry', ns)

            for entry in entries:
                # 1. Identify the ID to match it back to the original file
                full_id_url = entry.find('atom:id', ns).text
                found_id = full_id_url.split('/')[-1] # Extracts ID from the URL
                
                # 2. Extract specific metadata
                title = entry.find('atom:title', ns).text.strip().replace('\n', ' ')
                pub_date = entry.find('atom:published', ns).text[:4]
                DOI_Authors = [a.find('atom:name', ns).text for a in entry.findall('atom:author', ns)]
                
                # 3. Extract Comment (for Container/Subtitle)
                comment_node = entry.find('arxiv:comment', ns)
                comment_text = comment_node.text.strip() if comment_node is not None else "arXiv"
                
                # 4. Map this data back to every filename that shared this ID
                # (In case multiple files have the same arXiv ID)
                for filename, aid in failed_list:
                    if aid in found_id:
                        results_map[filename] = {
                            "title": title,
                            "date": pub_date,
                            "DOI_Authors": DOI_Authors,
                            "comment": comment_text, # Passed back to process_directory_to_excel
                            "url": f"https://arxiv.org/abs/{found_id}"
                        }
                        
        except Exception as e:
            print(f"   -> Batch Recovery Error: {e}")

        return results_map

    def extract_meta_data_from_doi(self, file_path):   
        # 1. Extract PDF info
        try:
            text, is_landscape, num_pages, has_comments = self.extract_pdf_info(file_path)
        except Exception as e:
            print(f"   -> PDF Read Error: {e}")
            text = ""

        # 2. Attempt Metadata Retrieval
        doi_data = self.fetch_metadata_by_doi(text) if text else None
        arxiv_data = self.fetch_metadata_by_arxiv(text) if (text and not doi_data) else None
        raw_data = doi_data if doi_data else arxiv_data
        
        # 3. Data Extraction Helper
        def get_val(obj, key, default="N/A"):
            if not obj: return default
            return obj.get(key, default)

        # 4. Process DOI_Authors and First Author
        author_list = get_val(raw_data, 'DOI_Authors', [])
        DOI_Authors_str = "N/A"
        DOI_First_Author = "N/A"
        publisher=get_val(raw_data, 'publisher', "N/A")
        container=get_val(raw_data, 'container', "N/A")
        subtitle=get_val(raw_data, 'subtitle', "N/A")
        
        if isinstance(author_list, list) and len(author_list) > 0:
            DOI_Authors_str = ", ".join(author_list)
            DOI_First_Author = author_list[0]

        # 5. Build base_data (with Fallbacks for missing metadata)
        # If raw_data is None, get_val will return the default "N/A" or "Title Not Found"
        base_data = {
            "Publication Name": get_val(raw_data, 'title', "Metadata Not Found"),
            "DOI_Link": get_val(raw_data, 'url', "N/A"),
            "Publication Year": get_val(raw_data, 'date', "N/A"),
            "Publisher":publisher,
            "Container":container,
            "Subtitle":subtitle,
            "DOI_Authors": DOI_Authors_str,
            "DOI_First_Author": DOI_First_Author,
            "File_Name": os.path.basename(file_path),
            "File_Path": file_path
        }

        return base_data

    def _iter_pdf_files(self, input_path):
        """
        Yield PDF file paths for ``input_path``. Accepts either a single PDF
        file path or a folder, which is walked recursively. This lets callers
        target exactly a selected file/folder instead of always scanning an
        entire storage-space PDF subfolder.
        """
        if os.path.isfile(input_path):
            if input_path.lower().endswith(".pdf"):
                yield input_path
            return

        for dirpath, _, filenames in os.walk(input_path):
            for filename in filenames:
                if filename.lower().endswith(".pdf"):
                    yield os.path.join(dirpath, filename)

    def process_input_to_excel(self, input_path, output_excel, should_cancel=None, skip_filenames=None):
        """
        Process a single PDF file OR a folder of PDFs (recursively) and write
        the combined metadata to ``output_excel``. Generalized version of
        ``process_directory_to_excel`` that also accepts a single file path.

        ``skip_filenames`` is an optional set of basenames to skip entirely
        (files whose DOI metadata already exists and is carried forward by the
        caller), so only genuinely-new PDFs incur PDF reads / network lookups.
        """
        all_metadata = []
        skip_filenames = skip_filenames or set()

        # --- PHASE 1: Initial Processing ---
        for file_path in self._iter_pdf_files(input_path):
            if should_cancel is not None and should_cancel():
                print("DOI extraction cancelled by user.")
                self._save_to_excel(all_metadata, output_excel)
                return

            filename = os.path.basename(file_path)
            if filename in skip_filenames:
                print(f"--- Skipping (DOI already present): {filename} ---")
                continue
            print(f"--- Processing: {filename} ---")

            try:
                # Note: You should pass file_path to fetch_metadata_by_arxiv
                # inside extract_meta_data_from_doi to catch the filename for the 429 list
                base_data = self.extract_meta_data_from_doi(file_path)

                if base_data['Publication Name'] == "Metadata Not Found":
                    title = get_title_in_the_file(file_path, 'b')
                    base_data['Publication Name'] = title

                all_metadata.append(base_data)
                self._save_to_excel(all_metadata, output_excel)

            except Exception as e:
                print(f"Error processing {filename}: {e}")

        # --- PHASE 2: Batch Processing 429 Failures ---
        if self.failed_arxiv_requests:
            print(f"\n--- Retrying {len(self.failed_arxiv_requests)} failed arXiv requests in batch ---")
            # We wait a bit more just to be safe before the retry
            time.sleep(5) 
            
            batch_results = self.fetch_arxiv_batch(self.failed_arxiv_requests)
            
            # Update the all_metadata list with the new data
            for data in all_metadata:
                fname = data["File_Name"]
                if fname in batch_results:
                    new_data = batch_results[fname]
                    print(f"   -> Successfully recovered metadata for: {fname}")
                    
                    # Map retrieved batch data back to your Excel structure
                    data.update({
                        "Publication Name": new_data.get('title', data["Publication Name"]),
                        "DOI_Link": new_data.get('url', "N/A"),
                        "Publication Year": new_data.get('date', "N/A"),
                        "Publisher": "arXiv",
                        "Container": "arXiv",
                        "Subtitle": "arXiv",
                        "DOI_Authors": ", ".join(new_data.get('DOI_Authors', [])),
                        "DOI_First_Author": new_data.get('DOI_Authors', ["N/A"])[0] if new_data.get('DOI_Authors') else "N/A"
                    })

            # Final save after recovery
            self._save_to_excel(all_metadata, output_excel)

        print(f"\nProcessing complete! Data saved to {output_excel}")

    def process_directory_to_excel(self, root_folder, output_excel, should_cancel=None, skip_filenames=None):
        """
        Kept for backward compatibility: scans a folder recursively for PDFs.
        Delegates to ``process_input_to_excel``, which also accepts a single
        PDF file path.
        """
        return self.process_input_to_excel(root_folder, output_excel, should_cancel=should_cancel,
                                           skip_filenames=skip_filenames)

    def _save_to_excel(self, data_list, filename):
        """Helper to convert list of dicts to Excel with specific column order"""
        if not data_list:
            return
            
        df = pd.DataFrame(data_list)
        
        # Define the exact column order requested
        cols = [
            "Publication Name",
            "DOI_Link",
            "Publication Year",
            "Publisher","Container", "Subtitle",
            "DOI_Authors",
            "DOI_First_Author",
            "File_Name",
            "File_Path"
        ]
        
        # Ensure only requested columns are exported in the correct order
        df = df[cols]
        df.to_excel(filename, index=False)

# DOI metadata excel column -> documents column in the SQLite store.
DOI_EXCEL_TO_DB = {
    "DOI_Link": "doi_link",
    "Publisher": "publisher",
    "Container": "container",
    "Publication Year": "publication_year",
    "DOI_Authors": "authors",
    "DOI_First_Author": "first_author",
}

# Full ordered column set for the managed DOI_Metadata workbook.
DOI_EXCEL_COLUMNS = [
    "Publication Name", "DOI_Link", "Publication Year", "Publisher", "Container",
    "Subtitle", "DOI_Authors", "DOI_First_Author", "File_Name", "File_Path",
]


def _has_real_doi(value) -> bool:
    """True if a DOI/link value is present and not a 'not found' placeholder."""
    if value is None:
        return False
    text = str(value).strip()
    return text != "" and text.lower() not in ("nan", "n/a")


def _doi_row_from_sql(doc) -> dict:
    """Reconstruct a DOI_Metadata-shaped row from an existing SQL document row."""
    return {
        "Publication Name": doc.get("title") or "",
        "DOI_Link": doc.get("doi_link") or "",
        "Publication Year": doc.get("publication_year") or "",
        "Publisher": doc.get("publisher") or "",
        "Container": doc.get("container") or "",
        "Subtitle": "",
        "DOI_Authors": doc.get("authors") or "",
        "DOI_First_Author": doc.get("first_author") or "",
        "File_Name": doc.get("filename") or "",
        "File_Path": doc.get("relative_path") or "",
    }


def enrich_space_with_doi(manager, db_path=None, should_cancel=None, input_path=None) -> int:
    """
    Run DOI/metadata extraction, write the managed ``DOI_Metadata.xlsx``, and
    push the metadata into the SQLite store (matching by File_Name ->
    document filename). Returns the number of documents updated.

    ``manager`` is a DataAnalyzeManager (or a folder path); it supplies the
    managed ``DOI_Metadata.xlsx`` output location.

    ``input_path``, if given, is a specific PDF file OR folder to scan
    instead of the manager's own ``pdf_subfolder`` -- e.g. exactly the file
    or folder selected in the UI. This lets a run target only what was
    selected rather than always re-scanning the whole storage space. When
    omitted, behavior is unchanged: it scans ``manager.pdf_subfolder``.
    ``should_cancel`` is an optional callable checked between PDFs for
    cooperative cancellation.
    """
    from alr.common.file_manager import DataAnalyzeManager
    from alr.common.sql_store import AnalyzedDataStore, DB_PATH

    if not isinstance(manager, DataAnalyzeManager):
        manager = DataAnalyzeManager(manager)

    from alr.common.analysis_precheck import latest_dated_row

    scan_target = input_path if input_path else str(manager.pdf_subfolder)
    output_excel = manager.doi_metadata_excel

    # Snapshot the live workbook BEFORE extraction: process_input_to_excel /
    # _save_to_excel fully overwrite ``output_excel`` with only this run's
    # rows, so rows for files outside the current scan target would otherwise
    # be lost. These prior rows are merged back in below (dedup by File_Name,
    # freshly extracted rows take precedence).
    prior_rows = []
    if os.path.exists(output_excel):
        try:
            prior_rows = pd.read_excel(output_excel).to_dict("records")
        except Exception:
            prior_rows = []

    store = AnalyzedDataStore(db_path or DB_PATH)
    docs_by_filename = {d.get("filename"): d for d in store.list_documents() if d.get("filename")}

    # --- Precheck: skip PDFs whose DOI metadata already exists (SQL or a prior
    # dated DOI file). Carry those prior rows forward so the refreshed workbook
    # stays complete, and only genuinely-missing files incur extraction. ---
    scanned = {os.path.basename(p) for p in MetadataLogic()._iter_pdf_files(scan_target)}
    skip_filenames = set()
    carry_rows = []
    for fname in scanned:
        doc = docs_by_filename.get(fname)
        prev_path, prev_row = latest_dated_row(manager.doi_metadata_subfolder, "DOI_Metadata", "File_Name", fname)
        has_sql = bool(doc) and _has_real_doi(doc.get("doi_link"))
        has_file = bool(prev_row) and _has_real_doi(prev_row.get("DOI_Link"))
        if has_file:
            skip_filenames.add(fname)
            carry_rows.append({c: prev_row.get(c, "") for c in DOI_EXCEL_COLUMNS})
        elif has_sql:
            skip_filenames.add(fname)
            carry_rows.append(_doi_row_from_sql(doc))

    meta_logic = MetadataLogic()
    meta_logic.process_input_to_excel(scan_target, output_excel, should_cancel=should_cancel,
                                      skip_filenames=skip_filenames)

    # Merge carried-forward rows AND the pre-run workbook snapshot into
    # today's workbook (dedup by File_Name; new extraction rows win, then
    # carry rows, then prior workbook rows).
    if carry_rows or prior_rows:
        existing = []
        if os.path.exists(output_excel):
            try:
                existing = pd.read_excel(output_excel).to_dict("records")
            except Exception:
                existing = []
        existing_names = {str(r.get("File_Name")) for r in existing}
        merged = list(existing)
        for r in list(carry_rows) + list(prior_rows):
            name = str(r.get("File_Name"))
            if not name or name == "nan" or name in existing_names:
                continue
            merged.append({c: r.get(c, "") for c in DOI_EXCEL_COLUMNS})
            existing_names.add(name)
        if merged:
            pd.DataFrame(merged).reindex(columns=DOI_EXCEL_COLUMNS).to_excel(output_excel, index=False)

    if not os.path.exists(output_excel):
        return 0

    df = pd.read_excel(output_excel)
    # filename -> uuid, built once
    by_filename = {fn: d["uuid"] for fn, d in docs_by_filename.items()}

    updated = 0
    for _, row in df.iterrows():
        fname = row.get("File_Name")
        if not fname or str(fname) == "nan":
            continue
        uuid = by_filename.get(str(fname))
        if not uuid:
            continue
        fields = {}
        for xl, db_col in DOI_EXCEL_TO_DB.items():
            val = row.get(xl)
            if val is not None and str(val).strip() and str(val) != "nan":
                fields[db_col] = str(val)
        if fields:
            store.update_document(uuid, fields)
            updated += 1
    print(f"DOI enrichment updated {updated} document(s).")
    return updated


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2:
        enrich_space_with_doi(sys.argv[1], input_path=sys.argv[2] if len(sys.argv) >= 3 else None)
    else:
        print("Usage: python -m alr.data_analysis.doi_metadata <storage_space_folder> [pdf_file_or_folder]")
# from docling.datamodel.base_models import LayoutLabel

# Path to your PDF file
import sys

from colorama import Fore, Style
from alr.common.llm_utils import llm_call

from alr.data_analysis.Data_analysis_system_prompts import Sys_Prompt_Title_Analyzer
from PyPDF2 import PdfReader
from docling.document_converter import DocumentConverter
import pdfplumber
import fitz  # PyMuPDF
import re
import pandas as pd
import requests
import json
import os
import unicodedata
from collections import Counter
import xml.etree.ElementTree as ET
from alr.common.excel_utils import extract_column




# source = "/localdata/user/kata_du/Automated Literature Survey/MBSE_MBSA_Aviation/2006_R Szczepanik_CATASASSAR.pdf"


# def get_title_with_docling(file_path):
#     converter = DocumentConverter()
#     result = converter.convert(file_path)
    
#     # Priority 1: Semantic Labeling
#     for element in result.document.texts:
#         if "title" in str(element.label).lower():
#             return element.text
            
#     # Priority 2: Metadata origin
#     if result.document.origin and hasattr(result.document.origin, 'title'):
#         if result.document.origin.title:
#             return result.document.origin.title
            
#     return "Title Not Found"


# def get_title_by_font_size(file_path):
#     with pdfplumber.open(file_path) as pdf:
#         first_page = pdf.pages[0]
#         # Get all text objects on the page
#         chars = first_page.chars
#         if not chars:
#             return None
        
#         # Find the maximum font size
#         max_size = max(c['size'] for c in chars)
        
#         # Filter characters that have that max size
#         title_chars = [c['text'] for c in chars if c['size'] == max_size]
#         return "".join(title_chars).strip()

def extract_pdf_info(file_path):
    """
    Rückgabe: text (1st 3 pages), is_landscape (any page), 
    num_pages, has_comments (any page)
    """
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
    return _clean_text(full_text), is_landscape, num_pages, has_comments

def _clean_text( text):
    if not text: return ""
    text = unicodedata.normalize('NFKD', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def fetch_metadata_by_doi( text):
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
                authors = []
                if 'author' in data:
                    for a in data['author']:
                        family = a.get('family', '')
                        given = a.get('given', '')
                        if family:
                            name = f"{family}, {given}".strip(', ')
                            authors.append(name)
                
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
                container = data.get('container-title', [''])[0] if data.get('container-title') else ""
                tags = data.get('subject', [])
                
                return {
                    "DOI_ID":doi,
                    "title": title, "authors": authors, "date": pub_date,
                    "doc_type": doc_type, "tags": tags,
                    "publisher": publisher or container,
                    "abstract": ""
                }
        except Exception as e:
            print(f"   -> DOI Error: {e}")
            continue
    return None

def fetch_metadata_by_arxiv( text):
    arxiv_pattern = r'arxiv[:\s/]*(\d{4}\.\d{4,5}(?:v\d+)?)'
    match = re.search(arxiv_pattern, text, re.IGNORECASE)
    if match:
        aid = match.group(1)
        print(f"   -> arXiv ID gefunden: {aid}")
        try:
            # url = f"http://export.arxiv.org/api/query?id_list={aid}"
            url = f"https://export.arxiv.org/api/query?id_list={aid}"
            r = requests.get(url, timeout=15)
            
            r.raise_for_status() # This helps catch 403 or 500 errors earl
            # r = requests.get(url, timeout=10)
            if r.status_code == 200:
                ns = {'atom': 'http://www.w3.org/2005/Atom'}
                root = ET.fromstring(r.content)
                entry = root.find('atom:entry', ns)
                if entry:
                    title = entry.find('atom:title', ns).text.strip().replace('\n', ' ')
                    pub_raw = entry.find('atom:published', ns).text
                    pub_year = pub_raw[:4] if pub_raw else ""
                    summary = entry.find('atom:summary', ns).text.strip().replace('\n', ' ')
                    authors = [a.find('atom:name', ns).text for a in entry.findall('atom:author', ns)]
                    tags = ["arXiv", "Preprint"]
                    for cat in entry.findall('atom:category', ns):
                        term = cat.get('term')
                        if term: tags.append(term)
                    return {
                        "DOI_ID":aid,
                        "title": title, "authors": authors, "date": pub_year,
                        "doc_type": "Preprint", "tags": tags, "abstract": summary,
                        "publisher": "arXiv"
                    }
        except requests.exceptions.Timeout:
            print(f"   -> arXiv Error: The request timed out. arXiv might be slow right now.")
        except Exception as e:
            print(f"   -> arXiv Error: {e}")
            pass
    return None

def extract_meta_data_from_doi( file_path):   
    # Original PDF extraction
    text, is_landscape, num_pages, has_comments = extract_pdf_info(file_path)

    # print(f'text: \n {text} \n')
    # print(f'is_landscape: \n {is_landscape} \n')
    # print(f'num_pages: \n {num_pages} \n')
    # print(f'has_comments: \n {has_comments} \n')

    if not text or len(text) < 50:
        print('no text')

    # 1. DOI/arXiv Check
    doi_data = fetch_metadata_by_doi(text)
    # print(f'doi_data: \n {doi_data} \n')
    
    arxiv_data = fetch_metadata_by_arxiv(text) if not doi_data else None
    # print(f'arxiv_data: \n {arxiv_data} \n')
    
    raw_data = doi_data if doi_data else arxiv_data
    # print(f'raw_data: \n {raw_data} \n')
    
    source_label = "DOI" if doi_data else ("arXiv" if arxiv_data else "AI")
    # print(f'source_label: \n {source_label} \n')

    # 2. Construct the Template dictionary
    # We use .get() for dicts or getattr() for objects to stay safe
    def get_val(obj, key, default=""):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default) if obj else default

    # Prepare the specific fields for the template
    authors = get_val(raw_data, 'authors', [])
    # Join authors list into a string for Excel readability
    if isinstance(authors, list):
        authors = ", ".join(authors)

    # Logic for publisher or container
    publisher_val = get_val(raw_data, 'publisher') or get_val(raw_data, 'container', "N/A")

    # Build the final base_data dictionary
    base_data = {
        "DOI_ID": get_val(raw_data, 'DOI_ID') or get_val(raw_data, 'doi', "N/A"),
        "title": get_val(raw_data, 'title', "Title Not Found"),
        "authors": authors,
        "date": get_val(raw_data, 'date', "N/A"),
        "doc_type": get_val(raw_data, 'doc_type', "N/A"),
        "tags": get_val(raw_data, 'tags', []),
        "publisher": publisher_val,
        "abstract": get_val(raw_data, 'abstract', "")
    }

    # print(f'final base_data dictionary: \n {base_data} \n')
    
    # 3. Final Fallback check on title inside the dict
    if base_data["title"] == "Title Not Found":
        print("Warning: Title was not found in metadata.")

    # print(f"Successfully processed: {base_data['title']}")
    return base_data

import pdfplumber

def get_title_by_font_size(file_path):
    with pdfplumber.open(file_path) as pdf:
        first_page = pdf.pages[0]
        chars = first_page.chars

        if not chars:
            return None

        # Get unique font sizes sorted descending
        font_sizes = sorted({c["size"] for c in chars}, reverse=True)

        if not font_sizes:
            return None

        # Largest and next-largest font sizes
        max_size = font_sizes[0]
        next_max_size = font_sizes[1] if len(font_sizes) > 1 else None

        # Select chars belonging to max and next-max font size
        title_chars = [
            c for c in chars
            if c["size"] == max_size or c["size"] == next_max_size
        ]

        # Sort to maintain reading order
        title_chars.sort(key=lambda c: (c["top"], c["x0"]))

        # Join text
        title_text = "".join(c["text"] for c in title_chars).strip()

        return title_text if title_text else None


def get_title_metadata(file_path):
    try:
        reader = PdfReader(file_path)
        meta = reader.metadata
        if meta and meta.title:
            return meta.title
        return "No Metadata Title"

    except FileNotFoundError:
        # Triggers if the file path provided does not exist
        return "No Metadata Title"

    except Exception as e:
        # Triggers for other issues like corrupted PDFs, permission errors, etc.
        # You can optionally print or log 'e' here if you need to debug
        return "No Metadata Title"

def get_title_in_the_file(file_path,llm_service):
        meta_title = get_title_metadata(file_path)
        Font_title = get_title_by_font_size(file_path)
        base_data=extract_meta_data_from_doi(file_path)

        Prompt = f"""Title Strings:
                    - {meta_title}
                    - {Font_title}
                    - {base_data['title']}
                    """

        # # log prompt
        # print(Fore.MAGENTA + "LLM PROMPT:" + Style.RESET_ALL)
        # print(Fore.MAGENTA + Prompt + Style.RESET_ALL)

        LLM_Choosen = llm_call(Prompt, Sys_Prompt_Title_Analyzer,llm_service)

        # # log response
        # print(Fore.BLUE + "LLM RESPONSE:" + Style.RESET_ALL)
        # print(Fore.BLUE + str(LLM_Choosen) + Style.RESET_ALL)
        
        print(Fore.BLUE +'Title Identified: '+str(LLM_Choosen) + Style.RESET_ALL)

        return LLM_Choosen
# print(get_title_with_docling(source)+"\n")

# print(get_title_by_font_size(source)+"\n")

# # print(get_title_metadata(source))
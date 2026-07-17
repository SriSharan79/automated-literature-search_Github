import json
import sys
import time

import PyPDF2
from colorama import Fore

from alr.data_analysis.Abstract_Analyzer import analyze_abstract
from alr.common.excel_utils import extract_column, get_corresponding_value
from alr.common.general_utils import caluculate_time_taken, find_missing_elements
from alr.common.json_utils import get_value_by_pair
from alr.data_analysis.Refrences_log_utils import log_Ref_data_extracted
from alr.data_analysis.LLM_Reference_Extractor import process_references_from_chunks_from_Sec_JSON
from alr.data_analysis.Pdf_File_processor import process_pdf_abstract, process_pdf_file, process_pdf_mode_file
from pathlib import Path
import traceback

# MF = DataAnalyzeManager(folder_path)

def _as_path(p):
    """Convert str/pathlike/Path into Path safely."""
    if isinstance(p, Path):
        return 
    return Path(str(p))

def process_abstract(MF, progress_callback=None):
    # 1. Get the list of all successful UUIDs
    # Added a check to ensure the file exists before extracting
    if not MF.excel_success.exists():
        print("⚠️ No success log found. Nothing to process.")
        return

    uuid_list = extract_column(MF.excel_success, "UUID")
    
    # 2. Identify what has already been done
    if Path(MF.AD_Abstract_log_path).exists():  # Fixed: .exists()
        recorded_abstracts = extract_column(MF.AD_Abstract_log_path, "UUID")
        to_be_processed = find_missing_elements(uuid_list, recorded_abstracts)
    else:
        # If no log exists, we process everything
        to_be_processed = uuid_list
    
    # 3. Execution
    if not to_be_processed:
        print("✅ All abstracts are already up to date.")
        return

    print(f"🧠 Analyzing {len(to_be_processed)} new abstracts...")
    for i, item_id in enumerate(to_be_processed, 1):
        try:
            # Pass MF if the function needs paths to save the abstract
            file_name= get_corresponding_value(MF.excel_success, "UUID", item_id, "filename")
            if progress_callback:
                progress_callback(i, len(to_be_processed), file_name)
            print(f"🧠 Analyzing abstract of {file_name}")
            analyze_abstract(item_id, MF)
        except Exception as e:
            print(f"❌ Failed to analyze abstract for {item_id}: {e}")
            traceback.print_exc()

def process_references(MF, progress_callback=None):
    # 1. Get the list of all successful UUIDs
    # Added a check to ensure the file exists before extracting
    if not MF.excel_success.exists():
        print("⚠️ No success log found. Nothing to process.")
        return

    uuid_list = extract_column(MF.excel_success, "UUID")
    
    # 2. Identify what has already been done
    if Path(MF.refrences_excel_log_path).exists():  # Fixed: .exists()
        recorded_references = extract_column(MF.refrences_excel_log_path, "UUID")
        to_be_processed = find_missing_elements(uuid_list, recorded_references)
    else:
        # If no log exists, we process everything
        to_be_processed = uuid_list
    
    # 3. Execution
    if not to_be_processed:
        print("✅ All references are already up to date.")
        return

    print(f"🧠 Analyzing {len(to_be_processed)} new references...")
    for i, item_id in enumerate(to_be_processed, 1):
        try:
            if progress_callback:
                progress_callback(i, len(to_be_processed), str(item_id))
            # Pass MF if the function needs paths to save the abstract
            start_time = time.time()
            MF.update_id_files(item_id)
            ref_chunks = get_value_by_pair(MF.raw_sec_json_path,"Section Name","references","Chunks")
            chunks=[]
            if ref_chunks:
                chunks= [t[1] for t in ref_chunks]

            ref_json_path = _as_path(MF.ref_json_path)
            process_references_from_chunks_from_Sec_JSON(chunks, ref_json_path)
            if ref_json_path.exists() and len(chunks)!=0:
                try:
                    with open(ref_json_path, "r", encoding="utf-8") as f:
                        ref_data = json.load(f)
                        if isinstance(ref_data, list) and len(ref_data) > 0:
                            ref_end_time = time.time()
                            time_taken_4_ref_processing = caluculate_time_taken(start_time, ref_end_time)
                            pdf_name= get_corresponding_value(MF.excel_success,"UUID", item_id,"filename")

                            log_Ref_data_extracted(
                                MF.refrences_excel_log_path,
                                ref_json_path,
                                pdf_name,
                                item_id,
                                time_taken_4_ref_processing,
                            )
                            print(Fore.GREEN + f"✓ Success: {pdf_name} references logged." + Fore.RESET)
                except Exception as e:
                    print(Fore.YELLOW + f"⚠ Error checking reference JSON: {e}" + Fore.RESET)
                    traceback.print_exc()   


        except Exception as e:
            print(f"❌ Failed to analyze references for {item_id}: {e}")
            traceback.print_exc()
            

def process_folder(source_path, storage_path, n=25):
    # 1. Setup Paths
    source_root = Path(source_path)

    pdf_files = list(source_root.rglob("*.pdf"))

    # Batch run: load the Docling model pipeline once and reuse it for every PDF
    # instead of re-initialising it per file.
    doc_converter = None
    if pdf_files:
        try:
            from alr.data_analysis.Table_image_extractor import get_shared_doc_converter
            doc_converter = get_shared_doc_converter()
        except Exception as e:
            print(f"⚠️ Shared Docling converter unavailable; falling back to per-file extraction: {e}")

    # rglob("*.pdf") finds all PDFs in all subfolders
    for file_path in pdf_files:
        print(f"\n🔍 Checking: {file_path.name}")
        # Check the number of pages in the PDF
        with open(file_path, "rb") as file:
            reader = PyPDF2.PdfReader(file)
            num_pages = len(reader.pages)
        # Skip the file if it has more than 'n' pages
        if num_pages > n:
            print(f"⏭️ Skipping {file_path.name} because it has {num_pages} pages (more than {n}).")
            continue

        # Process the PDF if it's within the page limit
        process_pdf_mode_file(file_path, storage_path, 'a', doc_converter=doc_converter)


    print("\n🎉 Synchronization and Processing Complete.")

# process_abstract(MF)
# process_references(MF)
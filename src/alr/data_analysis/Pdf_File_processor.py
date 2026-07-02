from pathlib import Path
import sys
import os

import PyPDF2
import time
from alr.data_analysis.Introduction_Analyzer import analyze_Introduction, get_Introduction_text
from alr.data_analysis.File_Data_extraction_with_Docling import _categorise_sections, _excel_log_has_file, _extract_and_chunk, _process_sections, _save_section_outputs, _init_excel_data
from alr.common.file_manager import DataAnalyzeManager
from alr.common.general_utils import caluculate_time_taken, find_missing_elements, generate_unique_id,_as_path,add_hh_mm_ss
from alr.common.excel_utils import get_corresponding_value,extract_column,update_corresponding_value
from alr.common.json_utils import get_value_by_pair,get_chunks_from_references, pretty_print_json_from_file,print_json_file
from alr.data_analysis.title_extracter import get_title_in_the_file
from alr.data_analysis.LLM_Reference_Extractor import process_references_from_chunks_from_Sec_JSON
from alr.data_analysis.Refrences_log_utils import log_Ref_data_extracted, save_references_to_json
from alr.data_analysis.Abstract_Analyzer import analyze_abstract, get_abstract_text

from colorama import Fore, Style
from datetime import datetime as dt      # Alias avoids conflicts
import shutil
import pandas as pd
import traceback
import json
from pathlib import Path
from multiprocessing import Process, Queue
import signal
import logging

# Get base python logger for this processor module
logger = logging.getLogger("PdfFileProcessor")
logger.setLevel(logging.INFO)

# Stream handler to keep showing outputs to console/Tkinter Text Redirector
if not logger.handlers:
    c_handler = logging.StreamHandler(sys.stdout)
    c_handler.setLevel(logging.INFO)
    c_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    c_handler.setFormatter(c_format)
    logger.addHandler(c_handler)


def setup_pdf_file_logger(log_file_path: str) -> logging.FileHandler:
    """Creates, attaches, and returns a specific file logger handler."""
    Path(log_file_path).parent.mkdir(parents=True, exist_ok=True)
    f_handler = logging.FileHandler(log_file_path, encoding='utf-8', mode='a')
    f_handler.setLevel(logging.INFO)
    f_format = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    f_handler.setFormatter(f_format)
    logger.addHandler(f_handler)
    return f_handler


def close_pdf_file_logger(f_handler: logging.FileHandler):
    """Safely closes and removes the file logger handler to prevent log leaks."""
    if f_handler:
        f_handler.close()
        logger.removeHandler(f_handler)


class ChunkingResult:
    def __init__(self, doc=None, chunks=None, end_time=None, time_taken=None):
        self.doc = doc
        self.chunks = chunks
        self.end_time = end_time
        self.time_taken = time_taken

def _chunking_wrapper(queue, f_path, s_time,MF):
    """Subprocess wrapper for extraction."""
    try:
        d, c, et, tt = _extract_and_chunk(MF,logger,file_path=f_path, start_time=s_time)
        queue.put(ChunkingResult(doc=d, chunks=c, end_time=et, time_taken=tt))
    except Exception as e:
        traceback.print_exc()       
        queue.put(e)


def _run_extraction_with_timeout(file_path, start_time,MF, timeout=120):
    """Polls the queue to return data as soon as it is available."""
    result_queue = Queue()
    p = Process(target=_chunking_wrapper, args=(result_queue, file_path, start_time,MF))
    
    p.start()
    
    start_wait = time.time()
    while time.time() - start_wait < timeout:
        # Check if data has arrived
        if not result_queue.empty():
            result = result_queue.get()
            
            # Clean up the process now that we have what we need
            if p.is_alive():
                p.join(0.1) # Brief wait for natural exit
                p.terminate()
                p.join()
            return result
        
        # Check if the process died unexpectedly without putting data in queue
        if not p.is_alive() and result_queue.empty():
            raise ValueError("Subprocess exited prematurely without returning data.")
            
        time.sleep(0.1)  # Sleep briefly to prevent high CPU usage while polling

    # If the loop finishes, we've hit the 120s limit
    if p.is_alive():
        p.terminate()
        p.join()
        raise TimeoutError(f"Docling process timed out after {timeout}s")
    
    raise ValueError("Extraction failed to return data within the time limit.")


# --- Helper functions (modular) ---
def save_logs(new_success, success_path, new_failed, failed_path):
    """Appends new results to existing Excel logs or creates new ones."""
    
    # Process Success Logs
    if new_success:
        df_new = pd.DataFrame(new_success)
        if success_path.exists():
            df_old = pd.read_excel(success_path)
            df_final = pd.concat([df_old, df_new], ignore_index=True)
        else:
            df_final = df_new
        df_final.to_excel(success_path, index=False)
        logger.info(f"📊 Success log updated: {len(new_success)} entries added.")

    # Process Failure Logs
    if new_failed:
        df_new_f = pd.DataFrame(new_failed)
        if failed_path.exists():
            df_old_f = pd.read_excel(failed_path)
            df_final_f = pd.concat([df_old_f, df_new_f], ignore_index=True)
        else:
            df_final_f = df_new_f
        df_final_f.to_excel(failed_path, index=False)
        logger.warning(f"⚠️ Failure log updated: {len(new_failed)} entries added.")
        
def _init_manager(storage_path: str):
    if storage_path:
        MF = DataAnalyzeManager(storage_path)
    else:
        MF = DataAnalyzeManager()  

    pdf_dest_root = MF.pdf_subfolder
    failed_dest_root = MF.failed_pdf_folder
    excel_success = MF.excel_success
    excel_failed = MF.excel_failed

    # Ensure storage subfolders exist
    pdf_dest_root.mkdir(parents=True, exist_ok=True)
    failed_dest_root.mkdir(parents=True, exist_ok=True)

    return MF, pdf_dest_root, failed_dest_root, excel_success, excel_failed


def _load_registry(excel_success):
    processed_files = set()
    processed_titles = set()
    processed_ids = set()

    if excel_success.exists():
        try:
            df_existing = pd.read_excel(excel_success)
            processed_files = set(df_existing["filename"].astype(str).tolist())
            processed_titles = set(df_existing["title"].astype(str).tolist())
            processed_ids = set(df_existing["UUID"].astype(str).tolist())
        except Exception as e:
            logger.warning(f"⚠️ Could not read registry, starting fresh: {e}")
            traceback.print_exc()       

    return processed_files, processed_titles, processed_ids

def _recheck_title_(excel_success,file_path,llm_service):

    default_title = "Title Not Found"
    # 1. Load the Excel file
    df = pd.read_excel(excel_success)
    pdf_name = file_path.name 
    logger.info(f"--- Debug: Starting Recheck for {pdf_name} ---")

    
    # 1. Fetch existing title
    existing_title = get_corresponding_value(excel_success, "relative_path", file_path, "title")
    if not existing_title:
        existing_title = get_corresponding_value(excel_success, "filename", pdf_name, "title")

    logger.info(f"DEBUG: Existing title found in Excel: '{existing_title}'")   

    if existing_title and isinstance(existing_title,str):
        # 2. Check if it matches the 'Not Found' placeholder
        is_default = existing_title.lower() == default_title.lower()
        logger.info(f"DEBUG: Is current title the default/placeholder? {is_default}")


        if is_default or len(existing_title)<10:
            logger.info(f"DEBUG: Triggering LLM title extraction for: {file_path}...")
            
            # 3. Get new title from file
            current_title = get_title_in_the_file(file_path, llm_service)
            logger.info(f"DEBUG: LLM returned new title: '{current_title}'")
            
            # 4. Update and log
            update_corresponding_value(excel_success, "filename", pdf_name, "title", current_title)
            logger.info(f"DEBUG: Update successful for {pdf_name}.")
            logger.info(f"Final Action: Updated title from '{existing_title}' to '{current_title}'")
        else:
            logger.info(f"DEBUG: Skipping update. Title is already valid (not default).")
    else:
        logger.info(f"DEBUG: No entry found at all for path: {file_path}")
        
    logger.info(f"--- Debug: Finished Recheck ---\n")


def _is_skipped(file_path, current_title, processed_files, processed_titles, llm_service):
    def count_in_set(string_set, target_string):
    # Return 1 if the target_string is in the set, otherwise 0
        return 1 if target_string in string_set else 0
    
    title=current_title
    if current_title=='Title Not Found':        
        num=count_in_set(processed_titles,current_title)
        title=current_title+str(num+1)
        
    if file_path.name in processed_files or title in processed_titles:
        logger.info(f"  ⏩ Skipping: Already exists in registry.")
        return True
    return False

def _register_success(new_success, uniq_id, file_path, current_title, formatted_time,completed_steps):
    new_success.append({
        'UUID': uniq_id,
        'timestamp': dt.now().strftime('%Y-%m-%d %H:%M:%S'),
        'time_taken': formatted_time,
        'filename': file_path.name,
        'title': current_title,
        'relative_path': file_path,
        'sectioning': completed_steps['sectioning'],  # Individual key for sectioning
        'references': completed_steps['references'],  # Individual key for references
        'abstract': completed_steps['abstract'],  # Individual key for abstract
        'Introduction': completed_steps['Introduction']  # Individual key for abstract
    })


def _register_failure(new_failed, file_path, error_Msg, failed_dest_root):
    logger.error(f"  ❌ Failed: {error_Msg}")

    # --- UPDATED: Move the file instead of copying ---
    try:
        shutil.move(str(file_path), failed_dest_root)
    except Exception as move_error:
        logger.warning(f"  ⚠️ Warning: Could not move file: {move_error}")

    new_failed.append({
        "filename": file_path.name,
        "error": error_Msg,
        "timestamp": dt.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


# --- Main function (same logic + same prints) ---

def process_pdf_sections(file, storage_path=""):

    completed_steps = {"sectioning": 'Failed', "references": 'Failed', "abstract": 'Failed', "Introduction": 'Failed'}
    file_path = Path(file)    
    pdf_name = file_path.name
    MF, pdf_dest_root, failed_dest_root, excel_success, excel_failed = _init_manager(storage_path)
    llm_service = MF.llm_service if MF.llm_service else 'o'
    processed_files, processed_titles, processed_ids = _load_registry(excel_success)
    chunking_status = False
    new_success, new_failed = [], []
    
    start_time = time.time()

    try:
        # --- STEP A & B: Registry and Metadata ---
        current_title = get_title_in_the_file(file_path, llm_service)
        process_registry_check = _is_skipped(file_path, current_title, processed_files, processed_titles,llm_service)
        section_exists = _excel_log_has_file(MF.raw_section_excel_log_path, pdf_name)        
        
        uniq_id = generate_unique_id(file_path.name, processed_ids) 
        pdf_name, excel_data = _init_excel_data(file_path, uniq_id) 
        MF.update_id_files(uniq_id)

        if process_registry_check and section_exists: return 'P'

        if section_exists:
            if not Path(MF.raw_chunks_json_path).exists():
                _run_extraction_with_timeout(file_path, start_time,MF,timeout=300)

            chunk_time = get_corresponding_value(MF.raw_section_excel_log_path, "UUID", uniq_id, "Chunking Time")
            sec_time = get_corresponding_value(MF.raw_section_excel_log_path, "UUID", uniq_id, "Sectioning Time")
            formatted_time = add_hh_mm_ss(chunk_time, sec_time)
            completed_steps["sectioning"] = 'Passed'
            _register_success(new_success, uniq_id, file_path, current_title, formatted_time, completed_steps)
            save_logs(new_success, excel_success, [], None)  
            return 'P'

        # --- STEP C: Run Docling Process (Modularized) ---
        try:
            logger.info(f"\n[DEBUG] Running Extraction for: {pdf_name}")
            result_data = _run_extraction_with_timeout(file_path, start_time,MF,timeout=300)

            if isinstance(result_data, Exception):
                raise result_data

            # Safe Mapping of Variables
            doc = result_data.doc
            chunks = result_data.chunks
            chunking_end_time = result_data.end_time
            time_taken_4_chunking = result_data.time_taken

            if not chunks:
                raise ValueError("Extraction returned empty results.")

            chunking_status = True
            logger.info(f"[DEBUG] Extraction successful for {pdf_name}")

        except Exception as e:
            _register_failure(new_failed, file_path, str(e), failed_dest_root)            
            save_logs([], None, new_failed, excel_failed)   
            traceback.print_exc()
            return 'F'
        
        # --- STEP D: Section Processing ---
        if chunking_status:
            final_merged_content, refined_Sections_Raw_chunks, body_headings = _process_sections(chunks, doc, llm_service)

            processed_data_for_json, excel_data = _categorise_sections(
                refined_Sections_Raw_chunks=refined_Sections_Raw_chunks,
                final_merged_content=final_merged_content,
                body_headings=body_headings,
                excel_data=excel_data,
            )

            sec_processing_time = time.time()
            time_taken_4_section_processing = caluculate_time_taken(chunking_end_time, sec_processing_time)

            _save_section_outputs(
                processed_data_for_json=processed_data_for_json,
                Main_Folder=MF,
                excel_data=excel_data,
                time_taken_4_chunking=time_taken_4_chunking,
                time_taken_4_section_processing=time_taken_4_section_processing,
                pdf_name=pdf_name,
            )
            
            elapsed = time.time() - start_time
            formatted_time = time.strftime("%H:%M:%S", time.gmtime(elapsed))       

            completed_steps["sectioning"] = 'Passed'
            _register_success(new_success, uniq_id, file_path, current_title, formatted_time, completed_steps)
            save_logs(new_success, excel_success, [], None)  
            return 'P'                   

    except Exception as e:
        _register_failure(new_failed, file_path, str(e), failed_dest_root)
        save_logs([], None, new_failed, excel_failed)      
        traceback.print_exc()        
        return 'F'
       

def process_pdf_references(file, storage_path=""):
    file_path = Path(file)
    pdf_name = file_path.name
    MF, _, _, excel_success, _ = _init_manager(storage_path)
    
    # Check if section JSON exists - if not, we can't do references
    
    UUID = get_corresponding_value(excel_success, "filename", pdf_name, "UUID")
    MF.update_id_files(UUID)
    llm_service='o'
    if MF.llm_service:
        llm_service=MF.llm_service

    
    if not Path(MF.raw_sec_json_path).exists():
        logger.warning(f"⚠️ Missing Section JSON for {pdf_name}. Skipping references.")
        return 'F'

    start_time = time.time()

    try:
        # Check for existing logs safely
        ref_log = Path(MF.refrences_excel_log_path)
        if ref_log.exists():
            if _excel_log_has_file(str(ref_log), pdf_name):
                logger.info("⏩ References already processed.")
                return 'P'

        MF.update_id_files(UUID)
        ref_chunks = get_chunks_from_references(MF.raw_sec_json_path)
        
        if not ref_chunks:
            logger.warning("⚠️ No references found in chunks.")
            return 'P' # Return 'P' because we checked, even if empty

        ref_json_path = _as_path(MF.ref_json_path)
        process_references_from_chunks_from_Sec_JSON(ref_chunks, ref_json_path,'l') # update the LLM servicebased on your choiuce
        update_corresponding_value(excel_success, "UUID", UUID, "references", 'Passed')

        # Logging logic...
        ref_end_time = time.time()
        time_taken = caluculate_time_taken(start_time, ref_end_time)
        log_Ref_data_extracted(MF.refrences_excel_log_path, ref_json_path, pdf_name, UUID, time_taken)
        
        return 'P'

    except Exception as e:
        logger.error(f"❌ Reference Error for {pdf_name} - {UUID}: {e}")
        traceback.print_exc()       
        return 'F'


def process_pdf_abstract(file, storage_path=""):
    file_path = Path(file)
    pdf_name = file_path.name
    MF, _, _, excel_success, _ = _init_manager(storage_path)

    # --- Inner Functions (Closures) ---
    
    def _check_and_cleanup_json() -> bool:
        """
        Checks if 'ERROR_NO_ABSTRACT_FOUND' exists inside the target JSON values.
        Deletes the file if the error token is found.
        Returns True if an error token was found, False otherwise.
        """
        json_path = MF.abstract_json_path
        if not json_path:
            return False
            
        json_file = Path(json_path)
        if not json_file.exists():
            return False

        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if isinstance(data, dict) and any(str(val) == 'ERROR_NO_ABSTRACT_FOUND' for val in data.values()):
                logger.warning(f"⚠️ Found error token in {json_file.name}. Deleting file...")
                json_file.unlink(missing_ok=True)
                return True
        except Exception as json_err:
            logger.warning(f"⚠️ Could not read or delete JSON file: {json_err}")
            
        return False

    def _handle_status_update(uuid, passed: bool) -> str:
        """
        Updates the Excel file with the final status and returns the corresponding function flag.
        """
        status = 'Passed' if passed else 'Failed'
        return_flag = 'P' if passed else 'F'
        
        update_corresponding_value(excel_success, "UUID", uuid, "abstract", status)
        return return_flag

    # --- Main Execution Flow ---
    
    UUID = get_corresponding_value(excel_success, "filename", pdf_name, "UUID")
    if not UUID:
        return 'F'

    try:
        # Check if the log exists BEFORE calling get_corresponding_value
        log_path = Path(MF.AD_Abstract_log_path) if MF.AD_Abstract_log_path else None
        MF.update_id_files(UUID)
        
        if log_path and log_path.exists():
            already_done = get_corresponding_value(str(log_path), "UUID", UUID, "file_path")
            
            if already_done:
                # Run the inner error check and cleanup routine
                has_error_token = _check_and_cleanup_json()
                json_path = MF.abstract_json_path
                
                if not has_error_token:
                    logger.info(f"⏩ Abstract already successfully processed for {pdf_name} :-: {UUID}")        
                    # abstract_text = get_abstract_text(MF)
                    with open(json_path, 'r', encoding='utf-8') as f:
                        json_data = json.load(f)
                    abstract_text=json_data['Abstract Text identified:']
                    logger.info("\nIdentifid Abstract Text:")
                    logger.info(f"   {abstract_text}")
                    pretty_print_json_from_file(MF.abstract_json_path)
                    return  _handle_status_update(UUID, passed=True)
                else:
                    # If it had an error token, the file is now deleted. Log failure.
                    logger.warning(f"🛑 Previously processed file for {pdf_name} contained errors. Registering failure.")
                    return _handle_status_update(UUID, passed=False)

        # If file doesn't exist or log entry was not found, run a brand new analysis
        res = analyze_abstract(UUID, MF) 
        
        # Run error checking & dynamic file cleanup inner function
        has_error_token = _check_and_cleanup_json()

        # Evaluate final conditions
        is_successful = (res == 'P' and not has_error_token)
        
        # Handle data updates and return via inner function
        return _handle_status_update(UUID, passed=is_successful)
            
    except Exception as e:
        logger.error(f"❌ Abstract Error: {e}")
        traceback.print_exc()       
        return 'F'
    
def process_pdf_intro(file, storage_path=""):
    file_path = Path(file)
    pdf_name = file_path.name
    MF, _, _, excel_success, _ = _init_manager(storage_path)

    UUID = get_corresponding_value(excel_success, "filename", pdf_name, "UUID")
    if not UUID:
        return 'F'

    try:
        # Check if the log exists BEFORE calling get_corresponding_value
        log_path = Path(MF.AD_Intro_log_path) if MF.AD_Intro_log_path else None
        MF.update_id_files(UUID)
        if log_path and log_path.exists():
            already_done = get_corresponding_value(str(log_path), "UUID", UUID, "file_path")
            if already_done:
                logger.info(f"⏩ Introduction already processed for {pdf_name} :-: {UUID}")  
                logger.info("\nIdentifid Introduction Data:")
                print_json_file(MF.intro_json_path)                
                update_corresponding_value(excel_success, "UUID", UUID, "Introduction", 'Passed')
                return 'P'

        # If file doesn't exist or entry not found, run analysis
        res=analyze_Introduction(UUID, MF) 
        if res=='P':
            update_corresponding_value(excel_success, "UUID", UUID, "Introduction", 'Passed')
            return 'P'
        else:            
            update_corresponding_value(excel_success, "UUID", UUID, "Introduction", 'Failed')
            return 'F'
    except Exception as e:
        logger.error(f"❌ Introduction Error: {e}")
        traceback.print_exc()       
        return 'F'   

def process_pdf_file(file, storage_path=""):
    file_path = Path(file)   
    logger.info(f"🚀 Analyzing source: {file_path}")
    
    start_time = time.time()
    MF, _, _, excel_success, _ = _init_manager(storage_path)
    
    # Initialize file usage logger handler tracker
    f_handler = None
    
    # STEP 1: Sectioning (Must pass to continue)
    result = process_pdf_sections(file, storage_path)
    
    if result == 'P':
        UUID = get_corresponding_value(excel_success, "filename", file_path.name, "UUID")
        
        # --- ATTACH INDIVIDUAL FILE LOGGER ---
        MF.update_id_files(UUID)
        if hasattr(MF, 'file_usage_log_path') and MF.file_usage_log_path:
            f_handler = setup_pdf_file_logger(MF.file_usage_log_path)
            logger.info(f"--- Started dedicated file log for UUID: {UUID} ---")
        
        try:
            # STEP 2: Abstract
            abs_res = process_pdf_abstract(file, storage_path)
            if abs_res == 'P':
                # Check for path ONLY after we are sure the process passed
                if MF.AD_Abstract_log_path and Path(MF.AD_Abstract_log_path).exists():
                    abstract_processed = get_corresponding_value(MF.AD_Abstract_log_path, "UUID", UUID, "file_path")
                    logger.info(f"✅ Abstract data available at: {abstract_processed}")
                    print(f"✅ Abstract data available at: {abstract_processed}")
            
            # STEP 3: References
            ref_res = process_pdf_references(file, storage_path)
            if ref_res == 'P':
                if MF.refrences_excel_log_path and Path(MF.refrences_excel_log_path).exists():
                    ref_processed = get_corresponding_value(MF.refrences_excel_log_path, "UUID", UUID, "filename")
                    logger.info(f"✅ Reference data logged for: {ref_processed}")
                    print(f"✅ Reference data logged for: {ref_processed}")

            # Final Time Update
            elapsed = time.time() - start_time
            Time_update = time.strftime("%H:%M:%S", time.gmtime(elapsed))
            update_corresponding_value(excel_success, "UUID", UUID, 'time_taken', Time_update)
            
        finally:
            # --- DETACH INDIVIDUAL FILE LOGGER ---
            if f_handler:
                logger.info(f"--- Finished dedicated file log for UUID: {UUID} ---")
                print(f"--- Finished dedicated file log for UUID: {UUID} ---")
                close_pdf_file_logger(f_handler)
    else:
        logger.error(f"❌ Initial PDF sectioning failed for {file_path.name}")
        
def process_pdf_mode_file(file, storage_path="", mode=None):
    """
    Orchestrates the PDF processing.
    mode='a': Only attempts Abstract extraction.
    mode='r': Only attempts Reference extraction.
    mode=None: Runs the full pipeline (Sectioning -> Abstract -> References).
    """
    file_path = Path(file)
    
    # Initialize manager to get paths and registry
    MF, pdf_dest_root, failed_dest_root, excel_success, excel_failed = _init_manager(storage_path)
    llm_service = MF.llm_service if MF.llm_service else 'o'
    if excel_success.exists():
        _recheck_title_(excel_success,file_path,llm_service)    

    new_failed = []
    logger.info(f"\n🚀 Processing: {file_path.name} ")  
    print(f"\n🚀 Processing: {file_path.name} ")    
    with open(file_path, "rb") as file:
        reader = PyPDF2.PdfReader(file)
        num_pages = len(reader.pages)
        
    n = 50 # Default number of pages to compare
        
    if num_pages > n:
        logger.warning(f"⏭️ Skipping {file_path.name} because it has {num_pages} pages (more than {n}).")   
        Msg=f" Reason: {file_path.name} with {num_pages} pages takes longer duration and this tool is not capable for such documents." 
        logger.error(Msg)
        _register_failure(new_failed, file_path, Msg, failed_dest_root)
        save_logs([], None, new_failed, excel_failed)
        return 'F'
    
    start_time = time.time()

    # --- STEP 1: PREREQUISITE VALIDATION ---
    # Every individual mode REQUIRES a UUID and Section JSON to exist
    In_UUID = get_corresponding_value(excel_success, "filename", file_path.name, "UUID")
    
    # Pre-declare file logger handler tracker variable context
    f_handler = None
    MF.update_id_files(In_UUID)
    
    if not Path(MF.raw_chunks_json_path).exists():
        _run_extraction_with_timeout(file_path, start_time,MF,timeout=300)
    # If the user wants an individual run but sectioning isn't done, we must run it first
    if not In_UUID or not Path(MF.raw_sec_json_path).exists():
        logger.info(f"📦 Extracting Text from the file.")
        sec_result = process_pdf_sections(file_path, storage_path)
        if sec_result != 'P':
            logger.error("❌ Data Extraction failed. Cannot proceed.")
            return 'F'
        # Re-fetch new valid entry ID mapping context variables post-processing execution
        In_UUID = get_corresponding_value(excel_success, "filename", file_path.name, "UUID")
    
    # --- STEP 2: ATTACH FILE-SPECIFIC LOGGER ---

    if hasattr(MF, 'file_usage_log_path') and MF.file_usage_log_path:
        f_handler = setup_pdf_file_logger(MF.file_usage_log_path)
        logger.info(f"--- Started dedicated file log for UUID: {In_UUID} ---")

    try:
        # --- STEP 3: MODE-BASED EXECUTION ---
        result = 'P'
        UUID = get_corresponding_value(excel_success, "filename", file_path.name, "UUID")
        
        # Mode 'a' or Full Pipeline
        if mode == 'a' or mode is None:
            logger.info(f"📝 Processing Abstract for {file_path.name}...")
            result = process_pdf_abstract(file_path, storage_path)
            result = process_pdf_intro(file_path, storage_path)
            logger.info(f"🏁 Abstract & Introduction Extraction finished ")  
        
        # Mode 'r' or Full Pipeline
        if mode == 'r' or mode is None:
            logger.info(f"📚 Processing References for {file_path.name}...")
            result = process_pdf_references(file_path, storage_path)
            logger.info(f"🏁 References Extraction finished ") 

        # Final Timestamp Update
        elapsed = time.time() - start_time
        formatted_time = time.strftime("%H:%M:%S", time.gmtime(elapsed))
        update_corresponding_value(excel_success, "UUID", UUID, 'time_taken', formatted_time)
        
        return result

    finally:
        # --- STEP 4: DETACH FILE-SPECIFIC LOGGER ---
        if f_handler:
            logger.info(f"--- Finished dedicated file log for UUID: {In_UUID} ---")
            close_pdf_file_logger(f_handler)

if __name__ == "__main__":
    source_path="/remotedata/U/DLR+kata_du/ALR DATA/AI_RM/AI_REQ_Pdfs/Text_Readable/1990_JD Palmer_CAAATRA.pdf"
    storage_path="/remotedata/U/DLR+kata_du/ALR DATA/AI_RM/AI_REQ_Results"
    source_root = Path(source_path)
    
    process_pdf_mode_file(source_path,storage_path,'a')
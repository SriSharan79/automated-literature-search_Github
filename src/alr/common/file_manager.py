import sys
import threading

from alr.common.general_utils import print_with_separator
import os
import pandas as pd
from pathlib import Path
from datetime import datetime


home_folder = Path.home()
ALR_main_folder= home_folder/ "Automated Literature Review"
ALR_main_folder.mkdir(parents=True, exist_ok=True)
ALR_colletion_folder=ALR_main_folder/ "01_Collection"
ALR_data_analyze_folder=ALR_main_folder/ "02_Analyzed_Data"
ALR_Vec_DB_folder=ALR_main_folder/ "10_Vector_DBs"
# Fixed home for generated cross-space overviews.
ALR_overviews_folder = ALR_main_folder / "20_Overviews"
ALR_overviews_folder.mkdir(parents=True, exist_ok=True)

# Canonical markers that identify a DataAnalyzeManager storage space on disk.
# Single source of truth reused by the storage-space scanner.
STORAGE_MARKER_FILES = ("Processed_file_registry.xlsx",)
STORAGE_MARKER_DIRS = (
    "Analyzed_Data_Files",
    "Raw_Section_JSON_Files",
    "References_JSON_Files",
    "pdf_files",
    "Raw_Chunk_files",
)


# ---------------------------------------------------------------------------
# Managed-folder registry
#
# Every manager below eagerly creates its whole folder tree on construction, so
# a run always leaves behind the sub-folders nothing happened to write into.
# Each manager records its root here, which lets a finished pass clean up
# exactly the trees it touched (see artifact_cleanup.prune_touched_folders)
# without having to remember to wire cleanup into each individual pass.
# ---------------------------------------------------------------------------
_touched_folders: set[str] = set()
_touched_lock = threading.Lock()


def register_managed_folder(folder) -> None:
    """Record a folder tree that a manager just created (or re-opened)."""
    try:
        resolved = str(Path(folder).resolve())
    except OSError:
        return
    with _touched_lock:
        _touched_folders.add(resolved)


def take_managed_folders() -> list[str]:
    """
    Drain and return the folder roots recorded since the last call. Draining
    keeps each pass's cleanup scoped to the trees that pass actually opened,
    instead of re-walking every space the session has ever seen.
    """
    with _touched_lock:
        folders = sorted(_touched_folders)
        _touched_folders.clear()
    return folders


class CollectionManager:
    def __init__(self,Collection_Folder=ALR_colletion_folder):
        """
        Initializes the folder structure and static paths.
        """
        # 1. Convert to Path object FIRST
        self.folder = Path(Collection_Folder)
        
        # 2. Now you can safely call mkdir
        self.folder.mkdir(parents=True, exist_ok=True)
        register_managed_folder(self.folder)

        # PDF Folders
        self.keywords_list_folder = self.folder / "keywords_lists"
        self.keywords_list_folder.mkdir(exist_ok=True)
        self.keywords_list_log_path = os.path.join(self.folder, "keywords_list_log.xlsx")

        self.search_phrase_list_folder = self.folder / "search_phrase_lists"
        self.search_phrase_list_folder.mkdir(exist_ok=True)
        self.search_phrase_log_path = os.path.join(self.folder, "search_phrase_list_log.xlsx")

        self.publications_list_folder = self.folder / "publications_lists"
        self.publications_list_folder.mkdir(exist_ok=True)
        self.publications_log_path = os.path.join(self.folder, "publications_list_log.xlsx")
        

        # Placeholders for ID-specific paths
        self.topic_id= None
        self.keywords_list_excel = None
        self.search_phrase_list_excel = None
        self.publications_list_excel = None

        self.Research_Area = None
        self.Research_Question = None
        self.Research_Scope = None
        self.Keyword_list = None
        self.Keyword_count = None
        self.Search_phrase_count = None
        self.Search_phrase_list = None       
        
        self.llm_service = 'b'
        print_with_separator("DebugLog",'/')

        print(f"Data Storage Initialized at: {Collection_Folder}")

    def ensure_folders(self):
        """
        (Re-)create the managed folder tree and return self.

        The tree is built once in ``__init__``, but empty sub-folders are removed
        again by ``artifact_cleanup.prune_touched_folders``, which runs at the end
        of *every* background pass (see ``main_window._run_threaded``). A pass that
        only derives the scope therefore leaves ``keywords_lists/``,
        ``search_phrase_lists/`` and ``publications_lists/`` pruned away, and the
        next pass that writes into them fails with pandas'
        "Cannot save file into a non-existent directory".

        Any pass that is about to write must call this first. It is cheap
        (``exist_ok=True``) and safe to call repeatedly.
        """
        self.folder.mkdir(parents=True, exist_ok=True)
        register_managed_folder(self.folder)
        for sub in (self.keywords_list_folder,
                    self.search_phrase_list_folder,
                    self.publications_list_folder):
            Path(sub).mkdir(parents=True, exist_ok=True)
        return self

    def update_topic_files(self, doc_id):
        """
        Updates the specific JSON paths for a given document ID.
        Replaces the old 'Update_ID_Files' global logic.
        """
        self.ensure_folders()
        self.topic_id = doc_id

        self.keywords_list_json = os.path.join(self.keywords_list_folder, f"{doc_id}_keywords_list.json")

        self.search_phrase_list_excel = os.path.join(self.search_phrase_list_folder, f"{doc_id}_search_phrase_list.xlsx")
        self.search_phrase_sorted_list_excel = os.path.join(self.search_phrase_list_folder, f"{doc_id}_search_phrase_sorted_list.xlsx")

        self.publications_list_excel = os.path.join(self.publications_list_folder, f"{doc_id}_publications_list.xlsx")

        
        print_with_separator("DebugLog",'/')

        print(f"File paths updated for ID: {doc_id}")
        
    def update_llm_service(self, Value):
        self.llm_service =Value
        print_with_separator("DebugLog",'/')        
        print(f"updated llm_service: {Value}")

    def update_Research_Area(self, Value):
        self.Research_Area =Value
        print_with_separator("DebugLog",'/')        
        print(f"updated Research_Area: {Value}")

    def update_Research_Question(self, Value):
        self.Research_Question =Value
        print_with_separator("DebugLog",'/')        
        print(f"updated Research_Question: {Value}")

    def update_Research_Scope(self, Value):
        self.Research_Scope =Value
        print_with_separator("DebugLog",'/')        
        print(f"updated Research_Scope: {Value}")

    def update_Keyword_list(self, Value):
        self.Keyword_list =Value
        self.Keyword_count =len(Value)
        print_with_separator("DebugLog",'/')        
        print(f"updated list of {len(Value)} Keywords: {Value}")

    def update_Search_phrase_list(self, Value):
        self.Search_phrase_list =Value      
        self.Search_phrase_count =len(Value)
        print_with_separator("DebugLog",'/')
        print(f"updated list of {len(Value)} Search_phrases")


class DataAnalyzeManager:
    def __init__(self, folder_path=ALR_data_analyze_folder):
        """
        Initializes the folder structure and static paths.
        """
        # 1. Convert to Path object FIRST
        self.folder = Path(folder_path)
        
        current_date = datetime.now().strftime("%Y-%m-%d")
        
        # 2. Now you can safely call mkdir
        self.folder.mkdir(parents=True, exist_ok=True)
        register_managed_folder(self.folder)
        # Core Excel Files
        self.excel_success = self.folder / "Processed_file_registry.xlsx"
        self.excel_failed = self.folder / "failed_files.xlsx"

        # PDF Folders
        self.pdf_subfolder = self.folder / "pdf_files"
        self.pdf_subfolder.mkdir(exist_ok=True)

        self.failed_pdf_folder = self.folder / "failed_pdfs"
        self.failed_pdf_folder.mkdir(exist_ok=True)

        # Section JSON Logic
        self.raw_chunks_subfolder = self.folder / "Raw_Chunk_files"
        self.raw_chunks_subfolder.mkdir(exist_ok=True)
        
        self.tables_subfolder = self.folder / "Raw_Tables_files"
        self.tables_subfolder.mkdir(exist_ok=True)
                        
        self.images_subfolder = self.folder / "Raw_Images_files"
        self.images_subfolder.mkdir(exist_ok=True)
        
        #refined Sections
        self.raw_section_subfolder = self.folder / "Raw_Section_JSON_Files"
        self.raw_section_subfolder.mkdir(exist_ok=True)
        self.raw_section_excel_log_path = os.path.join(self.raw_section_subfolder, "Raw_Section_log.xlsx")
        
        #File_usage_Log
        self.Files_Usage_Log_subfolder = self.folder / "Files_Usage_Log_files"
        self.Files_Usage_Log_subfolder.mkdir(exist_ok=True)

        # References JSON Logic
        self.references_subfolder = self.folder / "References_JSON_Files"
        self.references_subfolder.mkdir(exist_ok=True)
        self.refrences_excel_log_path = os.path.join(self.references_subfolder, "Refrences_log.xlsx")

        #Analyzed Data 
        self.AD = self.folder / "Analyzed_Data_Files"
        self.AD.mkdir(exist_ok=True)
        self.AD_log_path = os.path.join(self.AD, "AD_log.xlsx")#---Usagae not defined

        self.AD_Abstract = self.AD/ "Abstract_Data_Files"
        self.AD_Abstract.mkdir(exist_ok=True)
        self.AD_Abstract_log_path = os.path.join(self.AD_Abstract, "Abstract_log.xlsx")
        
        
        self.AD_Intro = self.AD/ "Introduction_Data_Files"
        self.AD_Intro.mkdir(exist_ok=True)
        self.AD_Intro_log_path = os.path.join(self.AD_Intro, "Introduction_log.xlsx")

        self.AD_ResCon = self.AD/ "Results_Conclusion_Data_Files"
        self.AD_ResCon.mkdir(exist_ok=True)
        self.AD_ResCon_log_path = os.path.join(self.AD_ResCon, "Results_Conclusion_log.xlsx")

        # Fixed managed locations for enrichment outputs (DOI metadata,
        # publication classification). Always created inside the storage space.
        self.doi_metadata_subfolder = self.folder / "DOI_Metadata_Files"
        self.doi_metadata_subfolder.mkdir(exist_ok=True)
        self.doi_metadata_excel = os.path.join(self.doi_metadata_subfolder, f"{current_date}_DOI_Metadata.xlsx")

        self.classification_subfolder = self.folder / "Publication_Classification_Files"
        self.classification_subfolder.mkdir(exist_ok=True)
        self.classification_excel = os.path.join(self.classification_subfolder, f"{current_date}_Title_Classification.xlsx")
        # Abstract-based classification (uses the identified abstract text).
        self.abstract_classification_excel = os.path.join(self.classification_subfolder, f"{current_date}_Abstract_Classification.xlsx")
        # Question-scored classification (multi-sheet, on-demand) output.
        self.question_classification_excel = os.path.join(self.classification_subfolder, f"{current_date}_Question_Scored_Classification.xlsx")

        # Batch de-duplication skip log (PDFs skipped before analysis because a
        # fuzzy-matching title was already analyzed).
        self.batch_dedup_subfolder = self.folder / "Batch_Dedup_Files"
        self.batch_dedup_subfolder.mkdir(exist_ok=True)
        self.duplicate_log_excel = os.path.join(self.batch_dedup_subfolder, "Skipped_Duplicates.xlsx")
        # Persistent record of every PDF already scanned for duplication (with its
        # extracted title + decision), so later batches skip re-scanning them and
        # only title-extract genuinely new files.
        self.dedup_scan_log_excel = os.path.join(self.batch_dedup_subfolder, "Dedup_Scan_Log.xlsx")

        # Placeholders for ID-specific paths
        self.raw_sec_json_path = None
        self.raw_chunks_json_path = None
        self.file_usage_log_path = None
        self.ref_json_path = None
        self.current_id = None
        self.abstract_json_path = None
        self.intro_json_path = None
        self.rescon_json_path = None
        self.tables_storage_path=None
        self.image_storage_path=None
        
        self.llm_service = 'b'

        # print(f"Data Storage Initialized at: {folder_path}")

    def update_id_files(self, doc_id):
        """
        Updates the specific JSON paths for a given document ID.
        Replaces the old 'Update_ID_Files' global logic.
        """
        self.current_id = doc_id        
        self.raw_sec_json_path = os.path.join(self.raw_section_subfolder, f"{doc_id}_raw_sections.json")
        self.raw_chunks_json_path = os.path.join(self.raw_chunks_subfolder, f"{doc_id}_raw_chunks.json")
        self.file_usage_log_path = os.path.join(self.Files_Usage_Log_subfolder, f"{doc_id}_file_usage.log")
        self.ref_json_path = os.path.join(self.references_subfolder, f"{doc_id}_References.json")
        self.abstract_json_path=os.path.join(self.AD_Abstract, f"{doc_id}_Abstract.json")
        self.intro_json_path=os.path.join(self.AD_Intro, f"{doc_id}_Intro.json")
        self.rescon_json_path=os.path.join(self.AD_ResCon, f"{doc_id}_Results_Conclusion.json")
        
        # Seperate table folder
        self.tables_storage_path = self.tables_subfolder / f"{doc_id}_Tables_files"
        self.tables_storage_path.mkdir(exist_ok=True)
        
        self.image_storage_path = self.images_subfolder / f"{doc_id}_Images_files"
        self.image_storage_path.mkdir(exist_ok=True)

        # print(f"File paths updated for ID: {doc_id}")

    def update_llm_service(self, Value):
        self.llm_service =Value
        print_with_separator("DebugLog",'/')
        print(f"updated llm_service: {Value}")

    @staticmethod
    def describe_folder(path):
        """
        Read-only inspection of a folder to decide whether it is a
        DataAnalyzeManager storage space, and how complete it is. Does NOT
        create anything. Returns a dict with marker presence and counts.
        """
        p = Path(path)
        registry = p / "Processed_file_registry.xlsx"
        ad = p / "Analyzed_Data_Files"
        abstract_dir = ad / "Abstract_Data_Files"

        present_dirs = [d for d in STORAGE_MARKER_DIRS if (p / d).is_dir()]
        n_pdfs = len(list((p / "pdf_files").glob("*.pdf"))) if (p / "pdf_files").is_dir() else 0
        n_abstracts = len(list(abstract_dir.glob("*_Abstract.json"))) if abstract_dir.is_dir() else 0

        n_registry = 0
        if registry.exists():
            try:
                n_registry = len(pd.read_excel(registry))
            except Exception:
                n_registry = 0

        has_registry = registry.exists()
        is_space = has_registry or len(present_dirs) >= 2
        complete = has_registry and n_abstracts > 0

        return {
            "path": str(p),
            "is_space": is_space,
            "status": ("complete" if complete else "partial") if is_space else "none",
            "has_registry": has_registry,
            "present_dirs": present_dirs,
            "n_pdfs": n_pdfs,
            "n_registry": n_registry,
            "n_abstracts": n_abstracts,
        }


class Vec_DB_Manager:
    def __init__(self, folder_path=ALR_Vec_DB_folder):
        """
        Initializes the folder structure and static paths.
        """
        # 1. Convert to Path object FIRST
        self.folder = Path(folder_path)
        
        # 2. Now you can safely call mkdir
        self.folder.mkdir(parents=True, exist_ok=True)
        register_managed_folder(self.folder)

        current_date = datetime.now().strftime("%Y-%m-%d")
        # Abstract DBs
        self.Abstract_DB = self.folder / "Abstract_DB"
        self.Abstract_DB.mkdir(exist_ok=True)
        
        self.Abstract_Overview_folder = self.Abstract_DB / "Abstract_Overview_folder"
        self.Abstract_Overview_folder.mkdir(exist_ok=True)
        
        self.Abstract_DB_Excel = self.Abstract_DB / "Abstract_DB_Excel"
        self.Abstract_DB_Excel.mkdir(exist_ok=True)        
        
        self.Abstract_DB_JSON = self.Abstract_DB / "Abstract_DB_JSON"
        self.Abstract_DB_JSON.mkdir(exist_ok=True)
                
        self.Abstract_DB_Vec_bins = self.Abstract_DB / "Abstract_DB_Vec_bins"
        self.Abstract_DB_Vec_bins.mkdir(exist_ok=True)
    
        self.Abstract_Overview = self.Abstract_Overview_folder / f"{current_date}_Abstract_Overview.xlsx"

        self.Abstract_Eval_Overview = self.Abstract_Overview_folder / f"{current_date}_Abstract_Eval_Overview.xlsx"
        # Batch metric evaluation results: one workbook per metric kind plus a
        # combined overview workbook holding all metric data together.
        self.Abstract_Lexical_Metrics = self.Abstract_Overview_folder / f"{current_date}_Abstract_Lexical_Metrics.xlsx"
        self.Abstract_Distance_Metrics = self.Abstract_Overview_folder / f"{current_date}_Abstract_Distance_Metrics.xlsx"
        self.Abstract_Cosine_Metrics = self.Abstract_Overview_folder / f"{current_date}_Abstract_Cosine_Metrics.xlsx"
        self.Abstract_Metrics_Overview = self.Abstract_Overview_folder / f"{current_date}_Abstract_Metrics_Overview.xlsx"
        # Per-document sentence-level metric detail JSONs ({uuid}_..._Sentence_Metrics.json).
        self.Abstract_Metric_Details = self.Abstract_Overview_folder / "Metric_Sentence_Details"

        self.Abstract_Eval = self.Abstract_DB / "Abstract_LLM_evaluation"
        self.Abstract_Eval.mkdir(exist_ok=True)

        # Introduction DBs (evaluation of the analyzed-introduction JSONs,
        # mirroring the abstract evaluation structure).
        self.Introduction_DB = self.folder / "Introduction_DB"
        self.Introduction_DB.mkdir(exist_ok=True)

        self.Introduction_Eval = self.Introduction_DB / "Introduction_LLM_evaluation"
        self.Introduction_Eval.mkdir(exist_ok=True)

        self.Introduction_Eval_Overview = self.Introduction_DB / f"{current_date}_Introduction_Eval_Overview.xlsx"
        # Batch metric evaluation results for introduction data (one workbook
        # per metric kind + a combined overview workbook).
        self.Introduction_Lexical_Metrics = self.Introduction_DB / f"{current_date}_Introduction_Lexical_Metrics.xlsx"
        self.Introduction_Distance_Metrics = self.Introduction_DB / f"{current_date}_Introduction_Distance_Metrics.xlsx"
        self.Introduction_Cosine_Metrics = self.Introduction_DB / f"{current_date}_Introduction_Cosine_Metrics.xlsx"
        self.Introduction_Metrics_Overview = self.Introduction_DB / f"{current_date}_Introduction_Metrics_Overview.xlsx"
        self.Introduction_Metric_Details = self.Introduction_DB / "Metric_Sentence_Details"

        # Per-intro-section evaluation workbooks (see sections.INTRO_SECTIONS).
        self.Background_Eval_excel = self.Introduction_Eval / "Background_Eval.xlsx"
        self.Motivation_Eval_excel = self.Introduction_Eval / "Motivation_Eval.xlsx"
        self.Gaps_Limitations_Eval_excel = self.Introduction_Eval / "Gaps_Limitations_Eval.xlsx"
        self.RQs_Scope_Eval_excel = self.Introduction_Eval / "RQs_Scope_Eval.xlsx"

        # Introduction RAG text/vector DBs (see sections.INTRO_RAG_SECTIONS),
        # mirroring the Abstract_DB Excel/JSON/Vec_bins layout.
        self.Introduction_DB_Excel = self.Introduction_DB / "Introduction_DB_Excel"
        self.Introduction_DB_Excel.mkdir(exist_ok=True)
        self.Introduction_DB_JSON = self.Introduction_DB / "Introduction_DB_JSON"
        self.Introduction_DB_JSON.mkdir(exist_ok=True)
        self.Introduction_DB_Vec_bins = self.Introduction_DB / "Introduction_DB_Vec_bins"
        self.Introduction_DB_Vec_bins.mkdir(exist_ok=True)

        self.Background_DB_excel = self.Introduction_DB_Excel / "Background_DB.xlsx"
        self.Motivation_DB_excel = self.Introduction_DB_Excel / "Motivation_DB.xlsx"
        self.Gaps_Limitations_DB_excel = self.Introduction_DB_Excel / "Gaps_Limitations_DB.xlsx"
        self.RQs_Scope_DB_excel = self.Introduction_DB_Excel / "RQs_Scope_DB.xlsx"

        self.Background_DB_json = self.Introduction_DB_JSON / "Background_DB.json"
        self.Motivation_DB_json = self.Introduction_DB_JSON / "Motivation_DB.json"
        self.Gaps_Limitations_DB_json = self.Introduction_DB_JSON / "Gaps_Limitations_DB.json"
        self.RQs_Scope_DB_json = self.Introduction_DB_JSON / "RQs_Scope_DB.json"

        self.Background_DB_bin = self.Introduction_DB_Vec_bins / "Background_DB.bin"
        self.Motivation_DB_bin = self.Introduction_DB_Vec_bins / "Motivation_DB.bin"
        self.Gaps_Limitations_DB_bin = self.Introduction_DB_Vec_bins / "Gaps_Limitations_DB.bin"
        self.RQs_Scope_DB_bin = self.Introduction_DB_Vec_bins / "RQs_Scope_DB.bin"

        # Results & Conclusion RAG text/vector DBs (see
        # sections.RESCON_RAG_SECTIONS), same layout again.
        self.ResCon_DB = self.folder / "Results_Conclusion_DB"
        self.ResCon_DB.mkdir(exist_ok=True)
        self.ResCon_DB_Excel = self.ResCon_DB / "Results_Conclusion_DB_Excel"
        self.ResCon_DB_Excel.mkdir(exist_ok=True)
        self.ResCon_DB_JSON = self.ResCon_DB / "Results_Conclusion_DB_JSON"
        self.ResCon_DB_JSON.mkdir(exist_ok=True)
        self.ResCon_DB_Vec_bins = self.ResCon_DB / "Results_Conclusion_DB_Vec_bins"
        self.ResCon_DB_Vec_bins.mkdir(exist_ok=True)
        self.ResCon_Eval = self.ResCon_DB / "Results_Conclusion_LLM_evaluation"
        self.ResCon_Eval.mkdir(exist_ok=True)

        # Results & Conclusion evaluation overview + batch metric workbooks
        # (mirroring the abstract/introduction evaluation structure).
        self.ResCon_Eval_Overview = self.ResCon_DB / f"{current_date}_Results_Conclusion_Eval_Overview.xlsx"
        self.ResCon_Lexical_Metrics = self.ResCon_DB / f"{current_date}_Results_Conclusion_Lexical_Metrics.xlsx"
        self.ResCon_Distance_Metrics = self.ResCon_DB / f"{current_date}_Results_Conclusion_Distance_Metrics.xlsx"
        self.ResCon_Cosine_Metrics = self.ResCon_DB / f"{current_date}_Results_Conclusion_Cosine_Metrics.xlsx"
        self.ResCon_Metrics_Overview = self.ResCon_DB / f"{current_date}_Results_Conclusion_Metrics_Overview.xlsx"
        self.ResCon_Metric_Details = self.ResCon_DB / "Metric_Sentence_Details"

        self.Results_Mentioned_DB_excel = self.ResCon_DB_Excel / "Results_Mentioned_DB.xlsx"
        self.Limitations_Boundary_DB_excel = self.ResCon_DB_Excel / "Limitations_Boundary_DB.xlsx"
        self.Content_Summary_DB_excel = self.ResCon_DB_Excel / "Content_Summary_DB.xlsx"
        self.Future_Work_DB_excel = self.ResCon_DB_Excel / "Future_Work_DB.xlsx"
        self.Outlook_DB_excel = self.ResCon_DB_Excel / "Outlook_DB.xlsx"

        self.Results_Mentioned_DB_json = self.ResCon_DB_JSON / "Results_Mentioned_DB.json"
        self.Limitations_Boundary_DB_json = self.ResCon_DB_JSON / "Limitations_Boundary_DB.json"
        self.Content_Summary_DB_json = self.ResCon_DB_JSON / "Content_Summary_DB.json"
        self.Future_Work_DB_json = self.ResCon_DB_JSON / "Future_Work_DB.json"
        self.Outlook_DB_json = self.ResCon_DB_JSON / "Outlook_DB.json"

        self.Results_Mentioned_DB_bin = self.ResCon_DB_Vec_bins / "Results_Mentioned_DB.bin"
        self.Limitations_Boundary_DB_bin = self.ResCon_DB_Vec_bins / "Limitations_Boundary_DB.bin"
        self.Content_Summary_DB_bin = self.ResCon_DB_Vec_bins / "Content_Summary_DB.bin"
        self.Future_Work_DB_bin = self.ResCon_DB_Vec_bins / "Future_Work_DB.bin"
        self.Outlook_DB_bin = self.ResCon_DB_Vec_bins / "Outlook_DB.bin"

        # Per-rescon-section evaluation workbooks (registered on the
        # SectionSpecs for uniformity; written once an evaluation pass for
        # Results & Conclusion data exists).
        self.Results_Mentioned_Eval_excel = self.ResCon_Eval / "Results_Mentioned_Eval.xlsx"
        self.Limitations_Boundary_Eval_excel = self.ResCon_Eval / "Limitations_Boundary_Eval.xlsx"
        self.Content_Summary_Eval_excel = self.ResCon_Eval / "Content_Summary_Eval.xlsx"
        self.Future_Work_Eval_excel = self.ResCon_Eval / "Future_Work_Eval.xlsx"
        self.Outlook_Eval_excel = self.ResCon_Eval / "Outlook_Eval.xlsx"
        
        self.results = self.folder/ "Querry_results"
        self.results.mkdir(exist_ok=True)

        self.DB_update_logger= self.Abstract_DB / "DB_update_logger.json"

        # Core Excel Files
        self.Research_problem_DB_excel = self.Abstract_DB_Excel / "Research_problem_DB.xlsx"
        self.Objective_DB_excel = self.Abstract_DB_Excel / "Objective_DB.xlsx"
        self.Methodology_DB_excel = self.Abstract_DB_Excel / "Methodology_DB.xlsx"
        self.Conclusion_DB_excel = self.Abstract_DB_Excel / "Conclusion_DB.xlsx" 
        self.Results_DB_excel = self.Abstract_DB_Excel / "Results_DB.xlsx"   
        self.Key_concepts_DB_excel = self.Abstract_DB_Excel / "Key_concepts_DB.xlsx"   
        self.Research_Areas_DB_excel = self.Abstract_DB_Excel / "Research_Areas_DB.xlsx"     
         
        # Core JSON Files
        self.Research_problem_DB_json = self.Abstract_DB_JSON / "Research_problem_DB.json"
        self.Objective_DB_json = self.Abstract_DB_JSON / "Objective_DB.json"
        self.Methodology_DB_json = self.Abstract_DB_JSON / "Methodology_DB.json"
        self.Conclusion_DB_json = self.Abstract_DB_JSON / "Conclusion_DB.json"
        self.Results_DB_json = self.Abstract_DB_JSON / "Results_DB.json"   
        self.Key_concepts_DB_json = self.Abstract_DB_JSON / "Key_concepts_DB.json"   
        self.Research_Areas_DB_json = self.Abstract_DB_JSON / "Research_Areas_DB.json"   
        
        # Core DB files
        self.Research_problem_DB_bin = self.Abstract_DB_Vec_bins / "Research_problem_DB.bin"
        self.Objective_DB_bin = self.Abstract_DB_Vec_bins / "Objective_DB.bin"
        self.Methodology_DB_bin = self.Abstract_DB_Vec_bins / "Methodology_DB.bin"
        self.Conclusion_DB_bin = self.Abstract_DB_Vec_bins / "Conclusion_DB.bin"
        self.Results_DB_bin = self.Abstract_DB_Vec_bins / "Results_DB.bin"   
        self.Key_concepts_DB_bin = self.Abstract_DB_Vec_bins / "Key_concepts_DB.bin"   
        self.Research_Areas_DB_bin = self.Abstract_DB_Vec_bins / "Research_Areas_DB.bin"   
        
        
        self.Research_problem_Eval_excel = self.Abstract_Eval/ "Research_problem_Eval.xlsx"
        self.Objective_Eval_excel = self.Abstract_Eval/ "Objective_Eval.xlsx"
        self.Methodology_Eval_excel = self.Abstract_Eval/ "Methodology_Eval.xlsx"
        self.Conclusion_Eval_excel = self.Abstract_Eval/ "Conclusion_Eval.xlsx" 
        self.Results_Eval_excel = self.Abstract_Eval/ "Results_Eval.xlsx"   
        self.Key_concepts_Eval_excel = self.Abstract_Eval/ "Key_concepts_Eval.xlsx"   
        self.Research_Areas_Eval_excel = self.Abstract_Eval/ "Research_Areas_Eval.xlsx"  

        self.key_folder=None
        self.querry_storage=None

        self.querry_storage_pdfs=None
        self.querry_storage_Abs_jsons=None
        self.querry_storage_Intro_jsons=None
        self.querry_storage_ResCon_jsons=None

    def update_key_folder(self, key):
        self.key_folder=self.results/key
        self.key_folder.mkdir(exist_ok=True)

    def update_query_folder(self, query_name):
        # 1. Define the base query folder
        query_folder = self.results / "Queries"
        
        # 2. Create the date-based subfolder path
        current_date = datetime.now().strftime("%Y-%m-%d")
        date_folder = query_folder / current_date
        
        # 3. Create all directories at once (parents=True handles "Queries")
        date_folder.mkdir(parents=True, exist_ok=True)
        
        # 4. Set the final storage path
        self.query_storage = date_folder / query_name
        self.query_storage.mkdir(exist_ok=True)

        # Harvest destinations. These are paths only -- none of them is created
        # here, because a query needs at most the ones its selected attributes
        # actually harvest into (copy_matching_jsons/_pdfs create the destination
        # themselves, and the harvest step removes any that stay empty). Creating
        # them eagerly left an empty Pdf_files + Abstract_Json_files behind for
        # every single query.
        self.querry_storage_pdfs = self.query_storage / 'Pdf_files'
        self.querry_storage_Abs_jsons = self.query_storage / 'Abstract_Json_files'
        self.querry_storage_Intro_jsons = self.query_storage / 'Introduction_Json_files'
        self.querry_storage_ResCon_jsons = self.query_storage / 'Results_Conclusion_Json_files'
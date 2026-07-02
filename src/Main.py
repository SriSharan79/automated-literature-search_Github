

from alr.data_analysis.Pdf_File_processor import process_pdf_mode_file
from alr.common.file_manager import DataAnalyzeManager
from alr.data_analysis.Folder_Data_Analyzer import process_abstract
import PyPDF2
from pathlib import Path

# path_pairs = [
#     (
#         "/remotedata/U/DLR+kata_du/ALR DATA/AI_RM/AI_REQ_Pdfs/Text_Readable",
#         "/remotedata/U/DLR+kata_du/ALR DATA/AI_RM/AI_REQ_Results"
#     ),
#     (
#         "/remotedata/U/DLR+kata_du/ALR DATA/AI_SE_Domains_main/AI_SE_Domains_PDF",
#         "/remotedata/U/DLR+kata_du/ALR DATA/AI_SE_Domains_main/AI_SE_Processed_results"
#     ),
#     (
#         "/remotedata/U/DLR+kata_du/ALR DATA/LLM_Safety/LLM_Safety_Certification_Pdfs/Text_Readable",
#         "/remotedata/U/DLR+kata_du/ALR DATA/LLM_Safety/LLM_Safety_Results"
#     ),
#     (
#         "/remotedata/U/DLR+kata_du/ALR DATA/MBSE_MBSA_Aviation/MBSE_MBSA_Aviation_Pdfs/Text_Readable",
#         "/remotedata/U/DLR+kata_du/ALR DATA/MBSE_MBSA_Aviation/MBSE_MBSA_Aviation_Results"
#     ),
#     (
#         "/remotedata/U/DLR+kata_du/ALR DATA/Only_MBSA/Only_MBSA_pdfs",
#         "/remotedata/U/DLR+kata_du/ALR DATA/Only_MBSA/Only_MBSA_results"
#     )
# ]

# for (source_path,storage_path) in path_pairs:
#     source_root = Path(source_path)
#     for file_path in source_root.rglob("*.pdf"):        
#         print(f"\n🔍 Checking: {file_path.name}")
#         process_pdf_mode_file(file_path, storage_path,'a')


source_path='/remotedata/U/DLR+kata_du/ALR DATA/MBSE_MBSA_Specific_literature/Pdfs'
storage_path='/remotedata/U/DLR+kata_du/ALR DATA/MBSE_MBSA_Specific_literature/Analyzed_results'

source_root = Path(source_path)

for file_path in source_root.rglob("*.pdf"):        
    print(f"\n🔍 Checking: {file_path.name}")
    process_pdf_mode_file(file_path, storage_path,'a')



# file_path="/remotedata/U/DLR+kata_du/ALR DATA/MBSE_MBSA_Aviation/Specific_literature/Pdfs/Integrated_System_Design_and_Safety_Framework_for_Model-Based_Safety_Assessment.pdf"
# process_pdf_mode_file(file_path, storage_path,'a')
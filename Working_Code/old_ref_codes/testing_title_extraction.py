import os
import pandas as pd
import fitz  # PyMuPDF
import re

from PyPDF2 import PdfReader
from LLM_Call import*
from title_extracter import*

import pdfplumber
from colorama import init, Fore, Style  # pip install colorama
init(autoreset=True)

Sys_Prompt_Title_Analyzer="""
Role: You are a precision text-analysis assistant. Your sole task is to identify and return the official document title from two provided candidate strings.

Evaluation Criteria:
1. Publication Suitability: Select the string that is most appropriate as a formal title for a publication or official report.
2. Semantic Substance: Prioritize strings with descriptive content over generic or functional labels (e.g., "Annual Strategy" vs "Document 1").
3. Clean Reconstruction: If a string contains meaningful content but has poor formatting (e.g., erratic spacing or casing), reconstruct it into a clean, professional title format.
4. Specific Blacklist: If the identified title is "No Metadata Title", or if both strings are functional fragments (e.g., "Page 1", "Draft"), you MUST return: Title Not Found.

Decision Logic:
- If only one string is a potential title, return it.
- If both are potential titles, return the one best suited for formal publication.
- If the result would be "No Metadata Title", return: Title Not Found.

STRICT OUTPUT CONSTRAINTS:
- Return ONLY the raw text of the identified title.
- DO NOT include labels (e.g., "Title:", "Chosen:").
- DO NOT include quotes, explanations, or introductory text.
- DO NOT include any punctuation not part of the title itself.
- If no title is found, return ONLY the phrase: Title Not Found
""".strip()


def process_folder(folder_path):
    excel_name = "Title_Analysis.xlsx"
    excel_path = os.path.join(folder_path, excel_name)

    if os.path.exists(excel_path):
        df_existing = pd.read_excel(excel_path)
        processed_files = set(df_existing['File Name'].tolist())
        print(Fore.GREEN + f"Found existing log. {len(processed_files)} files already indexed.")
    else:
        df_existing = pd.DataFrame()
        processed_files = set()
        print(Fore.YELLOW + "Creating new tracking file.")

    new_data = []
    valid_extensions = '.pdf'

    for file_name in os.listdir(folder_path):
        if not file_name.lower().endswith(valid_extensions):
            continue
        if file_name in processed_files:
            continue

        file_path = os.path.join(folder_path, file_name)
        print(Fore.CYAN + f"Processing NEW file: {file_name}...")

        try:
            meta_title = get_title_metadata(file_path)
            Font_title = get_title_by_font_size(file_path)

            Prompt = f"""Title Strings:
                        - {meta_title}
                        - {Font_title}
                        """

            # log prompt
            print(Fore.MAGENTA + "LLM PROMPT:" + Style.RESET_ALL)
            print(Fore.MAGENTA + Prompt + Style.RESET_ALL)

            LLM_Choosen = Local_Model_call(Prompt, Sys_Prompt_Title_Analyzer)

            # log response
            print(Fore.BLUE + "LLM RESPONSE:" + Style.RESET_ALL)
            print(Fore.BLUE + str(LLM_Choosen) + Style.RESET_ALL)

            new_data.append({
                "File Name": file_name,
                "File Path": file_path,
                "Meta Title": meta_title,
                "Bigger Font Title": Font_title,
                "LLM Choosen": LLM_Choosen,
            })
        except Exception as e:
            print(Fore.RED + f"Error reading {file_name}: {e}")

    if new_data:
        df_new = pd.DataFrame(new_data)
        df_final = pd.concat([df_existing, df_new], ignore_index=True)
        df_final.to_excel(excel_path, index=False)
        print(Fore.GREEN + f"\nUpdate complete! Added {len(new_data)} new files to {excel_name}")
    else:
        print(Fore.YELLOW + "\nNo new files found to add.")


process_folder("/localdata/user/kata_du/Automated Literature Survey/downloads/Test_Folder")
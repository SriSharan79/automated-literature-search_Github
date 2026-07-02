import sys
import traceback
import difflib
import json
import os
import re # Import regex
from typing import List,Dict,Any, Optional
from colorama import Fore, Style, init
import pandas as pd
from datetime import datetime
import time
from itertools import chain, combinations,product
from alr.common.llm_utils import blabla_ask_llm,PromptTemplate
from alr.collection.collection_system_prompts import SYSTEM_PROMPT_ClASSIFIER,PROMPT_TEMPLATE_ClASSIFIER
# Initialize colorama
init(autoreset=True)

def Classification_of_Phrase(qa_prompt_template, Phrase):

    Key_Phrases=[]

    try:
        # Format the prompt using the LangChain PromptTemplate for the user message
        formatted_user_prompt = qa_prompt_template.format(data=Phrase)

        response_from_llm = blabla_ask_llm(formatted_user_prompt, SYSTEM_PROMPT_ClASSIFIER)

        # Decide how to extract the text based on the type/structure
        if isinstance(response_from_llm, str):
            # Already a plain string
            llm_response_only = response_from_llm

        elif isinstance(response_from_llm, dict):
            # Single dict, try common keys
            if 'generated_text' in response_from_llm:
                llm_response_only = response_from_llm['generated_text']
            elif 'text' in response_from_llm:
                llm_response_only = response_from_llm['text']
            else:
                raise ValueError(f"Dict response does not contain 'generated_text' or 'text' keys: {response_from_llm}")

        elif isinstance(response_from_llm, list):
            # List: assume list of dicts or strings
            if len(response_from_llm) == 0:
                raise ValueError("LLM response list is empty")

            first_item = response_from_llm[0]

            if isinstance(first_item, dict):
                if 'generated_text' in first_item:
                    llm_response_only = first_item['generated_text']
                elif 'text' in first_item:
                    llm_response_only = first_item['text']
                else:
                    raise ValueError(f"Dict in list does not contain 'generated_text' or 'text' keys: {first_item}")
            elif isinstance(first_item, str):
                # Join all strings if needed, or just use the first one
                llm_response_only = "\n".join(response_from_llm)
            else:
                raise TypeError(f"Unsupported item type in LLM response list: {type(first_item)}")

        else:
            raise TypeError(f"Unsupported LLM response type: {type(response_from_llm)}")

        # ---- your existing cleaning + logging code ----

        # Clean up any special tokens that the tokenizer might add to the response
        llm_response_only = llm_response_only.replace("<|eot_id|>", "").strip()
        llm_response_only = llm_response_only.replace("<|start_header_id|>assistant<|end_header_id|>", "").strip()

        if not llm_response_only.strip():
            print(Fore.RED + "Warning: LLM response is empty after cleaning. This might indicate poor generation." + Style.RESET_ALL)
            raw_llm_response_text = ""
        else:
            raw_llm_response_text = llm_response_only

        print(Fore.CYAN + "\n--- RAW LLM RESPONSE START (simplified extraction) ---" + Style.RESET_ALL)
        print(Fore.CYAN + raw_llm_response_text + Style.RESET_ALL)
        print(Fore.CYAN + "--- RAW LLM RESPONSE END ---\n" + Style.RESET_ALL)

        # Split the input text by newlines to separate phrases
        phrases = raw_llm_response_text.strip().split("\n")

        return raw_llm_response_text
   

    except Exception as e:
        print(Fore.RED + f"An unexpected error occurred during LLM call or pre-parsing: {e}" + Style.RESET_ALL)
        print(Fore.RED + "Returning empty response due to error." + Style.RESET_ALL)
        
        traceback.print_exc()       
        return ""

def Phrase_Processing(Phrase):
    
    prompt = PromptTemplate(
    template=PROMPT_TEMPLATE_ClASSIFIER,
    input_variables=["data"]
    # partial_variables={"format_instructions": parser.get_format_instructions()}, # No longer needed
    )

    return (Classification_of_Phrase(prompt,Phrase))

def Classify_excel_data(
    file_path: str,
    column_name: str,
    output_column_name: str = 'Classification'
) -> Optional[pd.DataFrame]:
    """
    Identifies keywords from a list within the text of a specified column in an Excel file
    and adds a new column listing the found keywords.

    Args:
        file_path: The full path to the Excel file (e.g., 'data.xlsx').
        column_name: The name of the column to search within (e.g., 'Description').
        keywords: A list of strings to search for (e.g., ['apple', 'orange', 'banana']).
        output_column_name: The name for the new column with the results.

    Returns:
        The modified pandas DataFrame, or None if the file/column is not found.
    """
    try:
        # 1. Read the Excel file into a pandas DataFrame
        df = pd.read_excel(file_path)

    except FileNotFoundError:
        print(f"Error: File not found at path: {file_path}")
        return None
    except Exception as e:
        print(f"An error occurred while reading the Excel file: {e}")
        traceback.print_exc()       
        return None

    if column_name not in df.columns:
        print(f"Error: Column '{column_name}' not found in the Excel file.")
        return None

    # 2. Define the function to find and list keywords in a single cell's text
    def Classify_Name(text: str) -> str:

           return Phrase_Processing(text)

    # 3. Apply the function to the specified column and create the new column
    df[output_column_name] = df[column_name].apply(Classify_Name)

    # 4. Reorder the columns to place the new column right next to the original
    # Find the index of the original column
    col_index = df.columns.get_loc(column_name)
    
    # Get the list of columns
    cols = df.columns.tolist()
    
    # Move the new column to the desired position
    # It removes the column from the end, then inserts it after the original column
    cols.remove(output_column_name)
    cols.insert(col_index + 1, output_column_name)
    
    # Apply the new column order
    df = df[cols]

    # 5. Save the modified DataFrame back to a new Excel file (or overwrite the original)
    output_file_path = file_path.replace(".xlsx", "_Classified.xlsx")
    df.to_excel(output_file_path, index=False)
    
    print(f"\n✅ Success! New file saved to: {output_file_path}")
    print(f"Column '{output_column_name}' has been added next to '{column_name}'.")
    
    return df
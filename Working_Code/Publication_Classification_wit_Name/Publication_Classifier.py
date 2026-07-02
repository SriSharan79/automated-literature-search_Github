from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
import torch
from colorama import Fore, Style, init
import pandas as pd
from typing import List, Optional

import json
import os
import re # Import regex
from typing import List,Dict,Any
# LangChain Imports
from langchain_core.prompts import PromptTemplate
from Search_KeyWord_Formulator import*
from Duplication_Checker import*

from LLM_Key_Phrase_Formulation import*

# LLMs is not agood classifer based on the results obtained here

SYSTEM_PROMPT_ClASSIFIER="""
Our sole function is to act as an **Aviation AI Name Classifier**. You must analyze an input string (a paper name/title) and determine if its core focus is on the application or study of AI technologies *specifically within the context of aircraft system safety and certification*.

---

### **Core AI Concepts (Trigger Keywords)**

The name must primarily relate to one or more of the following concepts:

* **'Machine Learning (ML)'**
* **'Deep Learning (DL)'**
* **'Generative AI'**
* **'Large Language Models (LLMs)'**
* **'Neural Networks'**
* **'Reinforcement Learning (RL)'**
* **'Natural Language Processing (NLP)'**
* **'Explainable AI (XAI)'**
* **'AI Agents'**

### **Classification Rules**

1. **Positive Classification (`TRUE`):** The Name must explicitly mention or clearly imply the application, use, development, or study of any of the **Core AI Concepts** above, *within the aviation system safety or certification domain*.
    * **Examples:** *Validating Deep Learning Models for Flight Control Systems*, *Using LLMs to Summarize Airworthiness Directives*, *Certification Challenges for Reinforcement Learning in Avionics*.

2. **Negative Classification (`FALSE`):** If the Name focuses on a subject *without* explicitly linking it to one of the **Core AI Concepts** *or* if the context is clearly outside of aviation safety/certification. The link to an AI concept must be clear from the title itself.
    * **Examples:** *Advanced Methods for Aircraft Structural Fatigue Analysis*, *Optimizing Scheduling Algorithms for Air Traffic Management*, *New Algorithms for Fast Fourier Transforms*.

---

### **Output Format**

You **must** only output one of the following two strings. **NO other text, explanation, or punctuation is permitted.**

* **`TRUE`** (If the classification is positive)
* **`FALSE`** (If the classification is negative)
"""
# --- Define the prompt template (user part) ---
PROMPT_TEMPLATE_ClASSIFIER = """
Context:
{data}

"""

hf_pipline=hf_pipeline_with_Lamma3B()

def Classification_of_Phrase(qa_prompt_template,hf_pipeline, Phrase):

    Key_Phrases=[]

    try:
        # Format the prompt using the LangChain PromptTemplate for the user message
        formatted_user_prompt = qa_prompt_template.format(data=Phrase)

        # Create messages list for the LLM call, including the new strong system prompt
        messages_for_llm_qa = [
            {"role": "system", "content": SYSTEM_PROMPT_ClASSIFIER},
            {"role": "user", "content": formatted_user_prompt},
        ]

        # Generate chat prompt using the tokenizer's apply_chat_template
        chat_prompt_for_qa = hf_pipeline.tokenizer.apply_chat_template(
            messages_for_llm_qa,
            tokenize=False,
            add_generation_prompt=True # Add assistant's turn
        )

        # Call the local model (hf_pipeline)
        response_from_llm = hf_pipeline(chat_prompt_for_qa)
        llm_response_only = response_from_llm[0]['generated_text']

        # Clean up any special tokens that the tokenizer might add to the response
        llm_response_only = llm_response_only.replace("<|eot_id|>", "").strip()
        llm_response_only = llm_response_only.replace("<|start_header_id|>assistant<|end_header_id|>", "").strip()

        # No longer trying to force JSON braces; the new format is text-based.

        if not llm_response_only.strip():
            print(Fore.RED + "Warning: LLM response is empty after cleaning. This might indicate poor generation." + Style.RESET_ALL)
            raw_llm_response_text = ""
        else:
            raw_llm_response_text = llm_response_only


        print(Fore.CYAN + "\n--- RAW LLM RESPONSE START (simplified extraction) ---" + Style.RESET_ALL)
        print(Fore.CYAN + raw_llm_response_text + Style.RESET_ALL)
        print(Fore.CYAN + "--- RAW LLM RESPONSE END ---\n" + Style.RESET_ALL)

        # # Split the input text by newlines to separate phrases
        # phrases = raw_llm_response_text.strip().split("\n")

        # # Remove any extra quotes around the phrases
        # cleaned_phrases = [phrase.strip('"') for phrase in phrases]

        # for phrase in cleaned_phrases:
        #     Key_Phrases.append({
        #         'Phrase':Phrase,
        #           'Classification':
        #     })

        return raw_llm_response_text
   

    except Exception as e:
        print(Fore.RED + f"An unexpected error occurred during LLM call or pre-parsing: {e}" + Style.RESET_ALL)
        print(Fore.RED + "Returning empty response due to error." + Style.RESET_ALL)
        return "" 

def Phrase_Processing(Phrase):

    prompt = PromptTemplate(
    template=PROMPT_TEMPLATE_ClASSIFIER,
    input_variables=["data"]
    # partial_variables={"format_instructions": parser.get_format_instructions()}, # No longer needed
    )

    return (Classification_of_Phrase(prompt,hf_pipline,Phrase))

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
    output_file_path = file_path.replace(".xlsx", "_Classified.xlsx").replace(".xls", "_Classified.xls")
    df.to_excel(output_file_path, index=False)
    
    print(f"\n✅ Success! New file saved to: {output_file_path}")
    print(f"Column '{output_column_name}' has been added next to '{column_name}'.")
    
    return df

excel_file = 'Output Excel Files/LLM_Safety_Certification_Publications.xlsx' 

# 2. Set the column to search in
target_column = 'Publication Name' 

modified_df = Classify_excel_data(
    file_path=excel_file,
    column_name=target_column
)
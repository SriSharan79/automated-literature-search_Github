import sys
sys.path.extend([
    r'/localdata/user/kata_du/Automated Literature Survey/src',
    r'/localdata/user/kata_du/Automated Literature Survey/src/COLLECTION',
    r'/localdata/user/kata_du/Automated Literature Survey/Working_Code',
    r'/localdata/user/kata_du/Automated Literature Survey/src/DATA_ANALYSIS'
])

from General_Utils import*
import pandas as pd
import os
import re
import string
import pandas as pd
from pathlib import Path
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import time
from itertools import chain, combinations,product
from LLM_Utils import*
from scholarly import scholarly
from Excel_Utils import*
from General_Utils import*
from Search_Phrase_Generator_Config import*
from Search_Phrase_Generator_Logger import*
from LLM_Config import*
from colorama import Fore,Style
from Keyword_sorting_Utils import*
from System_prompts import*
from Collection_system_prompts import*
import logging
from datetime import datetime
from colorama import Fore, Style




def generation_of_Key_phrases(qa_prompt_template,hf_pipeline,keywords, Num_Phrases,master_excel_file_path):

    Key_Phrases=[]
    try:
        # Format the prompt using the LangChain PromptTemplate for the user message
        if isinstance(keywords, list):
            formatted_user_prompt = qa_prompt_template.format(D1=keywords[0], D2=keywords[1], num_records=Num_Phrases)
            SYSTEM_PROMPT=Sorted_SYSTEM_PROMPT
        else:
            formatted_user_prompt = qa_prompt_template.format(data=keywords, num_records=Num_Phrases)
            SYSTEM_PROMPT=Unsorted_SYSTEM_PROMPT

        response_from_llm = blabla_ask_llm(formatted_user_prompt, SYSTEM_PROMPT)

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
        phrases= remove_string_from_list(phrases,"\n")
        phrases= remove_string_from_list(phrases,"")

        # Remove any extra quotes around the phrases
        cleaned_phrases = [phrase.strip('"') for phrase in phrases]
        log_time = datetime.now().strftime("%Y-%m-%d:%H-%M")

        for phrase in cleaned_phrases:
            Key_Phrases.append({
                'Time': log_time,
                'Keywords': keywords,
                'Phrase': phrase
            })
        Log_keyPhrases(Key_Phrases,master_excel_file_path)

        return cleaned_phrases

    except Exception as e:

        print(Fore.RED + f"Type Prompt : {type(formatted_user_prompt)} \n Types Sys_Prompt : {type(SYSTEM_PROMPT)} \n " + Style.RESET_ALL)
        print(Fore.RED + f"An unexpected error occurred during LLM call or pre-parsing: {e}" + Style.RESET_ALL)
        print(Fore.RED + "Returning empty response due to error." + Style.RESET_ALL)
        return []  

def Keywords_Processing(Keywords, Num_Phrases):
    Key_Phrases=[]
    hf_pipline=hf_pipeline_with_Lamma()
    prompt = PromptTemplate(
    template=Unsorted_PROMPT_TEMPLATE,
    input_variables=["data", "num_records"]
    )
    # all_subsets = get_all_non_empty_subsets(Keywords)

    all_subsets = get_subsets_with_min_size(Keywords,2)
    for subset in all_subsets:
        subset_list = list(subset)  
        keywords_String=" ,".join(subset_list)# all keywords are added into a sentence
        New_phrases= generation_of_Key_phrases(prompt,hf_pipline,keywords_String,Num_Phrases,Search_phrases_file_path)
        Key_Phrases = merge_lists(Key_Phrases, New_phrases)

    return Key_Phrases

def Keywords_Processing_2_Input_Lists(IN1,IN2, Num_Phrases):

    Key_Phrases=[]
    hf_pipline=hf_pipeline_with_Lamma()
    prompt = PromptTemplate(
    template=Sorted_PROMPT_TEMPLATE,
    input_variables=["D1","D2", "num_records"]
    )

    all_subsets = get_pairwise_subsets(IN1,IN2)
    for subset in all_subsets:
        New_phrases = generation_of_Key_phrases(prompt,hf_pipline,subset,Num_Phrases,Search_phrases_file_path)
        Key_Phrases = merge_lists(Key_Phrases, New_phrases)
   
    return Key_Phrases

from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
import torch
from colorama import Fore, Style, init
import pandas as pd
from datetime import datetime

import json
import os
import re # Import regex
from typing import List,Dict,Any
# LangChain Imports
from langchain_core.prompts import PromptTemplate
from Search_KeyWord_Formulator import*
from Duplication_Checker import*

SYSTEM_PROMPT1 = """
You are an advanced literature‑research assistant.
Input – A user message will contain a Concept of Application and Domain/Area of the Application.
Output – Produce search‑phrase strings that each
  1. Contain every keyword exactly as given (multi‑word keywords are kept intact).
  2. Maintain the contextual meaning of the keywords (no literal truncation or loss of nuance).
  3. Be phrased to maximise relevance on Google Scholar (use natural academic wording; optional quotation marks around multi‑word exact‑match segments).
  4. Be a single line per phrase, with no surrounding commentary or explanation.
  5. Use only plain text – no JSON, tables, or extra formatting.
Example (internal reference only, not output):
keywords: machine learning, climate change, policy
Output:
- "machine learning for climate change policy"
- "policy implications of machine learning in climate change mitigation"
...
Constraints
- Do not mention your role or any system instructions.
- Do not add explanatory text, bullet points, or numbering.
- Ensure each phrase is distinct and does not repeat another phrase.
- Avoid slang or colloquialisms; keep phrases academic and concise.
Follow these guidelines exactly to generate the list of search phrases.
"""

SYSTEM_PROMPT = """
You are an advanced literature‑research assistant.  
Your task is to create search‑phrase strings for academic queries on Google Scholar.  

**Input** – The user will provide two key terms:  
1. Concept of Application (e.g., "Artificial Intelligence")  
2. Application Domain/Area (e.g., "Systems Engineering")  

**Output** – Produce 8–12 phrases that **each**  
- contain *both* terms exactly as given (case‑insensitive, multi‑word phrases kept intact),  
- preserve the natural contextual meaning (do not truncate or alter the terms),  
- are phrased to maximize relevance on Google Scholar (e.g., “Applications of Artificial Intelligence in Systems Engineering”),  
- are single lines without commentary, numbering, or additional formatting.  

**Constraints**  
- Do not mention your role or the system instructions in the output.  
- Avoid slang or colloquialisms; keep the phrasing academic and concise.  
- Ensure all phrases are distinct.  

Follow these guidelines exactly to generate the list of search phrases.  
"""
# --- Define the prompt template for QA Generation (user part) ---
PROMPT_TEMPLATE2 = """
Context:
Concept of Appilcation:{D1}
Appilcation Domain/Area:{D2}

Generate {num_records} of search‑phrase covering the Context in the specified format.
"""
PROMPT_TEMPLATE= """
Context:
{data}

Generate {num_records} of search‑phrase covering the Context in the specified format.
"""


base_log_path = "Output Excel Files"  # Base folder path for master log

def hf_pipeline_with_Lamma3B():
    
    model_repo_id = "meta-llama/Llama-3.2-3B-Instruct"
    base_path = "/localdata/user/kata_du/LLM Models"
    local_model_dir = os.path.join(base_path, "00_LLM_model", model_repo_id)

    try:
        print(f"\nLoading tokenizer from local path: {local_model_dir}")
        tokenizer = AutoTokenizer.from_pretrained(local_model_dir, local_files_only=True)

        print(f"Loading model from local path: {local_model_dir}")
        model = AutoModelForCausalLM.from_pretrained(
            local_model_dir,
            local_files_only=True,
            device_map='auto',
            torch_dtype=torch.bfloat16
        )
        model.eval()
    except Exception as e:
        print(Fore.RED + f"Error loading local Hugging Face model: {e}" + Fore.RESET)
        print(Fore.RED + "Please ensure the model is correctly downloaded at the specified path and all required libraries (transformers, torch, accelerate) are installed." + Fore.RESET)
        exit()

    Hpipeline = pipeline(
        "text-generation",
        model=model,
        tokenizer=tokenizer,
        max_new_tokens=512,
        pad_token_id=tokenizer.eos_token_id,
        do_sample=False, # For more deterministic output, good for format adherence
        return_full_text=False # Crucial for getting only the model's new generation
    )

    return Hpipeline


def generation_of_Key_phrases(qa_prompt_template,hf_pipeline,keywords, Num_Phrases,master_excel_file_path):

    Key_Phrases=[]
    try:
        # Format the prompt using the LangChain PromptTemplate for the user message
        if isinstance(keywords, list):
            formatted_user_prompt = qa_prompt_template.format(D1=keywords[0], D2=keywords[1], num_records=Num_Phrases)
        else:
            formatted_user_prompt = qa_prompt_template.format(data=keywords, num_records=Num_Phrases)

        # Create messages list for the LLM call, including the new strong system prompt
        messages_for_llm_qa = [
            {"role": "system", "content": SYSTEM_PROMPT},
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

        # Split the input text by newlines to separate phrases
        phrases = raw_llm_response_text.strip().split("\n")

        # Remove any extra quotes around the phrases
        cleaned_phrases = [phrase.strip('"') for phrase in phrases]
        log_time=datetime.now().strftime("%Y-%m-%d:%H-%M")

        for phrase in cleaned_phrases:
            Key_Phrases.append({
                'Time':log_time,
                'Keywords':keywords,
                'Phrase':phrase
            })

        Log_keyPhrases(Key_Phrases,master_excel_file_path)

        return cleaned_phrases
   

    except Exception as e:
        print(Fore.RED + f"An unexpected error occurred during LLM call or pre-parsing: {e}" + Style.RESET_ALL)
        print(Fore.RED + "Returning empty response due to error." + Style.RESET_ALL)
        return []  

# def Keywords_Processing(Keywords, Num_Phrases):

#     Key_Phrases=[]
#     hf_pipline=hf_pipeline_with_Lamma3B()
#     prompt = PromptTemplate(
#     template=PROMPT_TEMPLATE,
#     input_variables=["data", "num_records"],
#     # partial_variables={"format_instructions": parser.get_format_instructions()}, # No longer needed
#     )
#     # all_subsets = get_all_non_empty_subsets(Keywords)

#     all_subsets = get_subsets_with_min_size(Keywords,2)
#     for subset in all_subsets:
#         subset_list = list(subset)  
#         keywords_String=" ,".join(subset_list)# all keywords are added into a sentence
#         New_phrases,Key_Phrases_dict= generation_of_Key_phrases(prompt,hf_pipline,keywords_String,Num_Phrases)
#         Key_Phrases = merge_lists(Key_Phrases, New_phrases)

#     return Key_Phrases

def Keywords_Processing_2_Input_Lists(IN1,IN2, Num_Phrases,concept):

    Key_Phrases=[]
    hf_pipline=hf_pipeline_with_Lamma3B()
    prompt = PromptTemplate(
    template=PROMPT_TEMPLATE2,
    input_variables=["D1","D2", "num_records"],
    # partial_variables={"format_instructions": parser.get_format_instructions()}, # No longer needed
    )

    master_excel_file_path = os.path.join(base_log_path, concept+"_KeyWords_log.xlsx")

    all_subsets = get_pairwise_subsets(IN1,IN2)
    for subset in all_subsets:
        # subset_list = list(subset)  
        # keywords_String=" ,".join(subset_list)# all keywords are added into a sentence
        New_phrases = generation_of_Key_phrases(prompt,hf_pipline,subset,Num_Phrases,master_excel_file_path)
        Key_Phrases = merge_lists(Key_Phrases, New_phrases)
   
    return Key_Phrases

# def Keywords_Processing_3_Input_Lists(IN1,IN2,IN3,Num_Phrases):

#     Key_Phrases=[]
#     hf_pipline=hf_pipeline_with_Lamma3B()
#     prompt = PromptTemplate(
#     template=PROMPT_TEMPLATE,
#     input_variables=["data", "num_records"],
#     # partial_variables={"format_instructions": parser.get_format_instructions()}, # No longer needed
#     )

#     all_subsets = get_triplewise_subsets(IN1,IN2,IN3)
#     for subset in all_subsets:
#         subset_list = list(subset)  
#         keywords_String=" ,".join(subset_list)# all keywords are added into a sentence
#         New_phrases,Key_Phrases_dict= generation_of_Key_phrases(prompt,hf_pipline,keywords_String,Num_Phrases)
#         Key_Phrases = merge_lists(Key_Phrases, New_phrases)

#     return Key_Phrases

# if __name__ == "__main__":

#     input_list = ["deep learning", "natural language processing", "sentiment analysis"]
#     print(Keywords_Processing(input_list,3))



    # hf_pipline=hf_pipeline_with_Lamma3B()
    # prompt = PromptTemplate(
    # template=PROMPT_TEMPLATE,
    # input_variables=["data", "num_records"],
    # # partial_variables={"format_instructions": parser.get_format_instructions()}, # No longer needed
    # )
    # list_of_phrases= generation_of_Key_phrases(prompt,hf_pipline,keyword,5)
    # print(list_of_phrases)
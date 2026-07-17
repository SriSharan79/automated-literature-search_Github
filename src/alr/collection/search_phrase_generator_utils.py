import sys
import traceback
from typing import List

from alr.collection.keyword_sorting_Utils import get_subsets_of_size, get_subsets_with_min_size
from alr.common.llm_utils import blabla_ask_llm, llm_call
from alr.collection.collection_system_prompts import Serach_phrase_System_Prompt
from alr.common.excel_utils import add_column_sum, extract_column, sum_columns_ending_with_to_target
from alr.common.general_utils import merge_lists, print_with_separator
from alr.collection.search_phrase_generator_logger import Log_keyPhrases, aggregate_and_update_excel, log_generated_list_file

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
from scholarly import scholarly
from colorama import Fore,Style
from datetime import datetime
from colorama import Fore, Style
import threading
import time


def find_keywords_in_phrase(text: str,
    keywords: List[str]) -> str:
    if pd.isna(text):
        return ""  # Handle NaN/empty cells
    
    # Ensure the text is a string and convert to lowercase for case-insensitive matching
    text_lower = str(text).lower()
    
    found_keywords = []
    for keyword in keywords:
        # Check if the keyword is in the text (case-insensitive)
        if keyword.lower() in text_lower:
            found_keywords.append(keyword)
    
    # Join the found keywords with a comma and space (e.g., "apple, banana")
    return ", ".join(found_keywords)

import time
import threading
from scholarly import scholarly

def breakout_function():
    print(f"Timeout reached!")

# Timeout wrapper function to handle timeouts
def timeout_function(func, args=(), timeout=60):

    result = []
    
    def worker():
        nonlocal result
        try:
            search_results = func(*args)  # Unpack args and call the function
            result = list(search_results)  # Convert the iterator to a list
        except Exception as e:
            print(f"Error during search: {e}")
            traceback.print_exc()       
            
    
    timer = threading.Timer(timeout, breakout_function)
    timer.start()
    # Start the search in a separate thread
    search_thread = threading.Thread(target=worker)
    search_thread.start()
    search_thread.join(timeout)

    if search_thread.is_alive():
        # If the scholarly function is still running after 30 seconds, stop it
        print("Run scholarly timed out!")
        # Here you would need to stop the `run_scholarly` process, but Python threads can't be forcefully killed.
        # However, you could use some kind of cooperative termination strategy, such as checking a shared variable
        # inside the `run_scholarly` function to know when it should terminate.
    else:
        # If run_scholarly finishes in time, cancel the timer
        timer.cancel()
        
    if not result:
        print(f"No results found in {timeout} seconds.")
        return []  # Return empty list if no results
    return result

# Google Scholar data collection
def scrape_scholar_data(search_query, Num_Results, Total_keywords):
    """
    Searches Google Scholar for publications matching the combined keywords 
    and returns the data as a list of dictionaries.
    """
    
    print(f"Searching Google Scholar for: '{search_query}'")
    
    publications_data = []  # Stores the raw scraped data

    # Attempt to get search results with timeout
    # search_results = timeout_function(scholarly.search_pubs, (search_query,), timeout=60)
    search_results= scholarly.search_pubs(search_query)
    
    if not search_results:
        return publications_data  # Return empty list if no results within the timeout
    
    MAX_RESULTS = Num_Results
    
    for i, pub in enumerate(search_results):
        time.sleep(10)
        if i >= MAX_RESULTS:
            print(f"\nStopped after processing {MAX_RESULTS} results.")
            break

        try:
            # Safely extract the required information
            title = pub.get('bib', {}).get('title', 'N/A')
            authors = ', '.join(pub.get('bib', {}).get('author', ['N/A']))
            pub_year = pub.get('bib', {}).get('pub_year', 'N/A')
            venue = pub.get('bib', {}).get('venue', 'N/A')

            # Simplified link extraction
            link_pub = pub.get('eprint_url') or pub.get('pub_url') or pub.get('doi') or pub.get('url') or 'N/A'

            abstract = pub.get('bib', {}).get('abstract', 'N/A')
            keywords_in_title = find_keywords_in_phrase(title, Total_keywords)

            # Append data directly with all required keys (including the fixes)
            publications_data.append({
                'Occurrence': 1,  # Default for new entry
                'Search Phrase': search_query,  # Pass the search phrase
                'Publication Name': title,
                'Keywords in Title': keywords_in_title,
                'Abstract': abstract,
                'Link': link_pub,
                'Organization': venue,
                'Publication Year': pub_year,
                'Authors': authors  # Corrected spelling
            })
            print(f"  Extracted result {i+1}: {title}...")

        except Exception as e:
            print(f"  Error processing publication: {e}")
            traceback.print_exc()       

        # Sleep to avoid rate-limiting
        time.sleep(1)
        
    return publications_data  # Return the collected list


def generation_of_Key_phrases_with_scope(formatted_user_prompt, phrase_excel_file, Keywords,Total_keywords,llm):

    Key_Phrases = []
    try:
        # Debugging: Print the formatted_user_prompt before passing it to LLM
        # print(f"Formatted User Prompt: {formatted_user_prompt}")
        
        # Call to LLM (mocked for now)
        response_from_llm = llm_call(formatted_user_prompt, Serach_phrase_System_Prompt,llm)

        # Debugging: Print the LLM response type and content
        # print(f"Response from LLM (type: {type(response_from_llm)}): {response_from_llm}")

        # Decide how to extract the text based on the type/structure of the response
        if isinstance(response_from_llm, str):
            llm_response_only = response_from_llm
        elif isinstance(response_from_llm, dict):
            if 'generated_text' in response_from_llm:
                llm_response_only = response_from_llm['generated_text']
            elif 'text' in response_from_llm:
                llm_response_only = response_from_llm['text']
            else:
                raise ValueError(f"Dict response does not contain 'generated_text' or 'text' keys: {response_from_llm}")
        elif isinstance(response_from_llm, list):
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
                llm_response_only = "\n".join(response_from_llm)
            else:
                raise TypeError(f"Unsupported item type in LLM response list: {type(first_item)}")
        else:
            raise TypeError(f"Unsupported LLM response type: {type(response_from_llm)}")

        # Debugging: Print the cleaned response
        # print(f"Cleaned LLM response: {llm_response_only}")

        # Clean up any special tokens that the tokenizer might add to the response
        llm_response_only = llm_response_only.replace("<|eot_id|>", "").strip()
        llm_response_only = llm_response_only.replace("<|start_header_id|> <|end_header_id|>", "").strip()

        if not llm_response_only.strip():
            print("Warning: LLM response is empty after cleaning.")
            raw_llm_response_text = ""
        else:
            raw_llm_response_text = llm_response_only

        # Debugging: Print raw LLM response text
        # print(f"Raw LLM response text: {raw_llm_response_text}")

        def remove_string_from_list(strings, target):
            return [string for string in strings if string != target]

        # Split the input text by newlines to separate phrases
        phrases = raw_llm_response_text.strip().split("\n")
        phrases= remove_string_from_list(phrases,"\n")
        phrases= remove_string_from_list(phrases,"")

        # Remove any extra quotes around the phrases
        cleaned_phrases = [phrase.strip('"') for phrase in phrases]

        # Debugging: Print cleaned phrases
        # print(f"Cleaned phrases: {cleaned_phrases}")

        log_time = datetime.now().strftime("%Y-%m-%d:%H-%M")

        # Loop through cleaned phrases and append to Key_Phrases
        for phrase in cleaned_phrases:

            kW_in_ph=find_keywords_in_phrase(phrase,Total_keywords)
            Key_Phrases.append({
                'Time': log_time,
                'Keywords': Keywords,  # Ensure Keywords are passed in the function
                'Phrase': phrase,
                'Keywords_in_phrase': kW_in_ph
            })

        # Log the key phrases
        Log_keyPhrases(Key_Phrases, phrase_excel_file)

        return cleaned_phrases

    except Exception as e:
        # Detailed error logging
        print(f"Error in generation_of_Key_phrases_with_scope: {str(e)}")
        print(f"Formatted User Prompt: {formatted_user_prompt}")
        print(f"Master Excel File Path: {phrase_excel_file}")
        return []

def Keywords_Processing_with_scope(CM):
    scope= CM.Research_Scope
    Keywords= CM.Keyword_list
    llm= CM.llm_service
    Key_Phrases = []
    phrase_excel_file= Path(CM.search_phrase_list_excel)

    log_excel_file= Path(CM.search_phrase_log_path)

    try:
        # print(f"\nScope: {scope}\n")
        # print(f"\nKeywords: {Keywords}\n")

        all_subsets=None

        if len(Keywords)>=5:
            all_subsets= get_subsets_of_size(Keywords, 2)
        else:
        # Get all subsets of keywords with a minimum size of 2
            all_subsets = get_subsets_with_min_size(Keywords, 2)

        # Debugging: Print the generated subsets
        print(f"number of subsets:{ len(all_subsets)}")
        
        for i,subset in enumerate(all_subsets):
            subset_list = list(subset)
            Serach_phrase_User_prompt = f"\n The scope of the research is: {scope}\n The keywords provided are: {subset_list}"
            
            # Debugging: Print the generated prompt
            print(f"{i}th Generated Search Prompt: {Serach_phrase_User_prompt}")
            
            New_phrases = generation_of_Key_phrases_with_scope(Serach_phrase_User_prompt, phrase_excel_file, subset,Keywords,llm)
            Key_Phrases = merge_lists(Key_Phrases, New_phrases)
        
        # Debugging: Print the final key phrases
        # print(f"Final Key Phrases: {Key_Phrases}")
        total_phrases= extract_column(phrase_excel_file,'Phrase')  
        CM.update_Search_phrase_list(total_phrases)
        rank_to_available_data(CM)
        log_generated_list_file(phrase_excel_file,len(total_phrases),log_excel_file,CM)

        print(f"\n Final Key Phrases are updated:\n stored in {phrase_excel_file}\n logged in {log_excel_file}")

        return CM

    except Exception as e:
        print(f"Error in Keywords_Processing_with_scope: {str(e)}")        
        traceback.print_exc()       
        return []

def rank_to_available_data(CM): 
    scope= CM.Research_Scope
    Keywords= CM.Keyword_list
    phrase_excel_file= Path(CM.search_phrase_list_excel)    
    rank_search_phrases(scope,'Phrase',phrase_excel_file,'RS_Similarity_Score','RS_Rank')
    rank_search_phrases(CM.Research_Area,'Phrase',phrase_excel_file,'RA_Similarity_Score','RA_Rank')
    rank_search_phrases(CM.Research_Question,'Phrase',phrase_excel_file,'RQ_Similarity_Score','RQ_Rank')
    add_column_sum(phrase_excel_file,'RA_Rank','RQ_Rank','RA+RQ_Rank')
    if isinstance(Keywords,list):
        for idx, item in enumerate(Keywords):            
            rank_search_phrases(item,'Phrase',phrase_excel_file,f'{item}_Similarity_Score',f'{item}_Rank')
    
    sum_columns_ending_with_to_target(phrase_excel_file,'_Rank')           
    

def preprocess(text):
    """Safe preprocess: handles NaN, float, None, empty."""
    if pd.isna(text) or text is None or text == '':
        return ''
    # Convert to string safely
    text = str(text).strip()
    if not text:
        return ''
    text = text.lower()
    text = re.sub(f'[{re.escape(string.punctuation)}]', ' ', text)
    return ' '.join(text.split())



def rank_search_phrases(scope, column_name, excel_file, score_column_name='Similarity_Score', rank_column_name='Rank', top_k=10, threshold=0.1):
    query = scope
    dataset = extract_column(excel_file, column_name)
    
    # Preprocess & compute scores (same valid logic)
    valid_mask = [len(preprocess(s)) > 0 for s in dataset]
    valid_dataset = [s for s, m in zip(dataset, valid_mask) if m]
    
    if not valid_dataset:
        raise ValueError("No valid data.")
    
    valid_proc = [preprocess(s) for s in valid_dataset]
    query_proc = preprocess(query)
    
    vectorizer = TfidfVectorizer(stop_words='english', min_df=1, max_features=5000, ngram_range=(1, 2))
    tfidf_matrix = vectorizer.fit_transform(valid_proc)
    query_vec = vectorizer.transform([query_proc])
    valid_scores = cosine_similarity(query_vec, tfidf_matrix).flatten()
    
    # Map scores to full dataset
    scores = np.zeros(len(dataset))
    valid_idx = 0
    for i, is_valid in enumerate(valid_mask):
        if is_valid:
            scores[i] = valid_scores[valid_idx]
            valid_idx += 1
    
    # FIXED: Create ranks in ORIGINAL order, then rank by score
    temp_df = pd.DataFrame({'score': scores})
    temp_df = temp_df.sort_values('score', ascending=False).reset_index(drop=True)
    temp_df['rank'] = range(1, len(temp_df) + 1)  # 1=best, sequential
    
    # Map ranks back to original positions using stable sort indices
    original_ranks = np.full(len(dataset), len(dataset) + 1)  # Default low rank
    sorted_indices = np.argsort(-scores)  # Descending score indices
    for rank, orig_idx in enumerate(sorted_indices, 1):
        original_ranks[orig_idx] = rank
    
    # Write to Excel
    df_excel = pd.read_excel(excel_file)
    for col in [score_column_name, rank_column_name]:
        if col not in df_excel.columns:
            df_excel[col] = 0.0 if 'Score' in col else len(dataset) + 1
    
    df_excel[score_column_name] = scores
    df_excel[rank_column_name] = original_ranks
    df_excel.to_excel(excel_file, index=False)
    
    # print(f"✅ Scores & Ranks written. Rank 1 = highest score!")
    
    # Output ranked DataFrame
    df_ranked = pd.DataFrame({
        'original_index': range(len(dataset)),
        'sentence': dataset,
        'score': scores,
        'rank': original_ranks
    }).sort_values('score', ascending=False).reset_index(drop=True)
    
    matches = df_ranked[df_ranked['score'] >= threshold].head(top_k)
    # print("Top Matches (verified score-descending):")
    # print(matches[['rank', 'score', 'sentence']].head())
    
    return matches

def run_scholarly(Input_Phrases,CM, Num_Search_Results, progress_callback=None):
    """
    Loops through the Input_Phrases, scrapes data for each phrase, and updates the Excel file.
    If no publication results are found, it breaks the loop and returns an empty list.
    ``progress_callback(done, total, phrase)`` is called before each phrase is searched.
    """

    print_with_separator("DebugLog",'/')

    keywords_list=CM.Keyword_list

    PUB_EXCEL_FILE_PATH = Path(CM.publications_list_excel)

    publication_results = []  # Store the final list of publications

    for i, Phrase in enumerate(Input_Phrases, 1):
        if progress_callback:
            progress_callback(i, len(Input_Phrases), str(Phrase))
        # Get the publications for the current search phrase
        publication_results = scrape_scholar_data(Phrase, Num_Search_Results, keywords_list)
        
        # If no publications were found, break the loop and return an empty list
        if not publication_results:
            print(f"\nNo results found for phrase: '{Phrase}'. Breaking the loop.")
            return []  # Return empty list if no publications found

        # If publications are found, update the Excel file
        aggregate_and_update_excel(publication_results, PUB_EXCEL_FILE_PATH)
    
    if publication_results:
        PUB_log_excel=Path(CM.publications_log_path)
        
        pubs=extract_column(PUB_EXCEL_FILE_PATH,'Publication Name')
        
        log_generated_list_file(PUB_EXCEL_FILE_PATH,len(pubs),PUB_log_excel,CM)
    
    return publication_results
   



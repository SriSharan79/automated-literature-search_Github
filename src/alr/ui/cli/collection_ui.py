from pathlib import Path

from alr.common.llm_utils import llm_call
# from COMMON.System_prompts import KEYWORD_GENERATOR_PROMPT, SCOPE_DERIVATOR_PROMPT
from alr.common.general_utils import Proccess_string_to_list, generate_unique_id, print_two_column_table, print_with_separator
from alr.common.excel_utils import extract_column, get_values_from_sorted_numbers, get_values_from_sorted_numbers_and_save
from alr.collection.search_phrase_generator_utils import run_scholarly
from alr.collection.collection_system_prompts import KEYWORD_GENERATOR_PROMPT, SCOPE_DERIVATOR_PROMPT

import os

def _define_id(CM):
    RA= CM.Research_Area
    keywords_log= CM.keywords_list_log_path
    existing_ids=extract_column(keywords_log,'UUID')
    UUID = generate_unique_id(RA,existing_ids)
    return UUID


def get_RA_RQ(username):

    RA_RQ_Message=f""" 
Hello {username}!!!
To support you collection literature review 
Please provide the answers to the following question:

    """
    print(RA_RQ_Message)
    RA= input("\nWhat specific research area or topic are you focusing on for this review?\n Answer:").strip()
    RQ= input("\nWhat are the key research questions or gaps that you aim to address through this literature review?\n Answer:").strip()
    return RA,RQ

def get_llm_defined_scope(RA,RQ,service):

    Keywords_scope_derivator_UP = f"""
        1. Research Area/Topic: {RA}
        2. Key Research Questions/Gaps: {RQ}
        """

    Scope=llm_call(Keywords_scope_derivator_UP,SCOPE_DERIVATOR_PROMPT,service)

    print(f"\n Scope derived: {Scope}")

    return Scope

def remove_by_indices(index_list, string_list):
    """
    Removes elements from the string list based on the comma-separated indices provided in index_str.
    
    Parameters:
        index_str (str): Comma-separated string of indices (e.g., '1,3,5').
        string_list (list): List of strings to modify.

    Returns:
        list: Modified list of strings with specified indices removed.
    """
    if index_list == []:
        return string_list
    else:
        # Convert the comma-separated string to a list of integers
        indices = index_list
        
        # Remove elements at the specified indices (note: remove in reverse order to avoid index shifts)
        for index in sorted(indices, reverse=True):
            if 0 <= index < len(string_list):
                del string_list[index]
        
        return string_list

def add_by_indices(index_list, string_list):

    if index_list == []:
        return string_list  # Return the original list if no indices are specified
    else:
        indices = index_list
        # Create a new list containing only the elements at the specified indices
        selected_elements = [string_list[i] for i in indices if 0 <= i < len(string_list)]
        
        return selected_elements

def get_indicies(keywords_list):
    keyword_check_msg = """
Enter the numbers to be excluded separated by ',' (example: 2,8,9).
You can also specify ranges (e.g., 1:5, 7, 9:32, 45).
If you want to continue with all the existing keywords, just enter --#

Indicies:"""
    
    print_two_column_table(keywords_list, "  LLM Suggested keywords list  ")

    while True:
        # Get user input
        Num_String = input(keyword_check_msg).strip()

        # Check if the input is valid (either a valid list of numbers, ranges, or "#")
        if Num_String == "#":
            return []

        try:
            # Split by commas to get individual segments
            parts = Num_String.split(',')
            indices = []

            for part in parts:
                if ':' in part:  # Handle ranges
                    start, end = part.split(':')
                    start, end = int(start), int(end)
                    indices.extend(range(start, end + 1))  # Add the range to the list
                else:  # Single index
                    indices.append(int(part))

            return indices  # Return the list of indices
        except ValueError:
            print("Invalid input. Please enter valid comma-separated list of numbers or ranges, or just enter --# to continue with all keywords.")



def get_input_keyword_list():

    prompt_msg = """
        Please enter a list of keywords you want use for literature review, separated by commas (e.g., string1, string2, string3):
    """
    while True:
        user_input = input(prompt_msg).strip()

        # Split the input by commas, remove extra spaces, and store as a list
        string_list = [item.strip() for item in user_input.split(",")]

        # Check if the list is not empty (user hasn't just entered spaces or left it empty)
        if string_list and all(item for item in string_list):
            return string_list
        else:
            print("Invalid input. Please enter a valid list of strings separated by commas.")
            

def process_keyword_list(keywords_list):
    In_out_Msg = """
Do you want to choose the keywords from the suggested list

1- Select all from the list
2- Select specific keywords from the suggested list using the indices
3- Select all keywords except specific keywords (input indices to remove)

Please provide your choice
example: 2
choice: """

    while True:
        user_choice = input(In_out_Msg).strip()

        if user_choice == "1":
            return keywords_list

        elif user_choice == "2":
            choice_list = get_indicies(keywords_list)
            new_list = add_by_indices(choice_list, keywords_list)
            print_two_column_table(new_list, "updated keywords list")
            return new_list

        elif user_choice == "3":
            choice_list = get_indicies(keywords_list)
            new_list = remove_by_indices(choice_list, keywords_list)
            print_two_column_table(new_list, "updated keywords list")
            return new_list

        else:
            print("Invalid choice, please choose 1, 2, or 3.")

def get_keyword_list(CM,keywords_choice,service):

    RA=CM.Research_Area
    RQ=CM.Research_Question
    scope=CM.Research_Scope

    keywords_list=[]

    if keywords_choice == "y":

        Keywords_formulator_UP = f"""
            1. Research Area/Topic: {RA}
            2. Key Research Questions/Gaps: {RQ}
            3. Refined Scope: To {scope}
            """

        Res_KeyWords= llm_call (Keywords_formulator_UP,KEYWORD_GENERATOR_PROMPT,service)

        keywords_list=Proccess_string_to_list(Res_KeyWords)

        print_two_column_table(keywords_list, " LLM Suggested Keywords ")

        keywords_list = process_keyword_list(keywords_list)
        return keywords_list

    else:
        keywords_list= get_input_keyword_list()  
        return keywords_list
    

def try_pub_search(CM,rank_col):
    
    print_with_separator("Literature Collection UI",'=')
    
    pub_search_msg= f""" 
Choose one of the options!!!

S- Try scholary automated literature search (currently have very less sucess rate)
E- Save the results in an excel file

Input the number of your choice(S/E): """


    phrase_excel_file= Path(CM.search_phrase_list_excel)


    SP_Sorted_EXCEL_FILE_PATH = Path(CM.search_phrase_sorted_list_excel)
        
    num=int(input("\nHow many top ranked search phrases would like to have (input just the number): ").strip())
    
    sorted_search_phrases = get_values_from_sorted_numbers(phrase_excel_file,rank_col,'Phrase',num)

    while True:
        user_choice = input(pub_search_msg).strip().lower()

        if user_choice == "s":
            results = run_scholarly(sorted_search_phrases, CM, 15)
            if results == []:
                get_values_from_sorted_numbers_and_save(
                    phrase_excel_file, rank_col, 'Phrase', num, SP_Sorted_EXCEL_FILE_PATH
                )
            break

        elif user_choice == "e":
            get_values_from_sorted_numbers_and_save(
                phrase_excel_file, rank_col, 'Phrase', num, SP_Sorted_EXCEL_FILE_PATH
            )
            break

        else:
            print("Invalid choice, please choose S or E.")
            
    return 'done'


def Process_search_phrases(CM):

    print_with_separator("Literature Collection UI",'=')

    Ranking_message= f""" 
Currently {CM.Search_phrase_count} have been generated !!!

These search phraes were ranked considering inputs that
1. Research Area
2. Research Gap / Question 
3. Both Research Area & Research Gap / Question 
4. All the available data (Research Area ,Research Gap / Question and all the selected keywords)

on what basis would like to have the list of search phrases used for extract publications?
Input the number of your choice(1/2/3): """

    while True:
        user_choice = input(Ranking_message).strip()
        rank_col=""
        if user_choice == "1":
            rank_col='RA_Rank'
            return try_pub_search(CM,rank_col)
        elif user_choice == "2":
            rank_col='RQ_Rank'
            return try_pub_search(CM,rank_col)        
        elif user_choice == "3":
            rank_col='RA+RQ_Rank'
            return try_pub_search(CM,rank_col)     
        elif user_choice == "4":
            rank_col="TOTAL_Rank"
            return try_pub_search(CM,rank_col)
        else:
            print("Invalid choice, please choose 1, 2, or 3.")
            
    



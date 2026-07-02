
import os
from Command_Line_UI.Collection_ui import Process_search_phrases, _define_id, get_RA_RQ, get_keyword_list, get_llm_defined_scope
from COMMON.Excel_Utils import extract_column
from COMMON.File_Manager import CollectionManager,DataAnalyzeManager,Vec_DB_Manager
from COMMON.General_Utils import clean_folder_path, generate_unique_id, print_with_separator
from COLLECTION.Search_Phrase_Generator_Logger import log_Keyword_Json
from COLLECTION.Search_Phrase_Generator_Utils import Keywords_Processing_with_scope
from Command_Line_UI.Data_analysis_UI import get_file_or_folder
from DATA_ANALYSIS.Pdf_File_processor import process_pdf_file, process_pdf_mode_file
from DATA_ANALYSIS.Folder_Data_Analyzer import process_folder
from RAG_BUILDERs.DB_Manager import generate_databases
from RAG_BUILDERs.querry_excecuter import generate_query_report

username = os.environ.get("USERNAME")

def _choose_llm_service():

    LLM_Choice_Message=f""" 
please choose the llm service that you would like to use:

'O' or 'o': DLR ollama Nimbus Service 
'B' or 'b': BlaBla LLM models

Only input the number of your choice 
example: B

    """
    print(LLM_Choice_Message)
    user_choice = input("Enter the number of your choice: ").strip()

    return user_choice

def _user_check(text):

    choice = input("\nDo you want to edit this? (y/n): ").strip().lower()

    if choice == "y":
        
        new_text = input("Enter the new value:\n").strip()
        return new_text if new_text else text

    return text

def _choose_data_storage_path(username,type):

    File_choice_Message=f""" 
Dear {username}!!!
Do you want to choose a different path to store data related to {type} (y/n):
"""
    choice=input(File_choice_Message).strip().lower()

    return choice
def _define_data_storage_path(choice,type):

    FM =None
    if choice=='y':
        path_input=input(f"please provide the folder path to store the {type} data: ").strip()
        storage_path=clean_folder_path(path_input)
        if type=="Collection":
            FM=CollectionManager(storage_path)
        elif type=="Data_Analysis":            
            FM=DataAnalyzeManager(storage_path)
        elif type=="Vsiualize_VBD":            
            FM=Vec_DB_Manager(storage_path)
        else:
            FM= None
    else:
        if type=="Collection":
            FM=CollectionManager()
        elif type=="Data_Analysis":            
            FM=DataAnalyzeManager()
        elif type=="Vsiualize_VBD":            
            FM=Vec_DB_Manager()
        else:
            FM= None

    return FM

def get_collection_ui():

    type= "Collection"

    print_with_separator("Literature Collection UI",'=')

    choice = _choose_data_storage_path(username,type)

    CM =_define_data_storage_path(choice,type)

    print_with_separator("Literature Collection UI",'=')

    RA,RQ = get_RA_RQ(username)

    CM.update_Research_Area(RA)

    CM.update_Research_Question(RQ)

    topic_id= _define_id(CM)

    CM.update_topic_files(topic_id)

    print_with_separator("Literature Collection UI",'=')

    llm_service=_choose_llm_service()
    
    CM.update_llm_service(llm_service)

    scope = get_llm_defined_scope(RA,RQ,llm_service)

    print_with_separator("Literature Collection UI",'=')

    scope = _user_check(scope)

    CM.update_Research_Scope(scope)
        
    print_with_separator("Literature Collection UI",'=')

    keywords_choice = input("\nDo you want to get LLM sugested keyword list? (y/n): ").strip().lower()    
    
    print_with_separator("Literature Collection UI",'=')

    Keywords_list = get_keyword_list(CM,keywords_choice,llm_service)

    CM.update_Keyword_list(Keywords_list)

    log_Keyword_Json(CM)

    CM=Keywords_Processing_with_scope(CM)

    return Process_search_phrases(CM)

    

def get_data_analysis_ui():

    type= "Data_Analysis"    
    
    print_with_separator("Literature Analysis UI",'+-')
    
    result= get_file_or_folder()
    
    print_with_separator("Literature Analysis UI",'+-')

    choice = _choose_data_storage_path(username,type)

    MF =_define_data_storage_path(choice,type)
    
    print_with_separator("Literature Analysis UI",'+-')

    llm_service=_choose_llm_service()
    
    MF.update_llm_service(llm_service)
    # while True:
    if result.kind=="pdf_file":
        # get_single_pdf_process_options(result) --- need to be built
        return process_pdf_mode_file(result.input_path,MF.folder,'a')
        
    elif result.kind=="folder":        
        # get_folder_process_options(result)   --- need to be built
        return process_folder(result.input_path,MF.folder)
    else:
        print('processing intureputed!!!')
 
 
def get_visualize_ui():
          
    type= "Vsiualize_VBD"
    print_with_separator("Literature Visualize UI",'+-')    

    choice = _choose_data_storage_path(username,type)
    generate_databases(choice)    
    querry = input("\nGive the statement or the search querry that you want to use to identify the literature: ").strip().lower()    
    generate_query_report([querry],choice)


def select_functionality():

    print_with_separator("Automated literature review support Tool")

    Opening_Display_Message=f""" 
Hello {username}!!!
Choose what one aspect that you would like to do:

1. Collect: To collect the literature considering a specific Research Area, Reasearch Question 
(Optional: with specific keywords).
2. Analyze: To extract and analyze the existing literature documents 
3. Visualize: To visualize the analyzed data (not built as of now)

#- Exit

Only input the number of your choice 
example: 1 

    """
    print(Opening_Display_Message)
    user_choice = input("Enter the number of your choice: ").strip()

    if user_choice == "1":
        get_collection_ui()  
    elif user_choice == "2":
        get_data_analysis_ui() 
    elif user_choice == "3":
        print("Not fully refined yet just Querrying relevant literature feature")
        get_visualize_ui()
    elif user_choice == "#":
        print("Exiting Tool!!!!")
        return
    else:
        print("Invalid choice, please choose a valid option.")
    
    select_functionality()

if __name__ == "__main__":
    select_functionality()


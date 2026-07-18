import os


#  Configuration File 

#Working Area: Used for Automated file creation
# Topic='Literature_review_2'

# base_output_path = "Output_Files"

# Search_phrases_Folder= os.path.join(base_output_path,'Search_Phrases_Files')

# os.makedirs(Search_phrases_Folder, exist_ok=True)

# Search_phrases_file_path = os.path.join(Search_phrases_Folder,Topic+"_KeyWords_log.xlsx")

# Publication_Collection_Folder= os.path.join(base_output_path,'Publication_Collection_Files')

# os.makedirs(Publication_Collection_Folder, exist_ok=True)

# PUB_EXCEL_FILE_PATH =os.path.join(Search_phrases_Folder,Topic+"_Publications.xlsx")

PuB_Column_Name= 'Publication Name' 

# Number of Search phrases that are considered for the combination of keywords
Num_Search_Phrases = 10

# Number of publications collected from a search phrase
Num_Search_Results = 15

#If the keywords are not seperated
Input_KeyWords = []

#If there is a speration of keywords 
#Example:
AI_KEYWORDS=[
'Machine Learning (ML)',
'Deep Learning (DL)', 
'Generative AI', 
'Large Language Models (LLMs)',
'Neural Networks', 
'Reinforcement Learning (RL)', 
'Natural Language Processing (NLP)',
'Explainable AI (XAI)',
'Algorithmic Bias', 
'Foundation Models',
'AI Agents'
]

REQ_KEYWORDS= [ 
"requirements engineering",
"requirements elicitation",
"requirements analysis",
"requirements modeling",
"requirements specification",
"requirements validation",
"requirements management"
]

# All input keywords
TOTAL_KEYWORDS=AI_KEYWORDS+REQ_KEYWORDS
#Configured Column Order for Publication Collection

COLUMNS = [
    'Occurrence',
    'Search Phrase',
    'Publication Name',
    'Keywords in Title',
    'Abstract',
    'Link',
    'Organization',
    'Publication Year',
    'Authors',
    'Source'   # which backend produced the row: 'OpenAlex' or 'Google Scholar'
]

#Configured Column Order for Search Phrase collection
COLUMNS_Keyphrase=[
    'Time', 
    'Keywords', 
    'Occurrence',
    'Phrase',
]
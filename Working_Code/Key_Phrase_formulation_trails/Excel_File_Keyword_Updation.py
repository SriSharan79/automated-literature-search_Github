
import pandas as pd
from typing import List, Optional

def tag_excel_data_with_keywords(
    file_path: str,
    column_name: str,
    keywords: List[str],
    output_column_name: str = 'KeyWords In Phrase'
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
    def find_keywords_in_cell(text: str) -> str:
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

    # 3. Apply the function to the specified column and create the new column
    df[output_column_name] = df[column_name].apply(find_keywords_in_cell)

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
    output_file_path = file_path.replace(".xlsx", "_tagged.xlsx").replace(".xls", "_tagged.xls")
    df.to_excel(output_file_path, index=False)
    
    print(f"\n✅ Success! New file saved to: {output_file_path}")
    print(f"Column '{output_column_name}' has been added next to '{column_name}'.")
    
    return df


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

# --- Example Usage ---
# ⚠️ REMEMBER TO CHANGE THESE VALUES TO YOUR ACTUAL FILE AND DATA ⚠️

# 1. Set the file path
EXCEL_FILE_PATH ='Output Excel Files/AI_SE_Domains_Publications.xlsx'

# 2. Set the column to search in
target_column = 'Publication Name' 

# 3. Set the list of keywords to look for
search_keywords = ['organic', 'gluten-free', 'vegan', 'spicy', 'freshly baked']

REQ_KEYWORDS= [ 
"requirements engineering",
"requirements elicitation",
"requirements analysis",
"requirements modeling",
"requirements specification",
"requirements validation",
"requirements management",
"deep learning", 
"Machine learning",
"natural language processing", 
"Artificial Intelligence",
"Generative AI",
"Large Language Model",
"Systems Engineering",
'Safety engineering',
'Model-Based Systems Engineering', 
'Model-Based Systems Engineering (MBSE)',
'Model-Based Safety Analysis',
'Model-Based Safety Assessment',
'Model-Based Safety Analysis (MBSA)',
'SysML','MBSE','MBSA','LLM','AI','DL','ML','Gen-AI','NLP','RL','XAI',
'SysML (System Modeling Language)',
'Risk Assessment/Mitigation',
'Verification and Validation (V&V)',
'Machine Learning (ML)',
'Deep Learning (DL)', 
'Generative AI', 
'Large Language Models (LLMs)',
'Neural Networks', 
'Reinforcement Learning (RL)', 
'Natural Language Processing (NLP)',
'Explainable AI (XAI)',
# 'Algorithmic Bias', 
# 'Foundation Models',
'AI Agents'
]

# # Run the function
# modified_df = tag_excel_data_with_keywords(
#     file_path=EXCEL_FILE_PATH,
#     column_name=target_column,
#     keywords=REQ_KEYWORDS
# )

# # # Optional: Display the first few rows of the result
# # if modified_df is not None:
# #     print("\n--- First 5 Rows of Modified Data ---")
# #     print(modified_df.head())


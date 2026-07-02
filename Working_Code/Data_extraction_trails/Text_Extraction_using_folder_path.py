import re
from docling.document_converter import DocumentConverter
from docling.chunking import HybridChunker
from colorama import Fore,Style
import os
import pandas as pd
import json

from title_extracter import*
from Excel_Json_reading import*


def is_string_in_list_case_insensitive(target_string, string_list):
  """
  Checks if a target string exists in a list of strings, ignoring case.

  Args:
    target_string (str): The string to search for.
    string_list (list): The list of strings to search within.

  Returns:
    bool: True if the target_string is found (case-insensitively) in the list, False otherwise.
  """
  target_string_lower = target_string.lower()
  for item in string_list:
    if item.lower() == target_string_lower:
      return True
  return False

def get_first_line(text: str) -> str:
    """
    Returns the first non-empty line of a given string.
    
    Args:
        text (str): Input string/text
        
    Returns:
        str: First non-empty line of the text (empty string if no text found)
    
    Examples:
        >>> get_first_line("\\n\\nAbstract\\nThis paper...")
        'Abstract'
        >>> get_first_line("Introduction\\nThe study...")
        'Introduction'
        >>> get_first_line("\\nSingle line")
        'Single line'
        >>> get_first_line("")
        ''
    """
    if not text or not isinstance(text, str):
        return ""
    
    lines = text.split('\n')
    for line in lines:
        stripped_line = line.strip()
        if stripped_line:  # Skip empty/whitespace-only lines
            return stripped_line
    
    return ""


def process_document_chunks(chunks, doc):
    """
    Processes a list of document chunks, extracting and concatenating text content.
    This version is simplified and addresses previous issues.

    Args:
        chunks (list): A list of chunk objects.
        doc (object): The document object containing the 'texts' attribute.
        unimportant_headings (list): A list of headings to ignore.

    Returns:
        list: A list of processed document text strings.
    """
    last_text_item_index = -1
    processed_document_texts = []
    previous_Heading = None

    for i, chunk in enumerate(chunks):
        print(Fore.YELLOW + f"\n--- Processing Chunk {i+1} ---" + Style.RESET_ALL)
        print(Fore.LIGHTBLUE_EX + f"Raw Text:\n{chunk.text}" + Style.RESET_ALL) # Optional: enable for raw chunk text

        chunk_heading = None
        chunk_meta_dict = chunk.meta.model_dump()

        # Safely extract Chunk_Heading
        headings_list = chunk_meta_dict.get("headings")
        if headings_list and isinstance(headings_list, list) and len(headings_list) > 0:
            chunk_heading = str(headings_list[0]).strip() # .strip() to clean whitespace
        else:
            print(Fore.RED + "    Warning: No valid heading found for this chunk." + Style.RESET_ALL)

        print(Fore.GREEN + f"    Chunk Heading: {chunk_heading if chunk_heading else 'N/A'}." + Style.RESET_ALL)

        text_in_chunk=""

        for j, doc_item in enumerate(chunk.meta.doc_items):

            current_label = getattr(doc_item, 'label', 'N/A')
            # print(Fore.GREEN+ f" Text found in \n{chunk.text}" + Fore.RESET)
            self_ref = getattr(doc_item, 'self_ref', None)
            actual_text_content = None # Initialize to None

            if self_ref:
                # Regex to extract the index from the self_ref, e.g., '#/texts/91' -> '91'
                match = re.match(r'#/texts/(\d+)', self_ref)
                if match:
                    text_index = int(match.group(1))
                    try:
                        # Access the actual Text object from the document's texts list
                        # And then extract its 'text' attribute
                        actual_text_content = doc.texts[text_index].text
                    except IndexError:
                            # This handles cases where the index might be out of bounds
                            print(Fore.RED+f"    Warning: text_index {text_index} out of bounds for doc.texts (self_ref: {self_ref})."+Fore.RESET)
                    except AttributeError:    
                            # This handles cases where doc.texts[text_index] might not have a .text attribute
                            # (e.g., if it's not a Text object as expected)
                            print(Fore.RED+f"    Warning: Object at doc.texts[{text_index}] does not have a .text attribute (self_ref: {self_ref})."+Fore.RESET)

                # Now, apply the concatenation logic based on the label and extracted content
            if actual_text_content is not None and len(actual_text_content)>20:
                if current_label == 'text':
                    if text_in_chunk!="":
                        text_in_chunk+= actual_text_content
                        # processed_document_texts.append(actual_text_content)
                        # last_text_item_index = len(processed_document_texts) - 1
                        print(f"    >>> Added new 'text' item to list at index . <<<")
                    

                    elif last_text_item_index != -1 and text_in_chunk=="" and previous_Heading==chunk_heading:
                        # Concatenate to the last 'text' item.
                        # Using '\n- ' for better readability for list items.
                        # Adjust concatenation format as needed (e.g., '\n' or ' ')

                        text_in_chunk =  processed_document_texts[last_text_item_index]
                        text_in_chunk=text_in_chunk.replace(chunk_heading+"\n","")
                        text_in_chunk += " " + actual_text_content
                        processed_document_texts.pop(last_text_item_index)
                        # processed_document_texts[last_text_item_index] += "\n- " + actual_text_content
                        print(f"    >>> Concatenated 'list_item' to previous 'text' item at index {last_text_item_index} . <<<")
                    

                    elif text_in_chunk=="" and previous_Heading!=chunk_heading:
                        
                        text_in_chunk += " " + actual_text_content   

                        print(f"    >>> Concatenated 'list_item' to in new text 'text_in_chunk' . <<<")
                    
                    else:
                        # This list_item cannot be concatenated, so it's ignored based on the requirement.
                        # If you want to add it as a new item if no text precedes it, change this logic.
                        print(f"    Skipping 'list_item' as no preceding 'text' item was found to concatenate to.")


                elif current_label == 'list_item':

                    if text_in_chunk!="":

                        text_in_chunk += "\n- " + actual_text_content   

                        print(f"    >>> Concatenated 'list_item' to previous 'text_in_chunk' . <<<")

                    elif last_text_item_index != -1 and text_in_chunk=="" and previous_Heading==chunk_heading:
                        # Concatenate to the last 'text' item.
                        # Using '\n- ' for better readability for list items.
                        # Adjust concatenation format as needed (e.g., '\n' or ' ')

                        text_in_chunk =  processed_document_texts[last_text_item_index]
                        text_in_chunk=text_in_chunk.replace(chunk_heading+"\n","")
                        text_in_chunk += "\n- " + actual_text_content
                        processed_document_texts.pop(last_text_item_index)
                        # processed_document_texts[last_text_item_index] += "\n- " + actual_text_content
                        print(f"    >>> Concatenated 'list_item' to previous 'text' item at index {last_text_item_index} . <<<")
                    
                    elif text_in_chunk=="" and previous_Heading!=chunk_heading:
                        
                        text_in_chunk += "\n- " + actual_text_content   

                        print(f"    >>> Concatenated 'list_item' to in new text 'text_in_chunk' . <<<")
                    
                    else:
                        # This list_item cannot be concatenated, so it's ignored based on the requirement.
                        # If you want to add it as a new item if no text precedes it, change this logic.
                        print(f"    Skipping 'list_item' as no preceding 'text' item was found to concatenate to.")
                else:
                    print(f"    Info: DocItem label '{current_label}' is not 'text' or 'list_item'. Skipping.")
            else:
                print(f"    Info: No valid text content extracted for this doc_item. Skipping.")

        if text_in_chunk!="":
            text_in_chunk=chunk_heading+"\n"+text_in_chunk
            previous_Heading=chunk_heading
            processed_document_texts.append(text_in_chunk)
            last_text_item_index = len(processed_document_texts) - 1

    return processed_document_texts 



def process_headings(chunks, doc):
    """
    Processes a list of document chunks, extracting and concatenating text content.
    Categorizes content into sections: Abstract, Introduction, Conclusion, References.
    
    Args:
        chunks (list): A list of chunk objects.
        doc (object): The document object containing the 'texts' attribute.
        unimportant_headings (list): A list of headings to ignore.

    Returns:
        dict: Dictionary with keys ['Abstract', 'Introduction', 'Conclusion', 'References']
              containing concatenated text for matching sections. Empty strings for unmatched sections.
    """
    # Section mapping
    important_headings = [
        "abstract", "introduction", "conclusion", "summary", 
        "bibliography", "references"
    ]
    
    section_mapping = {
        "Abstract": ["abstract"],
        "Introduction": ["introduction"],
        "Conclusion": ["conclusion", "summary"],
        "References": ["bibliography", "references"]
    }
    
    # Initialize section dictionary with empty strings
    sections = {
        "Abstract": "",
        "Introduction": "",
        "Conclusion": "",
        "References": ""
    }
    processed_document_texts=process_document_chunks(chunks, doc)

    for text in processed_document_texts:
        first_line = get_first_line(text)  # Fix typo
        for section_name, matching_headings in section_mapping.items():
            if first_line and any(h.lower() in first_line.lower() for h in matching_headings):
            # if first_line and any(first_line.lower() == h.lower() for h in matching_headings):  # Fix condition + define matching
                sections[section_name] = text
                print(Fore.CYAN + f"    >>> Added to {section_name} section <<<" + Style.RESET_ALL)
                break  # Add break to prevent multiple matches


    print(Fore.MAGENTA + f"\nSection Summary:")
    for section, content in sections.items():
        status = "✓ Found" if content else "✗ Empty"
        print(f"  {section}: {status}" + Style.RESET_ALL)    
    
    return sections


def process_folder(folder_path):
    """
    Main orchestrator: modular PDF folder processing with Excel+JSON output.
    """
    excel_name, json_name = "Folder_Analysis.xlsx", "Folder_Analysis.json"
    excel_path = os.path.join(folder_path, excel_name)
    json_path = os.path.join(folder_path, json_name)
    
    print("=== Folder Analysis Started ===")
    
    # 1. Load data
    print("1. Loading existing data...")
    df_excel = load_existing_data(excel_path, is_excel=True)
    df_json = load_existing_data(json_path, is_excel=False)
    
    if not df_excel.empty:
        print(f"   Excel: {len(df_excel)} files")
    if not df_json.empty:
        print(f"   JSON: {len(df_json)} files")
    
    # 2. Sync histories
    print("2. Syncing histories...")
    df_excel, df_json = sync_histories(df_excel, df_json)
    
    # 3. Find & process new files
    print("3. Scanning for new PDFs...")
    processed_files = get_processed_files(df_excel) | get_processed_files(df_json)
    skip_files = {excel_name, json_name}

    converter = DocumentConverter()
    chunker = HybridChunker()
    
    new_data = []
    for file_name in os.listdir(folder_path):
        if should_process_file(file_name, processed_files, skip_files):
            file_path = os.path.join(folder_path, file_name)
            try:

                doc = converter.convert(file_path).document
                chunks = chunker.chunk(dl_doc=doc)
                content = process_headings(chunks, doc)
                
                entry = {
                    "File Name": os.path.basename(file_path),
                    "File Path": file_path,
                    "Title": get_title_in_the_file(file_path),
                    "Abstract": content.get("Abstract"),
                    "Introduction": content.get("Introduction"),
                    "Conclusion": content.get("Conclusion"),
                    "References": content.get("References")

                }
            except Exception as e:
                print(f"Error reading {os.path.basename(file_path)}: {e}")
            if entry:
                new_data.append(entry)    
        
    
    # 4. Save updated data
    print("4. Saving results...")
    if new_data:
        df_new = pd.DataFrame(new_data)
        num_new = len(new_data)
        
        # Excel
        df_final_excel = pd.concat([df_excel, df_new], ignore_index=True) if not df_excel.empty else df_new
        save_data(df_final_excel, excel_path, is_excel=True, file_type_name="Excel")
        
        # JSON  
        df_final_json = pd.concat([df_json, df_new], ignore_index=True) if not df_json.empty else df_new
        save_data(df_final_json, json_path, is_excel=False, file_type_name="JSON")
        
        print(f"Complete! Added {num_new} new files.")
    else:
        print("No new PDFs found.")
    
    print("=== Done ===")

if __name__ == "__main__":


    process_folder("/localdata/user/kata_du/Automated Literature Survey/downloads/Test_Folder") 
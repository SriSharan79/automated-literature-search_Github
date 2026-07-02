import re
from docling.document_converter import DocumentConverter
from docling.chunking import HybridChunker
from colorama import Fore,Style
import os
import pandas as pd
import json

unimportant_headings =[ "content",
                        "table of contents",
                        "acknowledgements",
                        "index",
                        "bibliography",
                        "glossary",
                        "appendix a: data tables",
                        "figure list",
                        "list of abbreviations",
                        "references",
                        "colophon",
                        "copyright page",
                        "dedication",
                        "preface",
                        "foreword",
                        "epilogue",
                        "afterword",
                        "errata",
                        "permissions",
                        "acknowledgments",
                        "author's note",
                        "references"]

def json_to_excel(json_filepath, excel_filepath, sheet_name='Sheet1'):
    """
    Reads a JSON file, where each top-level key becomes a new column,
    and the corresponding values populate the rows, then saves it to an Excel file.

    Args:
        json_filepath (str): The path to the input JSON file.
        excel_filepath (str): The path to the output Excel file (.xlsx).
        sheet_name (str, optional): The name of the sheet in the Excel file.
                                    Defaults to 'Sheet1'.
    """
    try:
        with open(json_filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # If the JSON is a list of dictionaries (most common tabular JSON),
        # pandas can directly convert it.
        # Example: [{"col1": "valA", "col2": "valB"}, {"col1": "valC", "col2": "valD"}]
        if isinstance(data, list) and all(isinstance(item, dict) for item in data):
            df = pd.DataFrame(data)
        
        # If the JSON is a single dictionary where keys are columns and values are lists of data,
        # e.g., {"col1": ["valA", "valC"], "col2": ["valB", "valD"]}
        elif isinstance(data, dict):
            # Check if values are list-like (assuming each list represents a column's data)
            # This might need adjustment based on your *exact* JSON structure
            if all(isinstance(v, list) for v in data.values()):
                df = pd.DataFrame(data)
            else:
                # If values are not lists, it's likely a single row dictionary
                # Convert it to a list containing that single dictionary
                df = pd.DataFrame([data])
        else:
            print(f"Unsupported JSON structure. Expected a list of dictionaries or a dictionary of lists/single dictionary. Got: {type(data)}")
            return

        # Write the DataFrame to an Excel file
        # index=False prevents writing the DataFrame index as a column in Excel
        df.to_excel(excel_filepath, sheet_name=sheet_name, index=False)
        print(f"Successfully converted '{json_filepath}' to '{excel_filepath}' on sheet '{sheet_name}'.")

    except FileNotFoundError:
        print(f"Error: JSON file not found at '{json_filepath}'")
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from '{json_filepath}'. Check if it's a valid JSON file.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

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



def process_document_chunks(chunks, doc, unimportant_headings):
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

        # Skip chunk if heading is unimportant
        if chunk_heading and is_string_in_list_case_insensitive(chunk_heading, unimportant_headings):
            print(Fore.CYAN + "    Skipping chunk due to unimportant heading." + Style.RESET_ALL)
            continue

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



def Text_spliting_Docling_with_File_path(File_Path):
    Processed_Files_Folder="Text_Processed_Files"
    os.makedirs(Processed_Files_Folder, exist_ok=True) # Ensure the directory exists
    PDF_filename_without_ext = os.path.splitext(os.path.basename(File_Path))[0] 
    Folder_Storing_Files=os.path.join(Processed_Files_Folder, PDF_filename_without_ext)
    json_output_path = os.path.join(Folder_Storing_Files, PDF_filename_without_ext+"_Text_Chunks.json")
    excel_File_path = os.path.join(Folder_Storing_Files, PDF_filename_without_ext+"_Text_Chunks.xlsx")
    converter = DocumentConverter()
    try:
        doc = converter.convert(File_Path).document
    except FileNotFoundError:
        print(Fore.RED + "Error: AFHA.pdf not found. Please ensure the PDF is in the correct directory." + Fore.RESET)
        exit()

    chunker = HybridChunker()
    chunks = chunker.chunk(dl_doc=doc)

    list_of_text_pairs=[]
    processed_data_for_json = [] # List to hold dictionaries of processed data
    processed_document_texts = process_document_chunks(chunks,doc,unimportant_headings)
        
    Id=0
    for text in processed_document_texts:
        if text is not None and len(text) > 20 and text != "":
            list_of_text_pairs.append((Id,text))
            processed_data_for_json.append({
                "ID": Id, # Include an identifier if available
                # "original_text": chunk.text,
                # "enriched_text": enriched_text,
                "Text_Content":text,
                # "chunk_id": chunk_id, # Include an identifier if available
            })
            Id=Id+1
    # After the loop, save all the collected data to a JSON file
    try:
        with open(json_output_path, 'w', encoding='utf-8') as f:
            json.dump(processed_data_for_json, f, indent=4)
        print(Fore.GREEN + f"\nSuccessfully saved {len(processed_data_for_json)} processed chunks to {json_output_path}" + Fore.RESET)
    except TypeError as e:
        print(Fore.RED + f"Error saving data: {e}. Make sure all data is JSON serializable." + Fore.RESET)
    except Exception as e:
        print(Fore.RED + f"An unexpected error occurred while saving: {e}" + Fore.RESET)

    json_to_excel(json_output_path, excel_File_path, sheet_name='List_item')

    return list_of_text_pairs

# if __name__ == "__main__":

#     list_of_text_pairs= Text_spliting_Docling_with_File_path(PDF_FILE_Path)
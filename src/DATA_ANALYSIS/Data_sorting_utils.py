import sys
sys.path.extend([
    r'src',
    r'src/COLLECTION',
    r'Working_Code',
    r'src/DATA_ANALYSIS',
    r'src/COMMON',
    r'src/Command_Line_UI'
])

import re
from colorama import Fore,Style

from difflib import SequenceMatcher

import os
import pandas as pd
# Constant Mapping for clean access
HEADER_MAPPING = {
    "Abstract": ["abstract"],
    "Keywords": ["keywords"],
    "Introduction": ["introduction"],
    "Conclusion": ["conclusion", "summary"],
    "References": ["references", "bibliography", "reference list", 
                   "literature cited", "works cited", "sources", 
                   "literatur", "références"],
    "Acknowledgments": ["acknowledgments", "acknowledgements", "funding", "disclosure"]
}

# def extract_chunk_heading(chunk):
#     chunk_meta_dict = chunk.meta.model_dump()
#     headings_list = chunk_meta_dict.get("headings")
#     if headings_list and isinstance(headings_list, list) and len(headings_list) > 0:
#         chunk_heading = str(headings_list[0]).strip()
#     else:
#         # print(Fore.RED + "    Warning: No valid heading found for this chunk." + Style.RESET_ALL)
#         chunk_heading = 'NA'
#     return chunk_heading

def extract_chunk_heading(chunk):
    """Handles extraction from both raw Pydantic/Docling models and cached dictionaries."""
    # Check if chunk is a dictionary (from JSON cache)
    if isinstance(chunk, dict):
        meta = chunk.get("meta", {})
        headings_list = meta.get("headings", [])
    else:
        # Live Docling object handling
        if hasattr(chunk, 'meta') and hasattr(chunk.meta, 'model_dump'):
            chunk_meta_dict = chunk.meta.model_dump()
            headings_list = chunk_meta_dict.get("headings", [])
        elif hasattr(chunk, 'meta') and hasattr(chunk.meta, 'headings'):
            headings_list = chunk.meta.headings
        else:
            headings_list = []

    if headings_list and isinstance(headings_list, list) and len(headings_list) > 0:
        return str(headings_list[0]).strip()
    
    return 'NA'

# def get_actual_text_content(doc_item, doc):
#     self_ref = getattr(doc_item, 'self_ref', None)
#     actual_text_content = None
#     if self_ref:
#         match = re.match(r'#/texts/(\d+)', self_ref)
#         if match:
#             text_index = int(match.group(1))
#             try:
#                 actual_text_content = doc.texts[text_index].text
#             except IndexError:
#                 print(Fore.RED + f"    Warning: text_index {text_index} out of bounds for doc.texts (self_ref: {self_ref})." + Fore.RESET)
#             except AttributeError:
#                 print(Fore.RED + f"    Warning: Object at doc.texts[{text_index}] does not have a .text attribute (self_ref: {self_ref})." + Fore.RESET)
#     return actual_text_content

def get_actual_text_content(doc_item, doc):
    """
    Safely retrieves text content. Fallback matches cached raw data patterns 
    when text is already merged or when doc tree is out of range.
    """
    # Handle dictionary (cached) or object (live)
    self_ref = doc_item.get('self_ref') if isinstance(doc_item, dict) else getattr(doc_item, 'self_ref', None)
    
    if not doc:
        # If running purely from JSON cache, there is no master 'doc' lookup object.
        # However, since chunk.text is used as standard text fallback, this is safe.
        return None

    actual_text_content = None
    if self_ref:
        match = re.match(r'#/texts/(\d+)', self_ref)
        if match:
            text_index = int(match.group(1))
            try:
                actual_text_content = doc.texts[text_index].text
            except IndexError:
                print(Fore.RED + f"    Warning: text_index {text_index} out of bounds for doc.texts." + Fore.RESET)
            except AttributeError:
                print(Fore.RED + f"    Warning: Object at doc.texts[{text_index}] lacks .text attribute." + Fore.RESET)
    return actual_text_content

def handle_text_label(actual_text_content, text_in_chunk, last_text_item_index, processed_document_texts, previous_Heading, chunk_heading):
    if text_in_chunk != "":
        text_in_chunk += actual_text_content
        # print(f"    >>> Added new 'text' item to list at index . <<<")
    elif last_text_item_index != -1 and text_in_chunk == "" and previous_Heading == chunk_heading:
        text_in_chunk = processed_document_texts[last_text_item_index]
        text_in_chunk = text_in_chunk.replace(chunk_heading + "\n", "")
        text_in_chunk += " " + actual_text_content
        processed_document_texts.pop(last_text_item_index)
        # print(f"    >>> Concatenated 'list_item' to previous 'text' item at index {last_text_item_index} . <<<")
    elif text_in_chunk == "" and previous_Heading != chunk_heading:
        text_in_chunk += " " + actual_text_content
        # print(f"    >>> Concatenated 'list_item' to in new text 'text_in_chunk' . <<<")
    else:
        # print(f"    Skipping 'list_item' as no preceding 'text' item was found to concatenate to.")
        return text_in_chunk
    return text_in_chunk

def handle_list_item_label(actual_text_content, text_in_chunk, last_text_item_index, processed_document_texts, previous_Heading, chunk_heading):
    if text_in_chunk != "":
        text_in_chunk += "\n- " + actual_text_content
        # print(f"    >>> Concatenated 'list_item' to previous 'text_in_chunk' . <<<")
    elif last_text_item_index != -1 and text_in_chunk == "" and previous_Heading == chunk_heading:
        text_in_chunk = processed_document_texts[last_text_item_index]
        text_in_chunk = text_in_chunk.replace(chunk_heading + "\n", "")
        text_in_chunk += "\n- " + actual_text_content
        processed_document_texts.pop(last_text_item_index)
        # print(f"    >>> Concatenated 'list_item' to previous 'text' item at index {last_text_item_index} . <<<")
    elif text_in_chunk == "" and previous_Heading != chunk_heading:
        text_in_chunk += "\n- " + actual_text_content
        # print(f"    >>> Concatenated 'list_item' to in new text 'text_in_chunk' . <<<")
    else:
        # print(f"    Skipping 'list_item' as no preceding 'text' item was found to concatenate to.")
        return text_in_chunk
    return text_in_chunk

# def process_document_chunks(chunks, doc):
#     last_text_item_index = -1
#     processed_document_texts = []
#     previous_Heading = None
    
#     # NEW: List to store unique headings in the order they appear
#     unique_headings_list = []

#     for i, chunk in enumerate(chunks):
#         chunk_heading = extract_chunk_heading(chunk)
        
#         # NEW: Logic to store unique headings
#         if chunk_heading and chunk_heading not in unique_headings_list:
#             unique_headings_list.append(chunk_heading)

#         text_in_chunk = ""

#         for doc_item in chunk.meta.doc_items:
#             current_label = getattr(doc_item, 'label', 'N/A')
#             actual_text_content = get_actual_text_content(doc_item, doc)

#             if actual_text_content is not None and len(actual_text_content) > 20:
#                 if current_label == 'text':
#                     text_in_chunk = handle_text_label(actual_text_content, text_in_chunk, last_text_item_index, processed_document_texts, previous_Heading, chunk_heading)
#                 elif current_label == 'list_item':
#                     text_in_chunk = handle_list_item_label(actual_text_content, text_in_chunk, last_text_item_index, processed_document_texts, previous_Heading, chunk_heading)

#         if text_in_chunk != "":
#             text_in_chunk = (chunk_heading if chunk_heading else "N/A") + "\n" + text_in_chunk
#             previous_Heading = chunk_heading
#             processed_document_texts.append(text_in_chunk)
#             last_text_item_index = len(processed_document_texts) - 1

#     # Return both the processed texts and the unique headings list
#     return processed_document_texts, unique_headings_list

def process_document_chunks(chunks, doc):
    last_text_item_index = -1
    processed_document_texts = []
    previous_Heading = None
    unique_headings_list = []

    for chunk in chunks:
        chunk_heading = extract_chunk_heading(chunk)
        
        if chunk_heading and chunk_heading not in unique_headings_list:
            unique_headings_list.append(chunk_heading)

        text_in_chunk = ""
        
        # Pull metadata components based on dictionary status or live class structure
        if isinstance(chunk, dict):
            doc_items = chunk.get("meta", {}).get("doc_items", [])
            fallback_text = chunk.get("text", "")
        else:
            doc_items = chunk.meta.doc_items if hasattr(chunk, 'meta') and hasattr(chunk.meta, 'doc_items') else []
            fallback_text = chunk.text if hasattr(chunk, 'text') else ""

        # Process granular sub-items if master document tree available
        if doc and doc_items:
            for doc_item in doc_items:
                current_label = doc_item.get('label', 'N/A') if isinstance(doc_item, dict) else getattr(doc_item, 'label', 'N/A')
                actual_text_content = get_actual_text_content(doc_item, doc)

                if actual_text_content is not None and len(actual_text_content) > 20:
                    if current_label == 'text':
                        text_in_chunk = handle_text_label(actual_text_content, text_in_chunk, last_text_item_index, processed_document_texts, previous_Heading, chunk_heading)
                    elif current_label == 'list_item':
                        text_in_chunk = handle_list_item_label(actual_text_content, text_in_chunk, last_text_item_index, processed_document_texts, previous_Heading, chunk_heading)
        
        # CRITICAL CACHE FALLBACK: If we are running purely from cache file (doc is None), 
        # use the pre-extracted raw chunk text saved inside the json object layout.
        if (not doc or text_in_chunk == "") and fallback_text.strip():
            text_in_chunk = fallback_text.strip()

        if text_in_chunk != "":
            text_in_chunk = (chunk_heading if chunk_heading else "N/A") + "\n" + text_in_chunk
            previous_Heading = chunk_heading
            processed_document_texts.append(text_in_chunk)
            last_text_item_index = len(processed_document_texts) - 1

    return processed_document_texts, unique_headings_list

def sort_raw_text_with_headings(chunks, doc):
    # This list will store objects for each unique section
    processed_document_sections = []
    # Dictionary to keep track of which index in our list belongs to which heading
    heading_to_index = {}

    for chunk in chunks:
        chunk_heading = extract_chunk_heading(chunk)
       # Access safely depending on dictionary storage or object instances
        text_in_chunk = chunk.get("text", "").strip() if isinstance(chunk, dict) else chunk.text.strip()

        # Skip empty chunks
        if not text_in_chunk:
            continue

        # Use a default heading if none is found
        heading_key = chunk_heading if chunk_heading else "General"

        if heading_key not in heading_to_index:
            # Create a new section object
            new_section = {
                "Section Heading": heading_key,
                "Chunks": []
            }
            processed_document_sections.append(new_section)
            # Map the heading name to the position in the list
            heading_to_index[heading_key] = len(processed_document_sections) - 1

        # Get the correct section object and add the chunk with a label
        section_idx = heading_to_index[heading_key]
        chunk_count = len(processed_document_sections[section_idx]["Chunks"]) + 1
        
        # Add the chunk in "Chunk X: Text" format
        chunk_entry = f"Chunk {chunk_count}: {text_in_chunk}"
        processed_document_sections[section_idx]["Chunks"].append((chunk_count, text_in_chunk))

    return processed_document_sections

def merge_content_by_refined_headings(processed_document_texts, refined_headings):
    """
    Merges text blocks with invalid headings into the previous valid block.
    Comparison is case-insensitive.
    """
    updated_texts = []
    # Pre-normalize refined headings to lowercase for faster, consistent comparison
    refined_lower = [h.lower().strip() for h in refined_headings]

    def is_similar(a, b, threshold=0.9):
    # Ratio returns a value between 0 and 1
        return SequenceMatcher(None, a, b).ratio() >= threshold 
    
    for text_block in processed_document_texts:
        # Split into heading (line 0) and the rest of the content
        parts = text_block.split('\n', 1)
        current_heading = parts[0].strip()
        current_heading_lower = current_heading.lower()
        content = parts[1] if len(parts) > 1 else ""
        
        # Logic Check: Is the current heading (or a part of it) in the refined list?
        # We check both directions: 'refined in current' and 'current in refined'
        is_valid = any(
            is_similar(current_heading_lower, ref, 0.9) 
            for ref in refined_lower
        )
        
        if is_valid and current_heading_lower != "n/a":
            # This is a valid section, add it to the list as a new entry
            updated_texts.append(text_block)
        else:
            # This is an invalid heading (e.g., 'PAGE 5' or 'n/a').
            # Append its content to the last valid entry if one exists.
            if updated_texts:
                # print(f"Merging content from invalid heading '{current_heading}' (Case-Insensitive Match Failed)\n \n compared texts: \n {current_heading_lower} in {refined_lower}")
                # Append only the content (skip the invalid heading line)
                updated_texts[-1] += "\n" + content
            else:
                # Fallback: If it's the very first block, keep it to avoid losing data
                updated_texts.append(text_block)
                
    return updated_texts

def merge_content_by_refined_headings_Chunks(Sections_Raw_Chunk_text,refined_headings):
    """
    Merges text blocks with invalid headings into the previous valid block 
    and returns a structured JSON-style list of dictionaries.
    """
    refined_json_output = []
    refined_lower = [h.lower().strip() for h in refined_headings]

    def is_similar(a, b, threshold=0.9):
        return SequenceMatcher(None, a, b).ratio() >= threshold 
    
    # We iterate through the raw grouped sections you created in the previous step
    for section in Sections_Raw_Chunk_text:
        current_heading = section["Section Heading"].strip()
        current_heading_lower = current_heading.lower()
        current_chunks = section["Chunks"] # This is the list of ["Chunk 1: ...", "Chunk 2: ..."]
        
        # Check if the heading is valid based on the refined list
        is_valid = any(
            is_similar(current_heading_lower, ref, 0.9) 
            for ref in refined_lower
        )
        
        if is_valid and current_heading_lower != "n/a":
            # Start a new valid section entry
            new_section = {
                "Section Heading": current_heading,
                "Chunks": current_chunks
            }
            refined_json_output.append(new_section)
        else:
            # INVALID HEADING logic: Merge these chunks into the previous valid section
            if refined_json_output:
                # print(f"Merging content from invalid heading '{current_heading}' into '{refined_json_output[-1]['Section Heading']}'")
                
                # Get the last valid section's chunk list
                last_chunks_list = refined_json_output[-1]["Chunks"]
                
                # Add the chunks from the invalid section to the end of the previous list
                # We re-index the "Chunk X" labels to keep them continuous
                for _, text in current_chunks:
                    # Re-calculate the index based on the new parent list length
                    new_index = len(last_chunks_list) + 1
                    last_chunks_list.append((new_index, text))
            else:
                # Fallback: If no valid section exists yet, keep this as the first section
                refined_json_output.append({
                    "Section Heading": current_heading,
                    "Chunks": current_chunks
                })
                
    return refined_json_output

def categorize_sections(refined_Sections_Raw_chunks, final_merged_content, body_headings, excel_data):
    """
    Maps content to standard columns and includes the specific chunk list 
    for each section in the final JSON.
    """
    processed_json = []
    body_headings_lower = [h.lower().strip() for h in body_headings]

    # Map refined_Sections_Raw_chunks by heading for O(1) lookup
    # This ensures we can quickly find the chunks for a given header
    chunks_lookup = {
        item["Section Heading"].strip().lower(): item["Chunks"] 
        for item in refined_Sections_Raw_chunks
    }

    for text_block in final_merged_content:
        # Split block into header and the actual content
        lines = text_block.split('\n')
        header = lines[0].strip()
        content = "\n".join(lines[1:]).strip()
        header_lower = header.lower()

        # Retrieve the chunks associated with this heading from our lookup
        # If not found, default to an empty list
        section_chunks = chunks_lookup.get(header_lower, [])

        # Create the updated JSON object structure
        section_entry = {
            "Section Name": header,
            "Text_Content": content,
            "Chunks": section_chunks
        }
        processed_json.append(section_entry)

        # --- Excel Categorization Logic ---
        matched_standard = False
        for column_name, keywords in HEADER_MAPPING.items():
            if any(key in header_lower for key in keywords):
                excel_data[column_name] = "Yes"
                matched_standard = True
                break
        
        if not matched_standard:
            # Check if it belongs in Body or Remaining
            if any(bh in header_lower or header_lower in bh for bh in body_headings_lower):
                excel_data["Body"].append(header)
            else:
                excel_data["Remaining"].append(header)
    
    return processed_json, excel_data

def save_excel_log(excel_log_path, excel_data,Time_taken_4_chunking,Time_taken_4_section_processing):
    """Handles persistence of the Excel log file."""

    excel_data["Chunking Time"] = Time_taken_4_chunking
    excel_data["Sectioning Time"] = Time_taken_4_section_processing
    excel_data["Body"] = ", ".join(excel_data["Body"])
    excel_data["Remaining"] = ", ".join(excel_data["Remaining"])
    
    df_new = pd.DataFrame([excel_data])
    if os.path.exists(excel_log_path):
        df_existing = pd.read_excel(excel_log_path)
        df_final = pd.concat([df_existing, df_new], ignore_index=True)
    else:
        df_final = df_new
    
    df_final.to_excel(excel_log_path, index=False)
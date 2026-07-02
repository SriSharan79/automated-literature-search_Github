
import os
import re
from colorama import Fore
from docling.document_converter import DocumentConverter
from docling.chunking import HybridChunker
from docling.datamodel.document import TextItem, DocItem # Import DocItem for general check
import json # For pretty printing dictionary-like objects

# --- Configuration (replace with your actual file path) ---
PDF_FILE_PATH='/home/kata_du/Files_For_Data_Set_Preparation/FCT_output/outputs/failed_pdfs/EASA SC E-19 - Electric Hybrid Propulsion System (EHPS).pdf'
OUTPUT_FILE_NAME = "/localdata/user/kata_du/Files_For_Data_Set_Preparation/FCT_output/outputs/docling_debug_list_items.txt"

# --- Open the output file ---
# Using 'with' statement ensures the file is automatically closed
with open(OUTPUT_FILE_NAME, 'w', encoding='utf-8') as outfile:

    # --- Helper function to write to both console and file (optional, but good for real-time monitoring) ---
    def log_print(message):
        print(message)         # Print to console
        outfile.write(message + '\n') # Write to file with a newline

    log_print(f"--- Docling Debugging Output ---")
    log_print(f"Output is being saved to: {os.path.abspath(OUTPUT_FILE_NAME)}\n")

    text_doc_item_texts = []

    # --- Document Conversion and Chunking ---
    converter = DocumentConverter()
    try:
        doc = converter.convert(PDF_FILE_PATH).document
    except FileNotFoundError:
        log_print(f"Error: PDF file not found at '{PDF_FILE_PATH}'. Please check the path.")
        exit()
    except Exception as e:
        log_print(f"An error occurred during document conversion: {e}")
        exit()

    chunker = HybridChunker()
    try:
        chunks = chunker.chunk(dl_doc=doc)
    except Exception as e:
        print(f"An error occurred during chunking: {e}")
        exit()

    paragraph_texts = []

    log_print("\n--- Starting Debugging Output for Chunks ---")
    # log_print(f"Total number of chunks to process: {len(chunks)}\n")

    for i, chunk in enumerate(chunks):
        log_print(f"\n===== Processing Chunk {i + 1} =====")
        log_print(Fore.LIGHTBLUE_EX + f"Raw Text:\n{chunk.text}" + Fore.RESET)

        # --- Debugging Chunk Meta Information ---
        log_print("\n--- Chunk Meta (DocMeta) Information ---")
        try:
            meta_dict = chunk.meta.model_dump() # Or chunk.meta.dict() for older Pydantic versions
            log_print(json.dumps(meta_dict, indent=2))
        except AttributeError:
            log_print(f"Warning: Could not use .model_dump() on chunk.meta. Attempting direct access.")
            log_print(f"Schema Name: {getattr(chunk.meta, 'schema_name', 'N/A')}")
            log_print(f"Version: {getattr(chunk.meta, 'version', 'N/A')}")
            log_print(f"Headings: {getattr(chunk.meta, 'headings', 'N/A')}")
            log_print(f"Captions (Deprecated): {getattr(chunk.meta, 'captions', 'N/A')}")
            log_print(f"Origin: {getattr(chunk.meta, 'origin', 'N/A')}")
        except Exception as e:
            log_print(f"Error dumping chunk meta: {e}")

        log_print(f"\n--- DocItems in Chunk {i + 1} ---")
        if not hasattr(chunk.meta, 'doc_items') or not chunk.meta.doc_items:
            log_print("No 'doc_items' attribute or no doc_items found in this chunk's meta.")
            if hasattr(chunk.meta, 'doc_items') and not chunk.meta.doc_items:
                 log_print("(The doc_items list was empty.)")
        else:
            for j, doc_item in enumerate(chunk.meta.doc_items):
                log_print(f"  --- DocItem {j + 1} (Type: {type(doc_item).__name__}, Label: {getattr(doc_item, 'label', 'N/A')}) ---")
                current_label = getattr(doc_item, 'label', 'N/A')
                self_ref = getattr(doc_item, 'self_ref', None)
                actual_text_content = None # Initialize to None

                # Check if the label is 'text' AND if it's a TextItem (to ensure it has a .text attribute)
                if current_label == 'list_item':
                    text_doc_item_texts.append(doc_item)
                    # Optional: You can also log this extraction if log_print is available
                    log_print(f"    >>> Found and added TextItem with label 'text': '{doc_item}...' <<<")
                    self_ref = getattr(doc_item, 'self_ref', None)

                    if self_ref:
                        # Regex to extract the index from the self_ref, e.g., '#/texts/91' -> '91'
                        match = re.match(r'#/texts/(\d+)', self_ref)
                        if match:
                            text_index = int(match.group(1))
                            try:
                                # Access the actual Text object from the document's texts list
                                # And then extract its 'text' attribute
                                actual_text_content = doc.texts[text_index].text
                                text_doc_item_texts.append(actual_text_content)
                                # Optional: Log the extraction if log_print is available
                                log_print(f"    >>> Found and added Text from self_ref='{self_ref}': '{actual_text_content}...' <<<")
                            except IndexError:
                                # This handles cases where the index might be out of bounds
                                log_print(f"    Warning: text_index {text_index} out of bounds for doc.texts (self_ref: {self_ref}).")
                            except AttributeError:    
                                # This handles cases where doc.texts[text_index] might not have a .text attribute
                                # (e.g., if it's not a Text object as expected)
                                log_print(f"    Warning: Object at doc.texts[{text_index}] does not have a .text attribute (self_ref: {self_ref}).")
                        else:
                            log_print(f"    Warning: Could not parse text index from self_ref: {self_ref}")
                    else:
                        log_print(f"    Warning: doc_item with label 'text' has no 'self_ref' attribute.")
                else:
                    # Optional: If you want to log doc_items that are not 'text' or don't meet criteria
                    log_print(f"    DocItem (Label: '{current_label}') not 'text' or missing self_ref.")



                # General attributes common to many DocItem types (if they exist)
                log_print(f"    UUID: {getattr(doc_item, 'uuid', 'N/A')}")
                log_print(f"    Page Number: {getattr(doc_item, 'page_number', 'N/A')}")
                log_print(f"    BBox: {getattr(doc_item, 'bbox', 'N/A')}")

                if isinstance(doc_item, TextItem):
                    log_print(f"    Is TextItem: True")
                    log_print(f"    TextItem Label: {doc_item.label}")
                    log_print(f"    Text Length: {len(doc_item.text)} characters")
                    log_print(f"    Text (first 200 chars): \"{doc_item.text[:200]}{'...' if len(doc_item.text) > 200 else ''}\"")

                    if doc_item.label == 'paragraph':
                        paragraph_texts.append(doc_item.text)
                        log_print(f"    >>> Added to paragraph_texts <<<")
                    else:
                        log_print(f"    (Not a paragraph, label is '{doc_item.label}')")
                else:
                    log_print(f"    Is TextItem: False")
                    # Add specific debugging for other DocItem types if needed
                    # e.g., if isinstance(doc_item, TableItem): log_print(f"    Table Data: ...")

    log_print("\n--- End of Debugging Output ---")

    log_print("\n--- Extracted Paragraph Texts ---")
    if paragraph_texts:
        for k, text in enumerate(paragraph_texts):
            log_print(f"\nParagraph {k + 1}:")
            log_print(text)
            log_print("-" * 70)
    else:
        log_print("No paragraph text was extracted.")

    log_print(f"\nTotal paragraphs extracted: {len(paragraph_texts)}")

log_print(f"\nDebugging process complete. Output saved to '{OUTPUT_FILE_NAME}'.")
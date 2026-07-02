import os
import json
import logging
import tkinter as tk
from tkinter import messagebox, scrolledtext
from colorama import Fore, Style, init
from docling.document_converter import DocumentConverter
from docling.chunking import HybridChunker

# --- Initialize Colorama for background processing logs ---
init(autoreset=True)

file_name='CM-21.A-004 - Acceptable Approaches for the Certification of Electric_Hybrid Propulsion Systems; 2024'

# --- Configuration ---
PDF_FILE_PATH='/home/kata_du/Files_For_Data_Set_Preparation/01_Regularien, Normen/EASA/CM (Certification Memorandum)/CM-21.A-004 - Acceptable Approaches for the Certification of Electric_Hybrid Propulsion Systems; 2024.pdf'

OUTPUT_FILE_NAME = f"/localdata/user/kata_du/Files_For_Data_Set_Preparation/FCT_output/outputs/{file_name}/docling_logged_chunks.json"
CHUNKS_CACHE_FILE = f"/localdata/user/kata_du/Files_For_Data_Set_Preparation/FCT_output/outputs/{file_name}/docling_chunks_cache.json"
LOG_FILE_NAME = f"/localdata/user/kata_du/Files_For_Data_Set_Preparation/FCT_output/outputs/{file_name}/docling_execution.log"

# --- Ensure Output Directory Exists ---
log_dir = os.path.dirname(LOG_FILE_NAME)
if log_dir and not os.path.exists(log_dir):
    os.makedirs(log_dir, exist_ok=True)

# --- Python Logger Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE_NAME, encoding='utf-8')
    ]
)
logger = logging.getLogger("DoclingDataExtractorUI")


class ChunkReviewApp:
    def __init__(self, root, chunks_data, logged_chunks, processed_indices):
        self.root = root
        self.chunks_data = chunks_data
        self.logged_chunks = logged_chunks
        self.processed_indices = processed_indices
        
        self.current_pointer = 0
        
        # Configure Main Window
        self.root.title("Docling Chunk Review & Curation Tool")
        self.root.geometry("850x700")
        self.root.minsize(750, 600)
        
        # UI Styling Elements
        self.bg_color = "#f4f4f6"
        self.card_color = "#ffffff"
        self.root.configure(bg=self.bg_color)
        
        self.setup_ui()
        self.advance_to_next_unprocessed()
        self.display_current_chunk()

    def setup_ui(self):
        # Top Metadata Frame
        meta_frame = tk.LabelFrame(self.root, text=" Chunk Metadata ", bg=self.card_color, font=("Arial", 10, "bold"), padx=15, pady=10)
        meta_frame.pack(fill="x", padx=15, pady=10)
        
        # Progress Tracking Label
        self.progress_label = tk.Label(meta_frame, text="Chunk: 0 / 0", font=("Arial", 11, "bold"), bg=self.card_color, fg="#2c3e50")
        self.progress_label.grid(row=0, column=0, sticky="w", pady=2)
        
        self.type_label = tk.Label(meta_frame, text="Chunk Type: N/A", font=("Arial", 10), bg=self.card_color)
        self.type_label.grid(row=1, column=0, sticky="w", pady=2)
        
        self.pages_label = tk.Label(meta_frame, text="Page Number(s): N/A", font=("Arial", 10), bg=self.card_color)
        self.pages_label.grid(row=2, column=0, sticky="w", pady=2)
        
        self.docitem_label = tk.Label(meta_frame, text="DocItem Elements: N/A", font=("Arial", 10), bg=self.card_color)
        self.docitem_label.grid(row=3, column=0, sticky="w", pady=2)

        # Headings Editing Frame
        heading_frame = tk.Frame(self.root, bg=self.bg_color)
        heading_frame.pack(fill="x", padx=15, pady=5)
        
        tk.Label(heading_frame, text="Headings (Comma-separated list):", font=("Arial", 10, "bold"), bg=self.bg_color).pack(anchor="w")
        self.headings_entry = tk.Entry(heading_frame, font=("Arial", 10), bd=2, relief="groove")
        self.headings_entry.pack(fill="x", pady=2)

        # Text Content Editing Frame
        content_frame = tk.Frame(self.root, bg=self.bg_color)
        content_frame.pack(fill="both", expand=True, padx=15, pady=5)
        
        tk.Label(content_frame, text="Chunk Text Content (Editable):", font=("Arial", 10, "bold"), bg=self.bg_color).pack(anchor="w")
        self.text_area = scrolledtext.ScrolledText(content_frame, font=("Consolas", 10), wrap=tk.WORD, bd=2, relief="groove")
        self.text_area.pack(fill="both", expand=True, pady=2)

        # Bottom Action Control Frame
        btn_frame = tk.Frame(self.root, bg=self.bg_color, pady=10)
        btn_frame.pack(fill="x", padx=15)
        
        self.btn_log = tk.Button(btn_frame, text="Log Chunk (Save)", font=("Arial", 10, "bold"), bg="#27ae60", fg="white", width=16, command=self.log_chunk, relief="raised", bd=3)
        self.btn_log.pack(side="left", padx=5)
        
        self.btn_skip = tk.Button(btn_frame, text="Skip Chunk", font=("Arial", 10, "bold"), bg="#e67e22", fg="white", width=14, command=self.skip_chunk, relief="raised", bd=3)
        self.btn_skip.pack(side="left", padx=5)

        # Button to copy the previous chunk's heading into the present chunk's entry field
        self.btn_use_prev = tk.Button(btn_frame, text="Use Prev Heading", font=("Arial", 10, "bold"), bg="#34495e", fg="white", width=16, command=self.use_previous_heading, relief="raised", bd=3)
        self.btn_use_prev.pack(side="left", padx=5)

        # Button to completely wipe the logged JSON data and restart from the first chunk
        self.btn_reset = tk.Button(btn_frame, text="Reset & Restart", font=("Arial", 10, "bold"), bg="#c0392b", fg="white", width=16, command=self.reset_all_progress, relief="raised", bd=3)
        self.btn_reset.pack(side="left", padx=5)
        
        self.btn_close = tk.Button(btn_frame, text="Exit Session", font=("Arial", 10), bg="#95a5a6", fg="white", width=12, command=self.root.destroy)
        self.btn_close.pack(side="right", padx=5)

    def advance_to_next_unprocessed(self):
        """Finds the next index that isn't already logged in previous application sessions."""
        while self.current_pointer < len(self.chunks_data):
            chunk_idx = self.chunks_data[self.current_pointer]["chunk_index"]
            if chunk_idx in self.processed_indices:
                logger.info(f"Chunk {chunk_idx} bypassed automatically via Resume Checkpoint.")
                self.current_pointer += 1
            else:
                break

    def display_current_chunk(self):
        if self.current_pointer >= len(self.chunks_data):
            logger.info("All document chunks completed.")
            messagebox.showinfo("Curation Complete", "All chunks have been successfully reviewed and processed!")
            self.root.destroy()
            return
        
        # Load item into UI fields
        chunk = self.chunks_data[self.current_pointer]
        
        self.progress_label.config(text=f"Chunk Summary: {chunk['chunk_index']} / {len(self.chunks_data)}")
        self.type_label.config(text=f"Chunk Type: {chunk['type']}")
        self.pages_label.config(text=f"Page Number(s): {chunk['page_num']}")
        self.docitem_label.config(text=f"DocItem Elements: {chunk['type_of_docitem']}")
        
        # Populate text areas
        self.headings_entry.delete(0, tk.END)
        
        # Safe fallback logic to handle lists, strings, or None values cleanly
        heading_val = chunk.get("heading")
        if isinstance(heading_val, list):
            headings_text = ", ".join(str(h) for h in heading_val if h)
        elif isinstance(heading_val, str):
            headings_text = heading_val
        else:
            headings_text = ""
            
        self.headings_entry.insert(0, headings_text)
        
        self.text_area.delete("1.0", tk.END)
        self.text_area.insert(tk.END, chunk["chunk_text"])
        
        logger.info(f"UI rendering Chunk index: {chunk['chunk_index']}")

    def save_progress_to_json(self, status, headings, text_body):
        chunk = self.chunks_data[self.current_pointer]
        chunk_idx = chunk["chunk_index"]
        
        log_entry = {
            "chunk_index": chunk_idx,
            "status": status,
            "heading": headings,
            "chunk_text": text_body,
            "type_of_docitem": chunk["type_of_docitem"],
            "page_num": chunk["page_num"]
        }
        
        self.logged_chunks.append(log_entry)
        try:
            with open(OUTPUT_FILE_NAME, 'w', encoding='utf-8') as outfile:
                json.dump(self.logged_chunks, outfile, indent=4, ensure_ascii=False)
            logger.info(f"Chunk {chunk_idx} written to output JSON with status: '{status}'")
        except Exception as e:
            logger.error(f"Critical disk write error for chunk {chunk_idx}: {e}")

    def log_chunk(self):
        # Fetch current UI inputs
        raw_headings = self.headings_entry.get().strip()
        headings_list = [h.strip() for h in raw_headings.split(",") if h.strip()]
        edited_text = self.text_area.get("1.0", tk.END).rstrip("\n")
        
        original_chunk = self.chunks_data[self.current_pointer]
        
        # Determine if content was customized by user
        if headings_list != original_chunk["heading"] or edited_text != original_chunk["chunk_text"]:
            status = "logged (edited)"
        else:
            status = "logged"
            
        self.save_progress_to_json(status, headings_list, edited_text)
        
        # Step Forward
        self.current_pointer += 1
        self.advance_to_next_unprocessed()
        self.display_current_chunk()

    def skip_chunk(self):
        # Fetch current UI text variants to make sure skipped text is accurately appended
        raw_headings = self.headings_entry.get().strip()
        headings_list = [h.strip() for h in raw_headings.split(",") if h.strip()]
        current_text = self.text_area.get("1.0", tk.END).rstrip("\n")
        
        self.save_progress_to_json("skipped", headings_list, current_text)
        
        # Step Forward
        self.current_pointer += 1
        self.advance_to_next_unprocessed()
        self.display_current_chunk()

    def replace_previous_heading(self):
        """Takes the text currently typed inside the headings entry box and 
        overwrites the heading list of the last saved chunk in the JSON file."""
        if not self.logged_chunks:
            messagebox.showwarning("Action Invalid", "There is no previously logged chunk in this session to modify.")
            return

        # Parse what is currently typed inside the entry box
        raw_headings = self.headings_entry.get().strip()
        headings_list = [h.strip() for h in raw_headings.split(",") if h.strip()]

        # Target the last element appended to your log list
        previous_entry = self.logged_chunks[-1]
        target_idx = previous_entry["chunk_index"]
        
        # Modify the values
        previous_entry["heading"] = headings_list
        if "edited" not in previous_entry["status"] and previous_entry["status"] != "skipped":
            previous_entry["status"] = "logged (edited)"

        # Rewrite the progress file instantly
        try:
            with open(OUTPUT_FILE_NAME, 'w', encoding='utf-8') as outfile:
                json.dump(self.logged_chunks, outfile, indent=4, ensure_ascii=False)
            
            logger.info(f"Retroactively changed heading for Chunk {target_idx} to: {headings_list}")
            messagebox.showinfo("Success", f"Heading for Chunk {target_idx} was overwritten successfully!")
        except Exception as e:
            logger.error(f"Failed retroactively updating heading for chunk {target_idx}: {e}")
            messagebox.showerror("File Error", f"Could not update JSON file: {e}")

    def use_previous_heading(self):
        """Replaces the present heading input text field with the heading string 
        extracted from the immediately preceding logged chunk."""
        if not self.logged_chunks:
            messagebox.showwarning("Action Invalid", "There is no previously logged chunk in this session to copy a heading from.")
            return

        # Fetch the heading list of the last logged item
        last_logged_heading = self.logged_chunks[-1]["heading"]
        heading_str = ", ".join(last_logged_heading)

        # Clear and overwrite the present text entry field
        self.headings_entry.delete(0, tk.END)
        self.headings_entry.insert(0, heading_str)
        
        logger.info(f"Present heading field overwritten with previous heading values: {last_logged_heading}")

    def reset_all_progress(self):
        """Clears all logged chunk history, rewrites the progress JSON file from the 
        start as an empty dataset, and resets the viewer loop back to Chunk 1."""
        confirm = messagebox.askyesno(
            "Confirm System Reset", 
            "Are you sure you want to clear all current entries and rewrite the progress file completely from the start?\n\nThis will permanently clear your choices.",
            icon="warning"
        )
        if not confirm:
            return

        # Clear active memory state indices
        self.logged_chunks.clear()
        self.processed_indices.clear()
        self.current_pointer = 0

        # Erase progress by rewriting an empty structure over the target file path
        try:
            with open(OUTPUT_FILE_NAME, 'w', encoding='utf-8') as outfile:
                json.dump([], outfile, indent=4, ensure_ascii=False)
            
            logger.info("User explicitly triggered full sequence reset. Output tracking JSON initialized to empty array.")
            messagebox.showinfo("Reset Complete", "The progress log file was cleared successfully. Restarting execution from Chunk 1.")
            
            # Re-sync view to display the original first chunk
            self.display_current_chunk()
        except Exception as e:
            logger.error(f"Failed clearing progress file target: {e}")
            messagebox.showerror("File IO Error", f"Could not reset log data file: {e}")


def main():
    logger.info("==================================================")
    logger.info(f"GUI Run Initialization for: {PDF_FILE_PATH}")
    print(Fore.GREEN + f"System logger actively writing background tracking entries to '{LOG_FILE_NAME}'.\n")

    # --- Load User Log Progress ---
    logged_chunks = []
    processed_indices = set()
    if os.path.exists(OUTPUT_FILE_NAME):
        try:
            with open(OUTPUT_FILE_NAME, 'r', encoding='utf-8') as infile:
                logged_chunks = json.load(infile)
                for entry in logged_chunks:
                    if "chunk_index" in entry:
                        processed_indices.add(entry["chunk_index"])
            print(Fore.CYAN + f">>> Resume validation: found {len(processed_indices)} handled elements.")
        except Exception as e:
            logger.warning(f"Failed to read existing progress log: {e}")

    # --- Chunk Caching / Docling Extraction Core ---
    chunks_data = []
    if os.path.exists(CHUNKS_CACHE_FILE):
        print(Fore.GREEN + Style.BRIGHT + f">>> Fetching layout structural entries from cache: '{CHUNKS_CACHE_FILE}'")
        try:
            with open(CHUNKS_CACHE_FILE, 'r', encoding='utf-8') as cache_file:
                chunks_data = json.load(cache_file)
        except Exception as e:
            logger.warning(f"Failed to parse cache file: {e}")

    if not chunks_data:
        print(Fore.YELLOW + "Cache missing. Running backend conversion pipelines via Docling...")
        try:
            converter = DocumentConverter()
            doc = converter.convert(PDF_FILE_PATH).document
            raw_chunks = list(HybridChunker().chunk(dl_doc=doc))
            
            for i, chunk in enumerate(raw_chunks):
                headings = getattr(chunk.meta, 'headings', [])
                doc_items = getattr(chunk.meta, 'doc_items', [])
                doc_item_types = [type(item).__name__ for item in doc_items]
                doc_item_labels = [getattr(item, 'label', 'N/A') for item in doc_items]
                page_numbers = list(set([getattr(item, 'page_number', 'N/A') for item in doc_items]))
                
                chunks_data.append({
                    "chunk_index": i + 1,
                    "type": type(chunk).__name__,
                    "heading": headings,
                    "chunk_text": chunk.text,
                    "type_of_docitem": doc_item_labels if doc_item_labels else doc_item_types,
                    "page_num": page_numbers
                })
            
            with open(CHUNKS_CACHE_FILE, 'w', encoding='utf-8') as cache_file:
                json.dump(chunks_data, cache_file, indent=4, ensure_ascii=False)
            print(Fore.GREEN + "Backend parsing complete. Structure saved to cache.")
        except Exception as e:
            logger.critical(f"Docling parsing execution crashed: {e}")
            print(Fore.RED + f"Critical Exception: {e}")
            return

    # --- Trigger UI Interaction Engine ---
    if chunks_data:
        root = tk.Tk()
        app = ChunkReviewApp(root, chunks_data, logged_chunks, processed_indices)
        root.mainloop()
    else:
        print(Fore.RED + "Error: No data available for processing.")


if __name__ == "__main__":
    main()
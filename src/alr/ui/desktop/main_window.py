import os
import re
import sys
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
# --- Import your existing backend logic ---
from alr.common.excel_utils import extract_column, get_values_from_sorted_numbers, get_values_from_sorted_numbers_and_save
from alr.common.file_manager import CollectionManager, DataAnalyzeManager, Vec_DB_Manager
from alr.common.general_utils import clean_folder_path, generate_unique_id
from alr.collection.search_phrase_generator_logger import log_Keyword_Json
from alr.collection.search_phrase_generator_utils import Keywords_Processing_with_scope, run_scholarly
from alr.collection.collection_system_prompts import KEYWORD_GENERATOR_PROMPT, SCOPE_DERIVATOR_PROMPT

from alr.common.general_utils import Proccess_string_to_list
from alr.common.llm_utils import (
    list_available_models, set_selected_model, get_selected_model,
    list_embedding_models, set_selected_embedding_model, set_embedding_backend,
    local_embedding_model_dir, embedding_model_repo_id,
)
from alr.common.LLM_Config import get_stored_api_key, set_api_key, KEY_ENV_NAMES
from alr.common.sql_store import sync_storage_to_sql
from alr.common import crash_logger
from alr.ui.desktop.review_app import open_review_app, ProgressDialog
from alr.ui.desktop.section_rewriter_view import JSONRestructurerUI, open_section_editor_window
from alr.data_analysis.Pdf_File_processor import process_pdf_mode_file
from alr.rag_builders.db_manager import generate_databases
from alr.rag_builders.query_executor import generate_query_report
from alr.ui.cli.Data_analysis_UI import analyse_pdf_input_path


class CustomTerminalText(tk.Text):
    """Custom Text widget to redirect stdout/stderr to the GUI window.

    Thread-safe: ``write`` may be called from worker threads (background analysis
    prints), so text is enqueued and inserted on the main thread by a poller.

    ANSI-aware: the backend uses ``colorama`` (``Fore.*`` / ``Style.*``), which
    emits raw ANSI SGR escape codes. These are parsed here and rendered as Tk
    text tags so the console shows the same colour coding as a real terminal.
    """

    # ANSI SGR escape sequence, e.g. "\x1b[34m" or "\x1b[1;44m".
    _ANSI_RE = re.compile(r"\x1b\[([0-9;]*)m")

    # SGR colour code -> readable colour on a black background.
    _FG = {
        30: "#7f7f7f", 31: "#ff5555", 32: "#55dd55", 33: "#e5e510",
        34: "#5c9dff", 35: "#ff6eff", 36: "#55dddd", 37: "#e6e6e6",
        90: "#a0a0a0", 91: "#ff8888", 92: "#88ff88", 93: "#ffff88",
        94: "#88bbff", 95: "#ff9cff", 96: "#88ffff", 97: "#ffffff",
    }
    _BG = {
        40: "#000000", 41: "#7a0000", 42: "#007a00", 43: "#7a7a00",
        44: "#00337a", 45: "#7a007a", 46: "#007a7a", 47: "#c0c0c0",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._write_queue = queue.Queue()
        self._configure_ansi_tags()
        # Currently active foreground/background/bright state carried across writes.
        self._fg_tag = None
        self._bg_tag = None
        self._bright = False
        self._poll_write_queue()

    def _configure_ansi_tags(self):
        for code, colour in self._FG.items():
            self.tag_configure(f"fg{code}", foreground=colour)
        for code, colour in self._BG.items():
            self.tag_configure(f"bg{code}", background=colour)
        self.tag_configure("bright", font=("Courier New", 10, "bold"))

    def write(self, string):
        # Called from any thread; marshal to the main thread via a queue.
        self._write_queue.put(string)

    def _active_tags(self):
        tags = []
        if self._fg_tag:
            tags.append(self._fg_tag)
        if self._bg_tag:
            tags.append(self._bg_tag)
        if self._bright:
            tags.append("bright")
        return tuple(tags)

    def _apply_sgr(self, params):
        """Update the active colour state from one SGR escape's parameters."""
        codes = [int(p) if p else 0 for p in params.split(";")] if params else [0]
        for c in codes:
            if c == 0:                     # reset all
                self._fg_tag = self._bg_tag = None
                self._bright = False
            elif c == 1:                   # bright / bold
                self._bright = True
            elif c == 22:                  # normal intensity
                self._bright = False
            elif c == 39:                  # default foreground
                self._fg_tag = None
            elif c == 49:                  # default background
                self._bg_tag = None
            elif c in self._FG:
                # Promote a standard colour to its bright variant when bold is set.
                resolved = c + 60 if self._bright and 30 <= c <= 37 else c
                self._fg_tag = f"fg{resolved if resolved in self._FG else c}"
            elif c in self._BG:
                self._bg_tag = f"bg{c}"

    def _insert_ansi(self, text):
        """Insert ``text``, converting ANSI SGR codes into Tk tag styling."""
        idx = 0
        for m in self._ANSI_RE.finditer(text):
            if m.start() > idx:
                self.insert(tk.END, text[idx:m.start()], self._active_tags())
            self._apply_sgr(m.group(1))
            idx = m.end()
        if idx < len(text):
            self.insert(tk.END, text[idx:], self._active_tags())

    def _poll_write_queue(self):
        try:
            while True:
                self._insert_ansi(self._write_queue.get_nowait())
                self.see(tk.END)
        except queue.Empty:
            pass
        except tk.TclError:
            return  # widget destroyed
        self.after(50, self._poll_write_queue)

    def flush(self):
        pass


class AutomatedLiteratureUI(tk.Tk):
    def __init__(self):
        super().__init__()

        # Any crash (main thread, Tk callback, background thread) writes a
        # timestamped traceback into ~/Automated Literature Review/00_Crash_Logs.
        crash_logger.install("Automated Literature Review Support Tool")
        crash_logger.attach_to_tk(self)

        self.title("Automated Literature Review Support Tool")
        self.geometry("900x750")
        self.username = os.environ.get("USERNAME", "User")
        
        # Track active manager objects
        self.CM = None
        self.MF = None

        self._create_widgets()
        
        # Redirect stdout to our custom GUI terminal widget
        sys.stdout = self.terminal_output
        sys.stderr = self.terminal_output

        print(f"Welcome, {self.username}! Application Initialized.")

    def _create_widgets(self):
        # Top bar: greeting + global actions (fixed height at the top).
        top_bar = tk.Frame(self)
        top_bar.pack(fill="x", pady=10)
        greeting_lbl = tk.Label(top_bar, text=f"Hello, {self.username}! Automated Literature Review Support Tool", font=("Arial", 12, "bold"))
        greeting_lbl.pack(side="left", padx=10)
        ttk.Button(top_bar, text="API Keys...", command=self._manage_api_keys_action).pack(side="right", padx=10)
        ttk.Button(top_bar, text="Open Review Tool", command=lambda: open_review_app(self)).pack(side="right", padx=4)

        # Body holds the notebook and the console. They are placed with fixed
        # height fractions so the console always occupies ~40% of the body height
        # (notebook 60% / console 40%), regardless of how tall the tab content is.
        body = tk.Frame(self)
        body.pack(fill="both", expand=True)

        # Tab Control (Notebook) -> top 60%.
        self.notebook = ttk.Notebook(body)
        self.notebook.place(relx=0, rely=0, relwidth=1, relheight=0.6)

        # Build individual Tabs
        self._build_collect_tab()
        self._build_analyze_tab()
        self._build_visualize_tab()
        self._build_section_editor_tab()
        self._build_evaluate_tab()

        # Integrated Console Terminal Output Box -> bottom 40%.
        terminal_frame = tk.LabelFrame(body, text="Console Output Log")
        terminal_frame.place(relx=0, rely=0.6, relwidth=1, relheight=0.4)

        self.terminal_output = CustomTerminalText(terminal_frame, wrap="word", background="black", foreground="white", font=("Courier New", 10))
        scrollbar = ttk.Scrollbar(terminal_frame, command=self.terminal_output.yview)
        self.terminal_output.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        self.terminal_output.pack(side="left", fill="both", expand=True)

    # ==========================================
    # TAB 1: LITERATURE COLLECTION
    # ==========================================
    def _build_collect_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="1. Collect Literature")

        # Storage Path configuration
        path_frame = tk.LabelFrame(tab, text="Data Storage Configuration")
        path_frame.pack(fill="x", padx=10, pady=5)

        self.custom_path_var_col = tk.BooleanVar(value=False)
        chk = ttk.Checkbutton(path_frame, text="Use Custom Storage Path?", variable=self.custom_path_var_col, command=self._toggle_collect_path_btn)
        chk.grid(row=0, column=0, padx=5, pady=5, sticky="w")

        self.collect_path_entry = ttk.Entry(path_frame, width=50, state="disabled")
        self.collect_path_entry.grid(row=0, column=1, padx=5, pady=5)
        
        self.collect_path_btn = ttk.Button(path_frame, text="Browse...", state="disabled", command=lambda: self._browse_folder(self.collect_path_entry))
        self.collect_path_btn.grid(row=0, column=2, padx=5, pady=5)

        # Inputs Frame
        inputs_frame = tk.LabelFrame(tab, text="Research Scope & Details")
        inputs_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(inputs_frame, text="Research Area/Topic:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.ra_entry = ttk.Entry(inputs_frame, width=70)
        self.ra_entry.grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(inputs_frame, text="Research Questions/Gaps:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.rq_entry = ttk.Entry(inputs_frame, width=70)
        self.rq_entry.grid(row=1, column=1, padx=5, pady=5)

        ttk.Label(inputs_frame, text="LLM Provider:").grid(row=2, column=0, padx=5, pady=5, sticky="w")
        self.llm_choice_col = ttk.Combobox(inputs_frame, values=["O", "B"], width=5, state="readonly")
        self.llm_choice_col.set("O")
        self.llm_choice_col.grid(row=2, column=1, padx=5, pady=5, sticky="w")
        ttk.Label(inputs_frame, text="(O = DLR ollama Nimbus Service | B = BlaBla LLM models)").grid(row=2, column=1, padx=70, pady=5, sticky="w")

        ttk.Button(inputs_frame, text="Choose Model...",
                   command=lambda: self._choose_model_action(self.llm_choice_col.get())
                   ).grid(row=3, column=1, padx=5, pady=5, sticky="w")

        # Scope Action Area
        scope_frame = tk.LabelFrame(tab, text="Refined Scope Setup")
        scope_frame.pack(fill="x", padx=10, pady=5)

        btn_derive_scope = ttk.Button(scope_frame, text="Generate Scope via LLM", command=self._generate_scope_action)
        btn_derive_scope.pack(side="left", padx=5, pady=5)

        self.scope_entry = ttk.Entry(scope_frame, width=65)
        self.scope_entry.pack(side="left", padx=5, fill="x", expand=True)

        # Keywords Control Area
        keyword_frame = tk.LabelFrame(tab, text="Keywords & Search Phrase Automation")
        keyword_frame.pack(fill="both", expand=True, padx=10, pady=5)

        kw_action_frame = ttk.Frame(keyword_frame)
        kw_action_frame.pack(fill="x")

        self.suggest_kw_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(kw_action_frame, text="Suggest keywords using LLM?", variable=self.suggest_kw_var).pack(side="left", padx=5, pady=5)

        btn_gen_kw = ttk.Button(kw_action_frame, text="Process Keywords", command=self._process_keywords_action)
        btn_gen_kw.pack(side="left", padx=5, pady=5)

        # Phrase settings 
        ttk.Label(kw_action_frame, text="Top Phrases count:").pack(side="left", padx=20, pady=5)
        self.phrases_count_spin = ttk.Spinbox(kw_action_frame, from_=1, to=100, width=5)
        self.phrases_count_spin.set(10)
        self.phrases_count_spin.pack(side="left", pady=5)

        # Ranking Framework selection
        rank_frame = ttk.Frame(keyword_frame)
        rank_frame.pack(fill="x", pady=2)
        ttk.Label(rank_frame, text="Phrases ranking strategy:").pack(side="left", padx=5)
        self.ranking_var = tk.StringVar(value="1")
        ttk.Radiobutton(rank_frame, text="1. Research Area Basis", variable=self.ranking_var, value="1").pack(side="left", padx=5)
        ttk.Radiobutton(rank_frame, text="2. Research Question Basis", variable=self.ranking_var, value="2").pack(side="left", padx=5)
        ttk.Radiobutton(rank_frame, text="3. Comb. RA + RQ", variable=self.ranking_var, value="3").pack(side="left", padx=5)
        ttk.Radiobutton(rank_frame, text="4. Total Rank (All Inputs)", variable=self.ranking_var, value="4").pack(side="left", padx=5)

        # Executer Execution trigger buttons
        exec_frame = ttk.Frame(keyword_frame)
        exec_frame.pack(fill="x", pady=5)
        
        self.btn_scholarly = ttk.Button(exec_frame, text="Run Scholarly Search", state="disabled", command=lambda: self._execute_search_strategy("s"))
        self.btn_scholarly.pack(side="left", padx=5)

        self.btn_save_excel = ttk.Button(exec_frame, text="Save Ranking to Excel Only", state="disabled", command=lambda: self._execute_search_strategy("e"))
        self.btn_save_excel.pack(side="left", padx=5)

    def _toggle_collect_path_btn(self):
        if self.custom_path_var_col.get():
            self.collect_path_entry.configure(state="normal")
            self.collect_path_btn.configure(state="normal")
        else:
            self.collect_path_entry.configure(state="disabled")
            self.collect_path_btn.configure(state="disabled")

    def _generate_scope_action(self):
        # Move the heavy imports here!
        from alr.collection.collection_system_prompts import SCOPE_DERIVATOR_PROMPT
        from alr.common.llm_utils import llm_call
        
        ra = self.ra_entry.get().strip()
        rq = self.rq_entry.get().strip()
        service = self.llm_choice_col.get()

        if not ra or not rq:
            messagebox.showerror("Error", "Please clarify both Research Area and Research Question items first.")
            return

        if not self._ensure_api_key(service):
            return

        # Setup Collection Manager Object
        if self.custom_path_var_col.get() and self.collect_path_entry.get().strip():
            clean_path = clean_folder_path(self.collect_path_entry.get().strip())
            self.CM = CollectionManager(clean_path)
        else:
            self.CM = CollectionManager()

        self.CM.update_Research_Area(ra)
        self.CM.update_Research_Question(rq)
        self.CM.update_llm_service(service)

        # Handle ID assignment
        topic_id = generate_unique_id(ra, extract_column(self.CM.keywords_list_log_path, 'UUID'))
        self.CM.update_topic_files(topic_id)

        print("\n[LLM System Process] Deriving standard research scope limits definitions...")
        scope_inputs = f"\n1. Research Area/Topic: {ra}\n2. Key Research Questions/Gaps: {rq}"
        derived_scope = llm_call(scope_inputs, SCOPE_DERIVATOR_PROMPT, service)
        
        self.scope_entry.delete(0, tk.END)
        self.scope_entry.insert(0, derived_scope)
        print(f"Scope Derived: {derived_scope}")

    def _process_keywords_action(self):
        from alr.common.llm_utils import llm_call
        if not self.CM:
            messagebox.showerror("Error", "Please initialize the scope config system parameters using 'Generate Scope' first.")
            return

        # Explicitly pull any structural adjustments user may have typed directly into the scope entry line
        refined_scope = self.scope_entry.get().strip()
        self.CM.update_Research_Scope(refined_scope)

        service = self.llm_choice_col.get()
        if not self._ensure_api_key(service):
            return
        keywords_list = []

        if self.suggest_kw_var.get():
            kw_prompt_inputs = f"\n1. Research Area/Topic: {self.CM.Research_Area}\n2. Key Research Questions/Gaps: {self.CM.Research_Question}\n3. Refined Scope: To {refined_scope}"
            raw_keywords = llm_call(kw_prompt_inputs, KEYWORD_GENERATOR_PROMPT, service)
            keywords_list = Proccess_string_to_list(raw_keywords)

            # Open a pop-up window interface for selecting keyword indices if requested
            keywords_list = self._prompt_keyword_indices_selection(keywords_list)
        else:
            # Custom input manual text fallback dialogue context setup box
            manual_input = filedialog.SimpleDialog(self, text="Enter comma-separated keywords:", title="Manual Keywords Choice Input")
            user_string = manual_input.go()
            if user_string:
                keywords_list = [item.strip() for item in user_string.split(",") if item.strip()]

        if not keywords_list:
            print("Action halted or keywords context returned blank frame arrays.")
            return

        self.CM.update_Keyword_list(keywords_list)
        log_Keyword_Json(self.CM)
        self.CM = Keywords_Processing_with_scope(self.CM)

        print(f"\nSuccessfully logged structural pipeline setups. Ready to rank/export across {self.CM.Search_phrase_count} expressions.")
        self.btn_scholarly.configure(state="normal")
        self.btn_save_excel.configure(state="normal")

    def _prompt_keyword_indices_selection(self, original_list):
        # Mini secondary functional frame pop-up window
        dialog = tk.Toplevel(self)
        dialog.title("Refine LLM Suggested Keywords")
        dialog.geometry("500x450")
        dialog.grab_set() # Modal interaction enforcement

        ttk.Label(dialog, text="LLM Suggested keywords list (Check items to KEEP):", font=("Arial", 10, "bold")).pack(pady=5)

        canvas = tk.Canvas(dialog)
        scrollbar = ttk.Scrollbar(dialog, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        checkbox_vars = []
        for index, item in enumerate(original_list):
            var = tk.BooleanVar(value=True) # Default all to selected state
            checkbox_vars.append((var, item))
            chk = ttk.Checkbutton(scrollable_frame, text=f"[{index}] {item}", variable=var)
            chk.pack(anchor="w", padx=10, pady=2)

        canvas.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        scrollbar.pack(side="right", fill="y")

        final_keywords = []

        def _on_confirm():
            nonlocal final_keywords
            final_keywords = [text for var, text in checkbox_vars if var.get()]
            dialog.destroy()

        ttk.Button(dialog, text="Confirm Selected List Strategy", command=_on_confirm).pack(pady=10)
        self.wait_window(dialog)
        return final_keywords

    def _execute_search_strategy(self, choice_mode):
        strategy_map = {"1": "RA_Rank", "2": "RQ_Rank", "3": "RA+RQ_Rank", "4": "TOTAL_Rank"}
        rank_col = strategy_map.get(self.ranking_var.get(), "TOTAL_Rank")
        
        num_phrases = int(self.phrases_count_spin.get())
        
        phrase_excel_file = Path(self.CM.search_phrase_list_excel)
        sp_sorted_path = Path(self.CM.search_phrase_sorted_list_excel)

        sorted_phrases = get_values_from_sorted_numbers(phrase_excel_file, rank_col, 'Phrase', num_phrases)

        if choice_mode == "s":
            print(f"\nRunning Scholarly search framework profiles matching ranking setup: {rank_col}")
            results = run_scholarly(sorted_phrases, self.CM, 15)
            if not results:
                print("Fallback triggered automatically: Saving calculations into target spreadsheet.")
                get_values_from_sorted_numbers_and_save(phrase_excel_file, rank_col, 'Phrase', num_phrases, sp_sorted_path)
        else:
            print(f"\nExtracting top numerical items context vectors targets to location mapping index matching file: {sp_sorted_path}")
            get_values_from_sorted_numbers_and_save(phrase_excel_file, rank_col, 'Phrase', num_phrases, sp_sorted_path)
        
        print("Collection Operations Sequence Completed.")

    # ==========================================
    # TAB 2: LITERATURE ANALYSIS
    # ==========================================
    def _build_analyze_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="2. Analyze Literature")

        # Select file or folder
        path_frame = tk.LabelFrame(tab, text="Input Selection Targets (.pdf format)")
        path_frame.pack(fill="x", padx=10, pady=10)

        self.analysis_input_entry = ttk.Entry(path_frame, width=65)
        self.analysis_input_entry.grid(row=0, column=0, padx=5, pady=10)

        btn_file = ttk.Button(path_frame, text="Select File", command=lambda: self._browse_file(self.analysis_input_entry))
        btn_file.grid(row=0, column=1, padx=2, pady=10)

        btn_folder = ttk.Button(path_frame, text="Select Folder", command=lambda: self._browse_folder(self.analysis_input_entry))
        btn_folder.grid(row=0, column=2, padx=2, pady=10)

        # Storage Management parameters Config settings
        storage_frame = tk.LabelFrame(tab, text="Analysis Storage Config Options")
        storage_frame.pack(fill="x", padx=10, pady=5)

        self.custom_path_var_an = tk.BooleanVar(value=False)
        chk = ttk.Checkbutton(storage_frame, text="Use Custom Storage Folder Path Location?", variable=self.custom_path_var_an, command=self._toggle_analyze_path_btn)
        chk.grid(row=0, column=0, padx=5, pady=5, sticky="w")

        self.analyze_storage_entry = ttk.Entry(storage_frame, width=50, state="disabled")
        self.analyze_storage_entry.grid(row=0, column=1, padx=5, pady=5)
        
        self.analyze_storage_btn = ttk.Button(storage_frame, text="Browse...", state="disabled", command=lambda: self._browse_folder(self.analyze_storage_entry))
        self.analyze_storage_btn.grid(row=0, column=2, padx=5, pady=5)

        # Operational Settings
        llm_frame = ttk.Frame(tab)
        llm_frame.pack(fill="x", padx=10, pady=10)
        ttk.Label(llm_frame, text="LLM Processing Service Engine:").pack(side="left", padx=5)
        self.llm_choice_an = ttk.Combobox(llm_frame, values=["O", "B"], width=5, state="readonly")
        self.llm_choice_an.set("O")
        self.llm_choice_an.pack(side="left", padx=5)
        ttk.Button(llm_frame, text="Choose Model...",
                   command=lambda: self._choose_model_action(self.llm_choice_an.get())
                   ).pack(side="left", padx=5)
        
        
        # Embedding engine used to build/query the vector DBs (shared session
        # setting - the same selector also appears on the Evaluate & Enrich tab
        # and both stay in sync).
        self._build_embedding_selector(llm_frame).pack(fill="x", padx=5, pady=(0, 5))

        # Components to extract (Sections incl. tables/images is required)
        comp_frame = tk.LabelFrame(tab, text="Components to Extract")
        comp_frame.pack(fill="x", padx=10, pady=(5, 0))
        self.comp_vars = {
            "sections": tk.BooleanVar(value=True),
            "abstract": tk.BooleanVar(value=True),
            "intro": tk.BooleanVar(value=True),
            "references": tk.BooleanVar(value=False),
            "doi": tk.BooleanVar(value=True),
            "classification": tk.BooleanVar(value=True),
            "rag": tk.BooleanVar(value=False),
        }
        # Sections (incl. tables/images) is a required prerequisite -> checked + disabled.
        ttk.Checkbutton(comp_frame, text="Sections (incl. tables/images) — required",
                        variable=self.comp_vars["sections"], state="disabled").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        ttk.Checkbutton(comp_frame, text="Abstract", variable=self.comp_vars["abstract"]).grid(row=0, column=1, sticky="w", padx=6, pady=3)
        ttk.Checkbutton(comp_frame, text="Introduction", variable=self.comp_vars["intro"]).grid(row=0, column=2, sticky="w", padx=6, pady=3)
        ttk.Checkbutton(comp_frame, text="References", variable=self.comp_vars["references"]).grid(row=1, column=0, sticky="w", padx=6, pady=3)
        ttk.Checkbutton(comp_frame, text="DOI / metadata", variable=self.comp_vars["doi"]).grid(row=1, column=1, sticky="w", padx=6, pady=3)
        ttk.Checkbutton(comp_frame, text="Classification", variable=self.comp_vars["classification"]).grid(row=1, column=2, sticky="w", padx=6, pady=3)
        ttk.Checkbutton(comp_frame, text="Build Text + Vector DB (RAG)",
                        variable=self.comp_vars["rag"]).grid(row=2, column=0, sticky="w", padx=6, pady=3)

        # Batch options
        batch_frame = ttk.Frame(tab)
        batch_frame.pack(fill="x", padx=10, pady=(0, 5))
        self.skip_dupes_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(batch_frame,
                        text="Skip duplicate titles (fuzzy match against already-analyzed documents)",
                        variable=self.skip_dupes_var).pack(side="left", padx=5)

        # Process Execute
        btn_run_analysis = ttk.Button(tab, text="Execute Document Extraction & Analysis", command=self._run_analysis_action)
        btn_run_analysis.pack(pady=20, ipadx=10, ipady=5)

    def _toggle_analyze_path_btn(self):
        if self.custom_path_var_an.get():
            self.analyze_storage_entry.configure(state="normal")
            self.analyze_storage_btn.configure(state="normal")
        else:
            self.analyze_storage_entry.configure(state="disabled")
            self.analyze_storage_btn.configure(state="disabled")

    def _run_analysis_action(self):
        input_target = self.analysis_input_entry.get().strip()
        service = self.llm_choice_an.get()

        if not input_target:
            messagebox.showerror("Error", "Please input or select a valid target path destination first.")
            return

        if not self._ensure_api_key(service):
            return

        result = analyse_pdf_input_path(input_target, recursive=True)
        print(f"\n[Validation Log Check] Processing input detected structure template parameters: {result.kind}")

        if result.kind not in ("pdf_file", "folder"):
            messagebox.showerror("Error", "Input verified path target structure configuration not recognized or invalid.")
            return

        # Setup Framework Data Storage Analyzer Config instance parameters
        if self.custom_path_var_an.get() and self.analyze_storage_entry.get().strip():
            clean_path = clean_folder_path(self.analyze_storage_entry.get().strip())
            self.MF = DataAnalyzeManager(clean_path)
        else:
            self.MF = DataAnalyzeManager()

        self.MF.update_llm_service(service)
        MF = self.MF
        skip_dupes = self.skip_dupes_var.get()

        # Which components the user chose to extract. Sections (incl. tables/images)
        # is always run as the prerequisite; the rest are optional.
        components = {c for c in ("abstract", "intro", "references") if self.comp_vars[c].get()}
        do_doi = self.comp_vars["doi"].get()
        do_classify = self.comp_vars["classification"].get()
        do_rag = self.comp_vars["rag"].get()
        print(f"[Selection] Components: sections (required)"
              + "".join(f", {c}" for c in ("abstract", "intro", "references") if c in components)
              + (", doi/metadata" if do_doi else "") + (", classification" if do_classify else "")
              + (", text+vector DB" if do_rag else ""))

        # Classification & Evaluation copy-vs-generate decision. When prior dated
        # data already exists for this storage space, ask ONCE per category (on the
        # main thread, before the worker starts, since it may pop a modal dialog)
        # whether to copy the previous data or generate fresh data into today's
        # dated file. A brand-new batch has no prior data and defaults to generate.
        class_mode = "generate"
        eval_mode = "generate"

        if do_classify:
            from alr.analysis_evaluation.publication_classification.classify_runner import has_existing_classification
            existing_class = has_existing_classification(MF, kind="title") + has_existing_classification(MF, kind="abstract")
            if existing_class:
                choice = messagebox.askyesnocancel(
                    "Existing classification found",
                    f"{existing_class} classification record(s) already exist for this storage space.\n\n"
                    "Yes = generate NEW classification (write today's dated file; prior files kept)\n"
                    "No = COPY the existing classification from the previous dated file(s)\n"
                    "Cancel = abort")
                if choice is None:
                    return
                class_mode = "generate" if choice else "copy"

        # Evaluation prior data lives in the dated Abstract_Eval_Overview workbooks.
        from alr.common.file_manager import Vec_DB_Manager
        try:
            eval_overview_folder = Vec_DB_Manager(MF.folder).Abstract_Overview_folder
            existing_eval = any(eval_overview_folder.glob("*Abstract_Eval_Overview*.xlsx"))
        except Exception:
            existing_eval = False
        if existing_eval:
            choice = messagebox.askyesnocancel(
                "Existing evaluation found",
                "Evaluation data already exists for this storage space.\n\n"
                "Yes = generate NEW evaluation\n"
                "No = COPY the existing evaluation\n"
                "Cancel = abort")
            if choice is None:
                return
            eval_mode = "generate" if choice else "copy"

        # The whole analysis + enrichment chain runs on a background thread with a
        # progress dialog so the UI stays responsive and cancellable. Each document
        # is taken through ALL selected steps before moving to the next one (rather
        # than stage-by-stage), and every step is precheck-driven: existing data is
        # copied across storage/SQL instead of recomputed.
        def work(progress, should_cancel):
            from alr.data_analysis.batch_dedup import find_new_and_duplicate_pdfs
            from alr.common.sql_store import sync_one_document, AnalyzedDataStore, DB_PATH

            def lookup_doc(filename):
                """Fetch this document's current SQL row (or None)."""
                try:
                    store = AnalyzedDataStore(DB_PATH)
                    for d in store.list_documents():
                        if str(d.get("filename")) == str(filename) and d.get("source_folder") == str(MF.folder):
                            return d
                except Exception as e:
                    print(f"[DB lookup] {filename}: {e}")
                return None

            print("[Processing Strategy Active] Directing targets into analysis execution channels...")

            # Build the list of PDFs to process (single file, or a folder with an
            # optional fuzzy-duplicate pre-scan) plus a shared Docling converter for
            # batches (loaded once). Single files keep the isolated subprocess path.
            doc_converter = None
            if result.kind == "pdf_file":
                to_process = [Path(result.input_path)]
            else:
                if skip_dupes:
                    progress(text="Scanning for duplicate titles…")
                    to_process, skipped = find_new_and_duplicate_pdfs(
                        result.input_path, MF, llm_service=service, components=components,
                        should_cancel=should_cancel,
                        progress_callback=lambda d, t: progress(done=d, total=t, text=f"Scanning duplicates {d}/{t}…"))
                    if skipped:
                        print(f"[Dedup] Skipped {len(skipped)} duplicate(s); logged to {MF.duplicate_log_excel}")
                else:
                    to_process = sorted(Path(result.input_path).rglob("*.pdf"))

                if to_process and not should_cancel():
                    progress(text="Loading Docling model (once for the batch)…")
                    try:
                        from alr.data_analysis.Table_image_extractor import get_shared_doc_converter
                        doc_converter = get_shared_doc_converter()
                    except Exception as e:
                        print(f"[Docling] Shared converter unavailable; falling back to per-file: {e}")

            processed = 0
            total = len(to_process)
            for i, pdf in enumerate(to_process, 1):
                if should_cancel():
                    break
                pdf = Path(pdf)

                # 1. Analyze the document (sectioning + selected components; on-disk
                #    resume skips already-completed stages). Evaluation runs inside
                #    right after abstract, honoring eval_mode.
                progress(done=i, total=total, text=f"[{i}/{total}] Analyzing {pdf.name}")
                process_pdf_mode_file(str(pdf), str(MF.folder), components=components,
                                      doc_converter=doc_converter, eval_mode=eval_mode)

                # 2. Copy this document's analysis into SQL immediately (latest wins).
                progress(text=f"[{i}/{total}] Updating database: {pdf.name}")
                try:
                    if not sync_one_document(MF, pdf.name):
                        print(f"[Database Sync] No registry row yet for {pdf.name}.")
                except Exception as e:
                    print(f"[Database Sync] {pdf.name}: {e}")

                doc = lookup_doc(pdf.name)

                # 3. DOI / metadata (precheck copies existing data instead of re-extracting).
                if do_doi and not should_cancel():
                    progress(text=f"[{i}/{total}] DOI / metadata: {pdf.name}")
                    try:
                        from alr.data_analysis.doi_metadata import enrich_space_with_doi
                        enrich_space_with_doi(MF, input_path=str(pdf), should_cancel=should_cancel)
                        doc = lookup_doc(pdf.name) or doc
                    except Exception as e:
                        print(f"[DOI Enrichment] {pdf.name}: {e}")

                # 4. Classification (title + abstract), copy-or-generate per class_mode.
                if do_classify and doc and not should_cancel():
                    progress(text=f"[{i}/{total}] Classifying: {pdf.name}")
                    try:
                        from alr.analysis_evaluation.publication_classification.classify_runner import classify_document
                        classify_document(MF, doc, kind="title", service=service, mode=class_mode)
                        classify_document(MF, doc, kind="abstract", service=service, mode=class_mode)
                    except Exception as e:
                        print(f"[Classification] {pdf.name}: {e}")

                processed += 1

            print("Analysis Execution Chain Log Sequence Finished.")

            if should_cancel():
                return processed

            # --- Finalization: full SQL refresh + completeness sweeps for anything
            # the per-document pass skipped (all idempotent). ---
            progress(text="Finalizing: syncing the review database…")
            try:
                synced = sync_storage_to_sql(MF)
                print(f"[Database Sync] {synced} document(s) written to the review database.")
            except Exception as e:
                print(f"[Database Sync] Skipped/failed: {e}")

            progress(text="Finalizing: evaluation sweep…")
            try:
                from alr.analysis_evaluation.data_evaluator import evaluate_space
                evaluate_space(MF, should_cancel=should_cancel, mode=eval_mode)
            except Exception as e:
                print(f"[Evaluation] Skipped/failed: {e}")

            # Introduction evaluation keeps pace with the abstract evaluation.
            progress(text="Finalizing: introduction evaluation sweep…")
            try:
                from alr.analysis_evaluation.data_evaluator import evaluate_space
                evaluate_space(MF, should_cancel=should_cancel, mode=eval_mode, target="intro")
            except Exception as e:
                print(f"[Intro Evaluation] Skipped/failed: {e}")

            if do_doi:
                progress(text="Matching metadata from download logs…")
                try:
                    from alr.common.download_log_enrich import enrich_from_download_logs
                    from alr.common.file_manager import ALR_main_folder
                    enrich_from_download_logs(ALR_main_folder, should_cancel=should_cancel)
                except Exception as e:
                    print(f"[Download-log Enrichment] Skipped/failed: {e}")

            if do_classify:
                progress(text="Finalizing: classification sweep…")
                try:
                    from alr.analysis_evaluation.publication_classification.classify_runner import (
                        classify_space, classify_abstract_space,
                    )
                    classify_space(MF, should_cancel=should_cancel, service=service, mode=class_mode)
                    classify_abstract_space(MF, should_cancel=should_cancel, service=service, mode=class_mode)
                except Exception as e:
                    print(f"[Classification] Skipped/failed: {e}")

            # Build text DB + FAISS vector DB for RAG when requested (heavy; last).
            if do_rag and not should_cancel():
                progress(text="Building text + vector DB…")
                try:
                    from alr.rag_builders.db_manager import generate_databases as build_rag_databases
                    build_rag_databases(str(MF.folder))
                except Exception as e:
                    print(f"[RAG DB] Skipped/failed: {e}")

            # Prune empty files/folders the manager pre-created but nothing wrote to.
            progress(text="Cleaning up empty files and folders…")
            try:
                from alr.common.artifact_cleanup import prune_empty_artifacts
                prune_empty_artifacts(MF.folder)
            except Exception as e:
                print(f"[Cleanup] Skipped/failed: {e}")

            return processed

        self._run_threaded(work, "Analyzing Literature", "analyzed")

    def _run_threaded(self, work, title, result_word="processed", on_success=None):
        """
        Run ``work(progress, should_cancel)`` on a background thread with a modal
        progress dialog (with Cancel). The worker only touches a thread-safe queue;
        all Tk access happens on the main thread via an ``after``-scheduled poller
        (calling Tk from a worker thread is unsafe). ``progress(done=?, total=?,
        text=?)`` enqueues an update; ``should_cancel()`` returns True once Cancel
        is pressed; ``work`` returns a value passed to ``on_success`` (or, by
        default, treated as an int document count).

        ``on_success(result)``, if given, is called on the main thread instead of
        the default "processed N document(s)" message box -- for passes whose
        result isn't a plain document count (e.g. a classification result dict,
        a workbook path, or computed metrics text).
        """
        cancel_event = threading.Event()
        dlg = ProgressDialog(self, title, on_cancel=cancel_event.set)
        q = queue.Queue()
        outcome = {}

        def progress(**kw):
            q.put(("progress", kw))

        def worker():
            try:
                q.put(("done", work(progress, cancel_event.is_set)))
            except Exception as e:  # noqa: BLE001 - surface any failure to the UI
                log_path = crash_logger.write_crash_log(
                    *sys.exc_info(), origin=f"background task: {title}")
                q.put(("error", (e, log_path)))

        def finish():
            dlg.close()
            if "error" in outcome:
                msg = str(outcome["error"])
                if outcome.get("error_log"):
                    msg += f"\n\nA full traceback was saved to:\n{outcome['error_log']}"
                messagebox.showerror(title, msg)
            elif cancel_event.is_set():
                messagebox.showinfo(title, f"{title}: cancelled after {result_word} "
                                           f"{outcome.get('n', 0)} document(s).")
            elif on_success is not None:
                on_success(outcome.get("n"))
            else:
                messagebox.showinfo(title, f"{title}: {result_word} {outcome.get('n', 0)} document(s).")

        def poll():
            try:
                while True:
                    kind, payload = q.get_nowait()
                    if kind == "progress":
                        dlg.apply(**payload)
                    elif kind == "done":
                        outcome["n"] = payload
                        finish()
                        return
                    elif kind == "error":
                        outcome["error"], outcome["error_log"] = payload
                        finish()
                        return
            except queue.Empty:
                pass
            self.after(80, poll)

        threading.Thread(target=worker, daemon=True).start()
        self.after(80, poll)

    # ==========================================
    # TAB 3: VISUALIZE & RAG QUERY
    # ==========================================
    def _build_visualize_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="3. Visualize & Query")

        v_frame = tk.LabelFrame(tab, text="RAG Database Query Engine (Vector DB Layout Profiles)")
        v_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # Storage Vector Path Layout Selection parameters settings setup
        db_path_frame = ttk.Frame(v_frame)
        db_path_frame.pack(fill="x", padx=5, pady=10)
        
        ttk.Label(db_path_frame, text="Select Data Storage Engine Source Directory Path:").pack(anchor="w", padx=5)
        self.visualize_storage_entry = ttk.Entry(db_path_frame, width=65)
        self.visualize_storage_entry.pack(side="left", padx=5, pady=5)
        ttk.Button(db_path_frame, text="Browse...", command=lambda: self._browse_folder(self.visualize_storage_entry)).pack(side="left", padx=2, pady=5)

        # Embedding engine used to build/query the vector DBs (shared session
        # setting - the same selector also appears on the Evaluate & Enrich tab
        # and both stay in sync).
        self._build_embedding_selector(v_frame).pack(fill="x", padx=5, pady=(0, 5))

        # Query Formulation block structures
        query_frame = ttk.Frame(v_frame)
        query_frame.pack(fill="x", padx=5, pady=10)

        ttk.Label(query_frame, text="Provide Statement/Search Query to identify Literature items:").pack(anchor="w", padx=5)
        self.query_entry = ttk.Entry(query_frame, width=80)
        self.query_entry.pack(fill="x", padx=5, pady=5)

        # Query scope: all sections vs. Research-Area / Key-Concept sections only.
        scope_frame = ttk.Frame(v_frame)
        scope_frame.pack(fill="x", padx=5, pady=(0, 5))
        ttk.Label(scope_frame, text="Query scope:").pack(side="left", padx=5)
        self.query_scope_var = ttk.Combobox(
            scope_frame,
            values=["All sections", "Research-Area & Key-Concept only"],
            width=32, state="readonly",
        )
        self.query_scope_var.set("All sections")
        self.query_scope_var.pack(side="left", padx=5)

        btn_run_query = ttk.Button(v_frame, text="Generate DB Framework & Query Report", command=self._run_visualization_query_action)
        btn_run_query.pack(pady=20, ipadx=10, ipady=5)

    def _run_visualization_query_action(self):
        storage_choice = self.visualize_storage_entry.get().strip()
        query_text = self.query_entry.get().strip()

        if not storage_choice or not query_text:
            messagebox.showerror("Error", "Please make sure to supply both data engine reference destination parameters and active query statements strings text models templates.")
            return

        print("\n[RAG Database Architecture Step] Synchronizing local vector storage mapping structures...")
        generate_databases(storage_choice)

        ra_kc_only = self.query_scope_var.get().startswith("Research-Area")
        print(f"[Query Pipeline Dispatch] Running query text profiling execution match targeting expression: '{query_text}'")
        if ra_kc_only:
            from alr.rag_builders.query_executor import generate_query_report_RA_KC
            print("[Query Scope] Restricting to Research-Area & Key-Concept sections.")
            generate_query_report_RA_KC([query_text], storage_choice)
        else:
            generate_query_report([query_text], storage_choice)
        print("Query Generation Suite Logging Executed successfully.")

    # ==========================================
    # TAB 4: SECTION JSON EDITOR
    # ==========================================
    def _build_section_editor_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="4. Section Editor")

        header = ttk.Frame(tab)
        header.pack(fill="x", padx=10, pady=(8, 0))
        ttk.Label(header, text="Restructure and edit section JSON files.").pack(side="left")
        ttk.Button(header, text="Pop out ▸", command=lambda: open_section_editor_window(self)).pack(side="right")

        container = ttk.Frame(tab)
        container.pack(fill="both", expand=True)
        self.section_editor = JSONRestructurerUI(container)

    # ==========================================
    # TAB 5: EVALUATE & ENRICH
    # ==========================================
    def _build_evaluate_tab(self):
        outer = ttk.Frame(self.notebook)
        self.notebook.add(outer, text="5. Evaluate & Enrich")

        # The tab holds two full sections (Evaluation + Enrichment) and grows
        # past the window height, so its content lives in a scrollable canvas.
        canvas = tk.Canvas(outer, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        tab = ttk.Frame(canvas)
        tab_window = canvas.create_window((0, 0), window=tab, anchor="nw")
        tab.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(tab_window, width=e.width))

        # --- Shared inputs (used by both Evaluation and Enrichment below) ---
        shared = tk.LabelFrame(tab, text="Storage Space & LLM Service (shared)")
        shared.pack(fill="x", padx=10, pady=(10, 5))

        row = ttk.Frame(shared)
        row.pack(fill="x", padx=5, pady=8)
        ttk.Label(row, text="Storage folder:").pack(side="left", padx=5)
        self.eval_storage_entry = ttk.Entry(row, width=55)
        self.eval_storage_entry.pack(side="left", padx=5)
        ttk.Button(row, text="Browse...", command=lambda: self._browse_folder(self.eval_storage_entry)).pack(side="left", padx=2)

        llm_row = ttk.Frame(shared)
        llm_row.pack(fill="x", padx=5, pady=(0, 8))
        ttk.Label(llm_row, text="LLM Processing Service Engine:").pack(side="left", padx=5)
        self.llm_choice_eval = ttk.Combobox(llm_row, values=["O", "B"], width=5, state="readonly")
        self.llm_choice_eval.set("B")
        self.llm_choice_eval.pack(side="left", padx=5)
        ttk.Button(llm_row, text="Choose Model...",
                   command=lambda: self._choose_model_action(self.llm_choice_eval.get())
                   ).pack(side="left", padx=5)
        ttk.Label(llm_row, text="(used by Classify Titles / Classify Abstracts)").pack(side="left", padx=5)

        # Embedding engine (used by the cosine-similarity evaluation and by
        # vector-DB builds/queries; kept in sync with the Visualize tab copy).
        self._build_embedding_selector(shared).pack(fill="x", padx=5, pady=(0, 8))

        # ================= EVALUATION =================
        eval_frame = tk.LabelFrame(tab, text="Evaluation (batch, over the storage space above)")
        eval_frame.pack(fill="x", padx=10, pady=5)

        # Evaluation-type choices.
        self.eval_kind_vars = {
            "substring": tk.BooleanVar(value=True),
            "lexical": tk.BooleanVar(value=False),
            "distance": tk.BooleanVar(value=False),
            "cosine": tk.BooleanVar(value=False),
        }
        kinds_row = ttk.Frame(eval_frame)
        kinds_row.pack(fill="x", padx=5, pady=(8, 2))
        ttk.Checkbutton(kinds_row, text="Substring match (data grounding)",
                        variable=self.eval_kind_vars["substring"]).grid(row=0, column=0, sticky="w", padx=6, pady=2)
        ttk.Checkbutton(kinds_row, text="Lexical overlap (Jaccard / ROUGE / BLEU)",
                        variable=self.eval_kind_vars["lexical"]).grid(row=0, column=1, sticky="w", padx=6, pady=2)
        ttk.Checkbutton(kinds_row, text="Distance & structural alignment (Levenshtein / WER)",
                        variable=self.eval_kind_vars["distance"]).grid(row=1, column=0, sticky="w", padx=6, pady=2)
        ttk.Checkbutton(kinds_row, text="Cosine similarity (embeddings; reuses/creates vector DBs)",
                        variable=self.eval_kind_vars["cosine"]).grid(row=1, column=1, sticky="w", padx=6, pady=2)

        target_row = ttk.Frame(eval_frame)
        target_row.pack(fill="x", padx=5, pady=(2, 2))
        ttk.Label(target_row, text="Evaluate:").pack(side="left", padx=6)
        self.eval_target_var = tk.StringVar(value="abstract")
        ttk.Radiobutton(target_row, text="Abstract data", value="abstract",
                        variable=self.eval_target_var).pack(side="left", padx=4)
        ttk.Radiobutton(target_row, text="Introduction data", value="intro",
                        variable=self.eval_target_var).pack(side="left", padx=4)
        ttk.Radiobutton(target_row, text="Both", value="both",
                        variable=self.eval_target_var).pack(side="left", padx=4)
        ttk.Button(target_row, text="Run Evaluation", command=self._run_evaluation_action).pack(side="left", padx=16)

        ttk.Label(eval_frame,
                  text="(Each selected metric compares every extracted section item against the document's "
                       "identified abstract/introduction text. Results: per-section evaluation workbooks, one dated "
                       "workbook per metric kind plus a combined metrics-overview workbook in the space, and summary "
                       "columns in the review database. Tip: run 'Build Text + Vector DB (RAG)' first so cosine "
                       "reuses the stored vectors.)",
                  wraplength=860, justify="left").pack(anchor="w", padx=8, pady=(0, 6))

        # --- Manual text comparison (kept from the standalone metrics panel) ---
        met_frame = tk.LabelFrame(eval_frame, text="Manual text comparison (paste two texts)")
        met_frame.pack(fill="both", expand=True, padx=6, pady=(0, 8))

        grid = ttk.Frame(met_frame)
        grid.pack(fill="both", expand=True, padx=5, pady=5)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        ttk.Label(grid, text="Reference text:").grid(row=0, column=0, sticky="w")
        ttk.Label(grid, text="Candidate text:").grid(row=0, column=1, sticky="w")
        self.metric_ref_text = tk.Text(grid, height=5, wrap="word")
        self.metric_ref_text.grid(row=1, column=0, sticky="nsew", padx=(0, 4), pady=2)
        self.metric_cand_text = tk.Text(grid, height=5, wrap="word")
        self.metric_cand_text.grid(row=1, column=1, sticky="nsew", padx=(4, 0), pady=2)
        grid.rowconfigure(1, weight=1)

        ttk.Button(met_frame, text="Compute Metrics", command=self._compute_metrics_action).pack(pady=6)

        self.metric_result_text = tk.Text(met_frame, height=7, wrap="word", state="disabled")
        self.metric_result_text.pack(fill="both", expand=True, padx=5, pady=(0, 5))

        # ================= ENRICHMENT =================
        enrich_frame = tk.LabelFrame(tab, text="Enrichment")
        enrich_frame.pack(fill="x", padx=10, pady=5)

        # --- Storage-space re-run passes ---
        pass_frame = tk.LabelFrame(enrich_frame, text="Storage-Space Passes (on an already-analyzed folder)")
        pass_frame.pack(fill="x", padx=6, pady=(8, 5))

        btn_row = ttk.Frame(pass_frame)
        btn_row.pack(fill="x", padx=5, pady=8)
        ttk.Button(btn_row, text="Re-run Abstract Analysis",
                   command=lambda: self._run_storage_pass("abstract")).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Re-run Reference Extraction",
                   command=lambda: self._run_storage_pass("references")).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Classify Titles",
                   command=lambda: self._run_storage_pass("classify_title")).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Classify Abstracts",
                   command=lambda: self._run_storage_pass("classify_abstract")).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Build Master Excel DB",
                   command=lambda: self._run_storage_pass("master_excel")).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Enrich from Download Logs",
                   command=lambda: self._run_storage_pass("download_logs")).pack(side="left", padx=4)

        # --- DOI / metadata extraction (standalone, targets a selected file/folder) ---
        doi_frame = tk.LabelFrame(enrich_frame, text="DOI / Metadata Extraction (standalone)")
        doi_frame.pack(fill="x", padx=6, pady=5)

        drow = ttk.Frame(doi_frame)
        drow.pack(fill="x", padx=5, pady=8)
        ttk.Label(drow, text="PDF file or folder:").pack(side="left", padx=5)
        self.doi_input_entry = ttk.Entry(drow, width=45)
        self.doi_input_entry.pack(side="left", padx=5)
        ttk.Button(drow, text="Select File", command=lambda: self._browse_file(self.doi_input_entry)).pack(side="left", padx=2)
        ttk.Button(drow, text="Select Folder", command=lambda: self._browse_folder(self.doi_input_entry)).pack(side="left", padx=2)
        ttk.Button(drow, text="Run DOI / Metadata Extraction",
                   command=self._run_doi_extraction_action).pack(side="left", padx=8)
        ttk.Label(doi_frame,
                  text="(Extracts DOI/arXiv metadata from exactly the file or folder selected above -- not the "
                       "whole storage space. Uses the 'Storage folder' above for the managed output workbook and "
                       "SQLite sync; leave it blank to use the default storage space.)"
                  ).pack(anchor="w", padx=8, pady=(0, 6))

        # --- Publication title classification (on-demand, single title) ---
        cls_frame = tk.LabelFrame(enrich_frame, text="Publication Title Classification")
        cls_frame.pack(fill="x", padx=6, pady=5)
        crow = ttk.Frame(cls_frame)
        crow.pack(fill="x", padx=5, pady=8)
        ttk.Label(crow, text="Title:").pack(side="left", padx=5)
        self.classify_title_entry = ttk.Entry(crow, width=70)
        self.classify_title_entry.pack(side="left", padx=5, fill="x", expand=True)
        ttk.Button(crow, text="Classify Title", command=self._classify_title_action).pack(side="left", padx=5)

        # --- Question-scored classification (on-demand, multi-sheet) ---
        qcls_frame = tk.LabelFrame(enrich_frame, text="Question-Scored Classification (on demand)")
        qcls_frame.pack(fill="x", padx=6, pady=(5, 8))

        qrow = ttk.Frame(qcls_frame)
        qrow.pack(fill="x", padx=5, pady=(8, 2))
        ttk.Label(qrow, text="Source:").pack(side="left", padx=5)
        self.qscore_source_var = ttk.Combobox(
            qrow,
            values=["Registry title", "Download log (Publication Name)"],
            width=32, state="readonly",
        )
        self.qscore_source_var.set("Registry title")
        self.qscore_source_var.pack(side="left", padx=5)
        ttk.Button(qrow, text="Run Question-Scored Classification",
                   command=self._question_score_action).pack(side="left", padx=8)

        qrow2 = ttk.Frame(qcls_frame)
        qrow2.pack(fill="x", padx=5, pady=(0, 8))
        ttk.Label(qrow2, text="Download log (.xlsx):").pack(side="left", padx=5)
        self.qscore_log_entry = ttk.Entry(qrow2, width=50)
        self.qscore_log_entry.pack(side="left", padx=5)
        ttk.Button(qrow2, text="Browse...", command=lambda: self._browse_file(self.qscore_log_entry)).pack(side="left", padx=2)
        ttk.Label(qcls_frame, text="(Uses the 'Storage folder' above for registry source and managed output; scores each title against the full question set.)").pack(anchor="w", padx=8, pady=(0, 6))

    def _run_evaluation_action(self):
        """
        Run the selected evaluation types over the shared storage space, against
        the chosen target data (abstract / introduction / both). Substring
        grounding goes through data_evaluator; lexical/distance/cosine go
        through metric_evaluator. Everything is synced to SQL for overviews.
        """
        folder = self.eval_storage_entry.get().strip()
        if not folder or not Path(folder).is_dir():
            messagebox.showerror("Error", "Select a valid analyzed storage folder first (shared inputs above).")
            return
        clean_path = clean_folder_path(folder)

        do_substring = self.eval_kind_vars["substring"].get()
        metric_kinds = {k for k in ("lexical", "distance", "cosine") if self.eval_kind_vars[k].get()}
        if not do_substring and not metric_kinds:
            messagebox.showerror("Error", "Select at least one evaluation type.")
            return

        choice = self.eval_target_var.get()
        targets = ["abstract", "intro"] if choice == "both" else [choice]
        print(f"[Evaluation] types: "
              + ", ".join((["substring"] if do_substring else []) + sorted(metric_kinds))
              + f" | target(s): {', '.join(targets)}")

        def work(progress, should_cancel):
            from alr.common.sql_store import sync_storage_to_sql

            # Sync first so evaluation summaries land on existing SQL rows.
            progress(text="Syncing storage into the review database…")
            try:
                sync_storage_to_sql(DataAnalyzeManager(clean_path))
            except Exception as e:
                print(f"[Database Sync] Skipped/failed: {e}")

            n = 0
            for t in targets:
                label = "introduction" if t == "intro" else "abstract"
                if should_cancel():
                    break
                if do_substring:
                    progress(text=f"Substring grounding evaluation ({label})…")
                    from alr.analysis_evaluation.data_evaluator import evaluate_space
                    n = max(n, evaluate_space(
                        clean_path, should_cancel=should_cancel, target=t,
                        progress_callback=lambda d, tot, lab=label: progress(
                            done=d, total=tot, text=f"Substring evaluation ({lab})  {d}/{tot}…")))
                if metric_kinds and not should_cancel():
                    progress(text=f"Metric evaluation ({', '.join(sorted(metric_kinds))}) — {label}…")
                    from alr.analysis_evaluation.metric_evaluator import evaluate_space_metrics
                    n = max(n, evaluate_space_metrics(
                        clean_path, metric_kinds, target=t, should_cancel=should_cancel,
                        progress_callback=lambda d, tot, lab=label: progress(
                            done=d, total=tot, text=f"Metric evaluation ({lab})  {d}/{tot}…")))
            return n

        self._run_threaded(work, "Run Evaluation", "evaluated")

    def _run_doi_extraction_action(self):
        input_target = self.doi_input_entry.get().strip()
        if not input_target or not Path(input_target).exists():
            messagebox.showerror("Error", "Please select a valid PDF file or folder first.")
            return

        folder = self.eval_storage_entry.get().strip()
        clean_path = clean_folder_path(folder) if folder and Path(folder).is_dir() else None

        def work(progress, should_cancel):
            from alr.data_analysis.doi_metadata import enrich_space_with_doi
            progress(text=f"Extracting DOI / metadata from {Path(input_target).name}…")
            MF = DataAnalyzeManager(clean_path) if clean_path else DataAnalyzeManager()
            n = enrich_space_with_doi(MF, input_path=input_target, should_cancel=should_cancel)
            print(f"[Evaluate] DOI/metadata enrichment updated {n} document(s).")
            return n

        self._run_threaded(work, "DOI / Metadata Extraction", "updated")

    def _run_storage_pass(self, mode):
        folder = self.eval_storage_entry.get().strip()
        # Download-log enrichment scans a root for *_download_log files; the
        # storage folder is optional (defaults to the main ALR folder).
        if mode != "download_logs" and (not folder or not Path(folder).is_dir()):
            messagebox.showerror("Error", "Please select a valid analyzed storage folder first.")
            return

        # API-key checks happen up front on the main thread (they may pop a
        # modal dialog), before any background work starts.
        classify_service = self.llm_choice_eval.get()
        if mode in ("abstract", "references"):
            if not self._ensure_api_key("B") and not self._ensure_api_key("O"):
                return
        elif mode in ("classify_title", "classify_abstract"):
            if not self._ensure_api_key(classify_service):
                return

        clean_path = clean_folder_path(folder) if folder and Path(folder).is_dir() else None

        # If a classification workbook already has data for this space, ask
        # whether to rewrite everything or only classify what's left.
        overwrite = True
        if mode in ("classify_title", "classify_abstract"):
            from alr.analysis_evaluation.publication_classification.classify_runner import has_existing_classification
            kind = "title" if mode == "classify_title" else "abstract"
            label = "title" if mode == "classify_title" else "abstract"
            existing_n = has_existing_classification(clean_path, kind=kind) if clean_path else 0
            if existing_n:
                choice = messagebox.askyesnocancel(
                    "Existing classification found",
                    f"{existing_n} document(s) already have {label} classification data saved "
                    "for this storage space.\n\n"
                    "Yes = rewrite all data from scratch\n"
                    "No = keep the existing data and classify only the remaining document(s)\n"
                    "Cancel = abort")
                if choice is None:
                    return
                overwrite = bool(choice)

        titles = {
            "download_logs": "Enrich from Download Logs",
            "abstract": "Re-run Abstract Analysis",
            "references": "Re-run Reference Extraction",
            "evaluate": "Build Evaluation DBs",
            "classify_title": "Classify Titles",
            "classify_abstract": "Classify Abstracts",
            "master_excel": "Build Master Excel DB",
        }
        dialog_title = titles.get(mode, "Storage-Space Pass")

        def work(progress, should_cancel):
            if mode == "download_logs":
                from alr.common.download_log_enrich import enrich_from_download_logs
                from alr.common.file_manager import ALR_main_folder
                root = clean_path or ALR_main_folder
                progress(text=f"Enriching metadata from download logs under: {root}…")
                print(f"[Evaluate] Enriching metadata from download logs under: {root}")
                n = enrich_from_download_logs(root, should_cancel=should_cancel)
                print(f"[Evaluate] Download-log enrichment updated {n} document(s).")
                return n

            if mode == "abstract":
                from alr.data_analysis.Folder_Data_Analyzer import process_abstract
                progress(text="Re-running abstract analysis pass…")
                print("[Evaluate] Re-running abstract analysis pass...")
                process_abstract(DataAnalyzeManager(clean_path))
                print("[Evaluate] Abstract analysis pass finished.")
                return 0

            if mode == "references":
                from alr.data_analysis.Folder_Data_Analyzer import process_references
                progress(text="Re-running reference extraction pass…")
                print("[Evaluate] Re-running reference extraction pass...")
                process_references(DataAnalyzeManager(clean_path))
                print("[Evaluate] Reference extraction pass finished.")
                return 0

            if mode == "evaluate":
                from alr.common.sql_store import sync_storage_to_sql
                from alr.analysis_evaluation.data_evaluator import generate_databases as generate_eval_databases
                progress(text="Syncing storage to DB…")
                print("[Evaluate] Syncing storage to DB, then building analysis-evaluation databases...")
                sync_storage_to_sql(DataAnalyzeManager(clean_path))
                progress(text="Building analysis-evaluation databases…")
                generate_eval_databases(clean_path)
                print("[Evaluate] Analysis-evaluation databases built.")
                return 0

            if mode == "classify_title":
                from alr.common.sql_store import sync_storage_to_sql
                from alr.analysis_evaluation.publication_classification.classify_runner import classify_space
                progress(text="Syncing storage to DB…")
                print("[Evaluate] Syncing storage to DB, then classifying titles...")
                sync_storage_to_sql(DataAnalyzeManager(clean_path))
                progress(text="Classifying titles…")
                n = classify_space(
                    clean_path, should_cancel=should_cancel, overwrite=overwrite, service=classify_service,
                    progress_callback=lambda d, t: progress(done=d, total=t, text=f"Classifying titles {d}/{t}…"))
                print(f"[Evaluate] Title classification updated {n} document(s).")
                return n

            if mode == "classify_abstract":
                from alr.common.sql_store import sync_storage_to_sql
                from alr.analysis_evaluation.publication_classification.classify_runner import classify_abstract_space
                progress(text="Syncing storage to DB…")
                print("[Evaluate] Syncing storage to DB, then classifying abstracts...")
                sync_storage_to_sql(DataAnalyzeManager(clean_path))
                progress(text="Classifying abstracts…")
                n = classify_abstract_space(
                    clean_path, should_cancel=should_cancel, overwrite=overwrite, service=classify_service,
                    progress_callback=lambda d, t: progress(done=d, total=t, text=f"Classifying abstracts {d}/{t}…"))
                print(f"[Evaluate] Abstract classification updated {n} document(s).")
                return n

            if mode == "master_excel":
                from alr.rag_builders.master_excel_db_builder import build_master_excel_db
                progress(text="Consolidating per-section data into the master Excel workbook…")
                print("[Evaluate] Consolidating per-section data into the master Excel workbook...")
                written, master_path = build_master_excel_db(clean_path)
                print(f"[Evaluate] Master Excel workbook ({written} document(s)): {master_path}")
                return written

            return 0

        def on_success(n):
            print("[Evaluate] Pass finished.")
            messagebox.showinfo("Done", "Storage-space pass finished. See the console log for details.")

        self._run_threaded(work, dialog_title, on_success=on_success)

    def _classify_title_action(self):
        title = self.classify_title_entry.get().strip()
        if not title:
            messagebox.showerror("Error", "Please enter a publication title to classify.")
            return
        service = self.llm_choice_eval.get()
        if not self._ensure_api_key(service):
            return

        def work(progress, should_cancel):
            from alr.analysis_evaluation.publication_classification.title_classifier import classify_title
            progress(text=f"Classifying title: {title!r}…")
            print(f"[Classify] Classifying title: {title!r} (service={service})")
            result = classify_title(title, service=service) or {}
            print(f"[Classify] Result: {result}")
            return result

        def on_success(result):
            result = result or {}
            matched = [topic for topic, hit in result.items() if hit]
            if matched:
                messagebox.showinfo("Classification result",
                                    "Matched topics:\n\n- " + "\n- ".join(matched))
            else:
                messagebox.showinfo("Classification result", "No topics matched this title.")

        self._run_threaded(work, "Classify Title", on_success=on_success)

    def _question_score_action(self):
        use_log = self.qscore_source_var.get().startswith("Download log")
        if use_log:
            download_log = self.qscore_log_entry.get().strip()
            if not download_log or not Path(download_log).is_file():
                messagebox.showerror("Error", "Please select a valid download-log .xlsx file.")
                return
            source, folder = "download_log", None
        else:
            folder = self.eval_storage_entry.get().strip()
            if not folder or not Path(folder).is_dir():
                messagebox.showerror("Error", "Please select a valid analyzed storage folder (above) for the registry source.")
                return
            source, download_log = "registry", None

        if not self._ensure_api_key("B"):
            return

        def work(progress, should_cancel):
            from alr.analysis_evaluation.publication_classification.classify_runner import question_score_space
            progress(text="Running question-scored classification (this can take a while)…")
            print("[Question Scoring] Running question-scored classification (this can take a while)...")
            manager = DataAnalyzeManager(clean_folder_path(folder)) if folder else DataAnalyzeManager()
            out = question_score_space(manager, source=source, download_log=download_log)
            if out:
                print(f"[Question Scoring] Workbook written: {out}")
            else:
                print("[Question Scoring] No workbook was produced.")
            return out

        def on_success(out):
            if out:
                messagebox.showinfo("Done", f"Question-scored classification saved to:\n{out}")
            else:
                messagebox.showwarning("Nothing produced", "No workbook was produced. See the console log.")

        self._run_threaded(work, "Question-Scored Classification", on_success=on_success)

    def _compute_metrics_action(self):
        ref = self.metric_ref_text.get("1.0", tk.END).strip()
        cand = self.metric_cand_text.get("1.0", tk.END).strip()
        if not ref or not cand:
            messagebox.showerror("Error", "Please provide both a reference and a candidate text.")
            return

        def work(progress, should_cancel):
            import importlib
            progress(text="Computing text comparison metrics…")
            print("[Metrics] Computing text comparison metrics...")
            lexical = importlib.import_module("alr.analysis_evaluation.Lexical_Overlap_Metrics")
            distance = importlib.import_module("alr.analysis_evaluation.Distance_w_Structural _Alignment")

            jaccard = lexical.calculate_jaccard_similarity(ref, cand)
            rouge = lexical.calculate_rouge_scores(ref, cand)
            bleu = lexical.calculate_bleu_score(ref, cand)
            edit = distance.calculate_edit_distance_metrics(ref, cand)

            lines = [
                f"Jaccard similarity : {jaccard:.4f}",
                f"ROUGE-1 (F1)       : {rouge['ROUGE-1']:.4f}",
                f"ROUGE-2 (F1)       : {rouge['ROUGE-2']:.4f}",
                f"ROUGE-L (F1)       : {rouge['ROUGE-L']:.4f}",
                f"BLEU               : {bleu:.4f}",
                f"Levenshtein dist   : {edit['character_level']['levenshtein_distance']}",
                f"Levenshtein ratio  : {edit['character_level']['similarity_ratio']:.4f}",
                f"Word Error Rate    : {edit['word_level']['word_error_rate']:.4f}",
                f"  substitutions={edit['word_level']['substitutions']}"
                f"  insertions={edit['word_level']['insertions']}"
                f"  deletions={edit['word_level']['deletions']}",
            ]
            text = "\n".join(lines)
            print("[Metrics]\n" + text)
            return text

        def on_success(text):
            self.metric_result_text.configure(state="normal")
            self.metric_result_text.delete("1.0", tk.END)
            self.metric_result_text.insert("1.0", text or "")
            self.metric_result_text.configure(state="disabled")

        self._run_threaded(work, "Compute Metrics", on_success=on_success)

    # ==========================================
    # API KEY MANAGEMENT
    # ==========================================
    def _manage_api_keys_action(self):
        """Modal dialog to view/enter and persist the provider API keys."""
        dialog = tk.Toplevel(self)
        dialog.title("Manage API Keys")
        dialog.geometry("520x220")
        dialog.transient(self)
        dialog.grab_set()

        ttk.Label(dialog, text="API keys are stored as environment variables and persisted for next launch.",
                  wraplength=480).pack(padx=12, pady=10, anchor="w")

        entries = {}
        form = ttk.Frame(dialog)
        form.pack(fill="x", padx=12, pady=5)
        for i, key_type in enumerate(KEY_ENV_NAMES):
            ttk.Label(form, text=f"{key_type}:", width=14).grid(row=i, column=0, sticky="w", pady=6)
            var = tk.StringVar(value=get_stored_api_key(key_type) or "")
            entry = ttk.Entry(form, textvariable=var, width=48, show="*")
            entry.grid(row=i, column=1, pady=6, padx=5)
            entries[key_type] = var

        def _save():
            for key_type, var in entries.items():
                value = var.get().strip()
                if value:
                    set_api_key(key_type, value)
            messagebox.showinfo("Saved", "API keys saved.")
            dialog.destroy()

        ttk.Button(dialog, text="Save", command=_save).pack(pady=12)

    def _ensure_api_key(self, provider_code):
        """
        Ensure a key exists for the selected provider ('O'/'B'). If missing, open
        the key dialog. Returns True if a key is now present, else False.
        """
        key_type = "DLR Ollama" if str(provider_code).upper() == "O" else "BlaBla Door"
        if get_stored_api_key(key_type):
            return True
        messagebox.showinfo("API key required",
                            f"No API key found for {key_type}. Please enter it to continue.")
        self._manage_api_keys_action()
        return bool(get_stored_api_key(key_type))

    # ==========================================
    # GLOBAL UTILITY OPERATIONS HELPER MAPPINGS
    # ==========================================
    def _browse_file(self, target_entry):
        file_selected = filedialog.askopenfilename(filetypes=[("PDF Documents", "*.pdf"), ("All Document Items", "*.*")])
        if file_selected:
            target_entry.delete(0, tk.END)
            target_entry.insert(0, str(Path(file_selected).resolve()))

    def _browse_folder(self, target_entry):
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            target_entry.delete(0, tk.END)
            target_entry.insert(0, str(Path(folder_selected).resolve()))

    def _choose_model_action(self, provider_code):
        """
        Fetch the live list of available models for the selected provider
        ('O' = DLR Ollama, 'B' = Blablador), let the user pick one, and store
        it as the session model used by all subsequent LLM calls.
        """
        service = "DLR Ollama" if str(provider_code).upper() == "O" else "BlaBla"

        print(f"Fetching available {service} models...")
        try:
            models = list_available_models(service)
        except Exception as e:
            messagebox.showerror("Model list failed", f"Could not fetch models for {service}:\n{e}")
            return

        if not models:
            messagebox.showwarning("No models", f"No models returned for {service}.\nKeeping current: {get_selected_model(service)}")
            return

        current = get_selected_model(service)

        dialog = tk.Toplevel(self)
        dialog.title(f"Select {service} Model")
        dialog.geometry("560x400")
        dialog.transient(self)
        dialog.grab_set()

        ttk.Label(dialog, text=f"Available {service} models (current: {current}):",
                  font=("Arial", 10, "bold")).pack(padx=10, pady=8, anchor="w")

        list_frame = ttk.Frame(dialog)
        list_frame.pack(fill="both", expand=True, padx=10, pady=5)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set)
        scrollbar.config(command=listbox.yview)
        scrollbar.pack(side="right", fill="y")
        listbox.pack(side="left", fill="both", expand=True)

        for m in models:
            listbox.insert(tk.END, m)
        if current in models:
            idx = models.index(current)
            listbox.selection_set(idx)
            listbox.see(idx)

        def _on_confirm():
            sel = listbox.curselection()
            if not sel:
                messagebox.showinfo("No selection", "Please select a model, or close the dialog to keep the current one.")
                return
            set_selected_model(service, models[sel[0]])
            dialog.destroy()

        ttk.Button(dialog, text="Use Selected Model", command=_on_confirm).pack(pady=10)

    # ==========================================
    # EMBEDDING ENGINE SELECTION (vector DBs / cosine similarity)
    # ==========================================
    def _build_embedding_selector(self, parent):
        """
        Build one 'Embedding Engine' selector row inside `parent`.

        All instances share the same tk variables, so the copies on the
        Visualize tab and on the Evaluate & Enrich tab always show the same
        state. Changing any of them updates the session-wide embedding backend
        (llm_utils.set_embedding_backend) used by cosine evaluation, RAG
        vector-DB builds and vector queries via vector_db_updater.
        """
        if not hasattr(self, "embed_method_var"):
            # Same deployment-aware default as vector_db_updater: local model
            # if its weights exist on disk (or the env var forces a method),
            # otherwise the remote embedding API.
            override = os.getenv("ALR_EMBEDDING_METHOD", "").strip().lower()
            if override in ("local", "api"):
                default_method = "Local" if override == "local" else "API"
            else:
                has_local = bool(local_embedding_model_dir) and os.path.isdir(local_embedding_model_dir)
                default_method = "Local" if has_local else "API"
            default_service = "B" if "blabla" in os.getenv("ALR_EMBEDDING_SERVICE", "").lower() else "O"

            self.embed_method_var = tk.StringVar(value=default_method)
            self.embed_service_var = tk.StringVar(value=default_service)
            self.embed_model_var = tk.StringVar(value="")
            self._embed_service_boxes = []
            self._embed_model_buttons = []
            # Embedding model chosen this session, per service code ('O'/'B').
            self._embed_model_choice = {"O": None, "B": None}

        row = ttk.Frame(parent)
        ttk.Label(row, text="Embedding Engine:").pack(side="left", padx=5)
        method_box = ttk.Combobox(row, values=["Local", "API"], width=7, state="readonly",
                                  textvariable=self.embed_method_var)
        method_box.pack(side="left", padx=5)
        method_box.bind("<<ComboboxSelected>>", lambda e: self._apply_embedding_backend())

        ttk.Label(row, text="Service:").pack(side="left", padx=(10, 2))
        service_box = ttk.Combobox(row, values=["O", "B"], width=5, state="readonly",
                                   textvariable=self.embed_service_var)
        service_box.pack(side="left", padx=5)
        service_box.bind("<<ComboboxSelected>>", lambda e: self._apply_embedding_backend())

        model_btn = ttk.Button(row, text="Choose Embedding Model...",
                               command=self._choose_embedding_model_action)
        model_btn.pack(side="left", padx=5)
        ttk.Label(row, textvariable=self.embed_model_var).pack(side="left", padx=5)

        self._embed_service_boxes.append(service_box)
        self._embed_model_buttons.append(model_btn)
        self._apply_embedding_backend()
        return row

    def _apply_embedding_backend(self):
        """
        Push the widgets' state into the session-wide embedding backend and
        enable/disable the API-only controls (service box + model button).
        """
        method = "local" if self.embed_method_var.get() == "Local" else "api"
        service_code = self.embed_service_var.get().upper()
        service = "DLR Ollama" if service_code == "O" else "BlaBla"
        set_embedding_backend(method=method, service=service)

        box_state = "readonly" if method == "api" else "disabled"
        btn_state = "normal" if method == "api" else "disabled"
        for box in self._embed_service_boxes:
            box.configure(state=box_state)
        for btn in self._embed_model_buttons:
            btn.configure(state=btn_state)

        if method == "local":
            self.embed_model_var.set(f"(model: {embedding_model_repo_id}, local GPU/CPU)")
        else:
            chosen = self._embed_model_choice.get(service_code)
            self.embed_model_var.set(f"(model: {chosen})" if chosen else "(model: auto - service default)")

    def _choose_embedding_model_action(self):
        """
        Fetch the live list of embedding models for the selected embedding
        service ('O' = DLR Ollama, 'B' = Blablador), let the user pick one, and
        store it as the session embedding model. Mirrors _choose_model_action.
        """
        service_code = self.embed_service_var.get().upper()
        service = "DLR Ollama" if service_code == "O" else "BlaBla"

        print(f"Fetching available {service} embedding models...")
        try:
            models = list_embedding_models(service)
        except Exception as e:
            messagebox.showerror("Model list failed", f"Could not fetch embedding models for {service}:\n{e}")
            return

        if not models:
            messagebox.showwarning(
                "No embedding models",
                f"No embedding models returned for {service}.\n"
                f"Keeping current: {self._embed_model_choice.get(service_code) or 'auto (service default)'}")
            return

        current = self._embed_model_choice.get(service_code)

        dialog = tk.Toplevel(self)
        dialog.title(f"Select {service} Embedding Model")
        dialog.geometry("560x400")
        dialog.transient(self)
        dialog.grab_set()

        ttk.Label(dialog, text=f"Available {service} embedding models (current: {current or 'auto'}):",
                  font=("Arial", 10, "bold")).pack(padx=10, pady=8, anchor="w")

        list_frame = ttk.Frame(dialog)
        list_frame.pack(fill="both", expand=True, padx=10, pady=5)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set)
        scrollbar.config(command=listbox.yview)
        scrollbar.pack(side="right", fill="y")
        listbox.pack(side="left", fill="both", expand=True)

        for m in models:
            listbox.insert(tk.END, m)
        if current in models:
            idx = models.index(current)
            listbox.selection_set(idx)
            listbox.see(idx)

        def _on_confirm():
            sel = listbox.curselection()
            if not sel:
                messagebox.showinfo("No selection", "Please select a model, or close the dialog to keep the current one.")
                return
            chosen = models[sel[0]]
            set_selected_embedding_model(service, chosen)
            self._embed_model_choice[service_code] = chosen
            self._apply_embedding_backend()
            dialog.destroy()

        ttk.Button(dialog, text="Use Selected Embedding Model", command=_on_confirm).pack(pady=10)


if __name__ == "__main__":
    app = AutomatedLiteratureUI()
    app.mainloop()
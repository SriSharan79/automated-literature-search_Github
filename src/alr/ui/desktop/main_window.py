import inspect
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
        self._build_evaluation_tab()
        self._build_enrichment_tab()

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

        # The LLM call runs on a worker thread so the UI stays responsive; the
        # derived scope is written back to the entry on the main thread.
        def work(progress, should_cancel):
            print("\n[LLM System Process] Deriving standard research scope limits definitions...")
            progress(text="Deriving the research scope via the LLM…")
            scope_inputs = f"\n1. Research Area/Topic: {ra}\n2. Key Research Questions/Gaps: {rq}"
            return llm_call(scope_inputs, SCOPE_DERIVATOR_PROMPT, service)

        def on_success(derived_scope):
            self.scope_entry.delete(0, tk.END)
            self.scope_entry.insert(0, derived_scope or "")
            print(f"Scope Derived: {derived_scope}")

        self._run_threaded(work, "Derive Scope", on_success=on_success)

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
        suggest = self.suggest_kw_var.get()

        # LLM suggestion + phrase processing run on the worker thread; the
        # keyword-refinement pop-up / manual-input dialog are Tk modals, so the
        # worker hands them to the main thread via the ask() round-trip.
        def work(progress, should_cancel, ask):
            keywords_list = []
            if suggest:
                progress(text="Requesting keyword suggestions from the LLM…")
                kw_prompt_inputs = f"\n1. Research Area/Topic: {self.CM.Research_Area}\n2. Key Research Questions/Gaps: {self.CM.Research_Question}\n3. Refined Scope: To {refined_scope}"
                raw_keywords = llm_call(kw_prompt_inputs, KEYWORD_GENERATOR_PROMPT, service)
                suggested = Proccess_string_to_list(raw_keywords)
                progress(text="Waiting for the keyword selection…")
                keywords_list = ask(lambda app: app._prompt_keyword_indices_selection(suggested))
            else:
                progress(text="Waiting for the manual keyword input…")

                def manual(app):
                    manual_input = filedialog.SimpleDialog(
                        app, text="Enter comma-separated keywords:", title="Manual Keywords Choice Input")
                    user_string = manual_input.go()
                    if user_string:
                        return [item.strip() for item in user_string.split(",") if item.strip()]
                    return []
                keywords_list = ask(manual)

            if not keywords_list:
                print("Action halted or keywords context returned blank frame arrays.")
                return 0

            progress(text=f"Processing {len(keywords_list)} keyword(s) with the scope…")
            self.CM.update_Keyword_list(keywords_list)
            log_Keyword_Json(self.CM)
            self.CM = Keywords_Processing_with_scope(self.CM)
            print(f"\nSuccessfully logged structural pipeline setups. Ready to rank/export across {self.CM.Search_phrase_count} expressions.")
            return self.CM.Search_phrase_count

        def on_success(count):
            if count:
                self.btn_scholarly.configure(state="normal")
                self.btn_save_excel.configure(state="normal")

        self._run_threaded(work, "Process Keywords", on_success=on_success)

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

        # The Scholarly scrape is slow network work -> worker thread, with a
        # determinate bar over the phrases. The Excel-only export is quick but
        # goes through the same funnel so every pass behaves identically.
        def work(progress, should_cancel):
            if choice_mode == "s":
                print(f"\nRunning Scholarly search framework profiles matching ranking setup: {rank_col}")
                progress(text=f"Searching Scholarly across {len(sorted_phrases)} phrase(s)…")
                results = run_scholarly(
                    sorted_phrases, self.CM, 15,
                    progress_callback=lambda d, t, phrase: progress(
                        done=d, total=t, text=f"[{d}/{t}] Scholarly: {phrase}"))
                if not results:
                    print("Fallback triggered automatically: Saving calculations into target spreadsheet.")
                    get_values_from_sorted_numbers_and_save(phrase_excel_file, rank_col, 'Phrase', num_phrases, sp_sorted_path)
            else:
                print(f"\nExtracting top numerical items context vectors targets to location mapping index matching file: {sp_sorted_path}")
                progress(text="Saving the ranked phrases to Excel…")
                get_values_from_sorted_numbers_and_save(phrase_excel_file, rank_col, 'Phrase', num_phrases, sp_sorted_path)

            print("Collection Operations Sequence Completed.")
            return len(sorted_phrases)

        title = "Scholarly Search" if choice_mode == "s" else "Save Ranking to Excel"
        self._run_threaded(work, title, "processed",
                           on_success=lambda n: print(f"[Collection] {title} finished ({n} phrase(s))."))

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
            "results": tk.BooleanVar(value=True),
            "references": tk.BooleanVar(value=False),
            "doi": tk.BooleanVar(value=True),
            "classification": tk.BooleanVar(value=True),
            "text_db": tk.BooleanVar(value=False),
            "vector_db": tk.BooleanVar(value=False),
        }
        # Sections (incl. tables/images) is a required prerequisite -> checked + disabled.
        ttk.Checkbutton(comp_frame, text="Sections (incl. tables/images) — required",
                        variable=self.comp_vars["sections"], state="disabled").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        ttk.Checkbutton(comp_frame, text="Abstract", variable=self.comp_vars["abstract"]).grid(row=0, column=1, sticky="w", padx=6, pady=3)
        ttk.Checkbutton(comp_frame, text="Introduction", variable=self.comp_vars["intro"]).grid(row=0, column=2, sticky="w", padx=6, pady=3)
        ttk.Checkbutton(comp_frame, text="Results & Conclusion", variable=self.comp_vars["results"]).grid(row=0, column=3, sticky="w", padx=6, pady=3)
        ttk.Checkbutton(comp_frame, text="References", variable=self.comp_vars["references"]).grid(row=1, column=0, sticky="w", padx=6, pady=3)
        ttk.Checkbutton(comp_frame, text="DOI / metadata", variable=self.comp_vars["doi"]).grid(row=1, column=1, sticky="w", padx=6, pady=3)
        ttk.Checkbutton(comp_frame, text="Classification", variable=self.comp_vars["classification"]).grid(row=1, column=2, sticky="w", padx=6, pady=3)
        # RAG database builds, now selectable separately. Both are incremental
        # syncs (only missing entries/vectors are added, nothing is rebuilt from
        # scratch). The vector DB is built FROM the text DB's section JSON files,
        # so vector-only runs assume the text DB is already up to date.
        ttk.Checkbutton(comp_frame, text="Build Text DB (RAG: section JSON + Excel)",
                        variable=self.comp_vars["text_db"]).grid(row=2, column=0, sticky="w", padx=6, pady=3)
        ttk.Checkbutton(comp_frame, text="Build Vector DB (RAG: FAISS embeddings)",
                        variable=self.comp_vars["vector_db"]).grid(row=2, column=1, sticky="w", padx=6, pady=3)

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
        components = {c for c in ("abstract", "intro", "results", "references") if self.comp_vars[c].get()}
        do_doi = self.comp_vars["doi"].get()
        do_classify = self.comp_vars["classification"].get()
        do_text_db = self.comp_vars["text_db"].get()
        do_vector_db = self.comp_vars["vector_db"].get()
        print(f"[Selection] Components: sections (required)"
              + "".join(f", {c}" for c in ("abstract", "intro", "results", "references") if c in components)
              + (", doi/metadata" if do_doi else "") + (", classification" if do_classify else "")
              + (", text DB" if do_text_db else "") + (", vector DB" if do_vector_db else ""))

        # The per-document pass reuses whatever a previous dated file already holds
        # (falling back to a fresh run when there is nothing to copy), so a re-run
        # never silently pays for the same LLM call twice. The real copy-vs-generate
        # decision is made at the END, by the finalization dialog, which can look at
        # what actually landed in SQL instead of guessing up front.
        class_mode = "copy"
        eval_mode = "copy"

        # The whole analysis + enrichment chain runs on a background thread with a
        # progress dialog so the UI stays responsive and cancellable. Each document
        # is taken through ALL selected steps before moving to the next one (rather
        # than stage-by-stage), and every step is precheck-driven: existing data is
        # copied across storage/SQL instead of recomputed.
        def work(progress, should_cancel, ask):
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

            # --- Finalization: refresh SQL, then work out what is still missing and
            # let the user decide, per stage, whether to reuse data an earlier dated
            # file already holds, run the stage fresh, or skip it. ---
            progress(text="Finalizing: syncing the review database…")
            try:
                synced = sync_storage_to_sql(MF)
                print(f"[Database Sync] {synced} document(s) written to the review database.")
            except Exception as e:
                print(f"[Database Sync] Skipped/failed: {e}")

            progress(text="Finalizing: checking the database for missing data…")
            try:
                from alr.common.analysis_precheck import compute_space_gaps
                gaps = compute_space_gaps(MF)
            except Exception as e:
                print(f"[Completeness Check] Skipped/failed: {e}")
                gaps = {}

            decisions = {}
            if gaps and not should_cancel():
                for stage, info in gaps.items():
                    print(f"[Completeness Check] {info['label']}: {len(info['missing'])} missing"
                          f" ({len(info['reusable'])} reusable from previous files).")
                # Modal on the main thread; the worker blocks until the user answers.
                decisions = ask(lambda app: app._finalization_gap_dialog(gaps)) or {}
            else:
                print("[Completeness Check] The review database is already up to date.")

            def mode_for(stage):
                """copy/generate for a stage the user wants filled, else None (skip)."""
                choice = decisions.get(stage, "skip")
                return {"reuse": "copy", "fresh": "generate"}.get(choice)

            for stage, target, label in (("eval_abstract", "abstract", "Evaluation"),
                                         ("eval_intro", "intro", "Intro Evaluation"),
                                         ("eval_rescon", "rescon", "Results & Conclusion Evaluation")):
                mode = mode_for(stage)
                if not mode or should_cancel():
                    continue
                progress(text=f"Finalizing: {label.lower()} sweep…")
                try:
                    from alr.analysis_evaluation.data_evaluator import evaluate_space
                    evaluate_space(MF, should_cancel=should_cancel, mode=mode, target=target,
                                   progress_callback=lambda d, t, lab=label: progress(
                                       done=d, total=t, text=f"{lab} {d}/{t}…"))
                except Exception as e:
                    print(f"[{label}] Skipped/failed: {e}")

            if mode_for("doi") and not should_cancel():
                # Reuse pulls metadata already sitting in the download logs; fresh
                # re-runs the DOI lookup for the space.
                progress(text="Finalizing: DOI / metadata…")
                try:
                    if decisions.get("doi") == "fresh":
                        from alr.data_analysis.doi_metadata import enrich_space_with_doi
                        enrich_space_with_doi(MF, should_cancel=should_cancel,
                                              progress_callback=lambda d, t, name: progress(
                                                  done=d, total=t, text=f"[{d}/{t}] DOI / metadata: {name}"))
                    else:
                        from alr.common.download_log_enrich import enrich_from_download_logs
                        from alr.common.file_manager import ALR_main_folder
                        enrich_from_download_logs(ALR_main_folder, should_cancel=should_cancel,
                                                  progress_callback=lambda d, t: progress(
                                                      done=d, total=t, text=f"Download-log enrichment {d}/{t}…"))
                except Exception as e:
                    print(f"[DOI Enrichment] Skipped/failed: {e}")

            for stage, kind, fn_name in (("title_class", "title", "classify_space"),
                                         ("abstract_class", "abstract", "classify_abstract_space")):
                mode = mode_for(stage)
                if not mode or should_cancel():
                    continue
                progress(text=f"Finalizing: {kind} classification sweep…")
                try:
                    from alr.analysis_evaluation.publication_classification import classify_runner
                    getattr(classify_runner, fn_name)(
                        MF, should_cancel=should_cancel, service=service, mode=mode,
                        progress_callback=lambda d, t, k=kind: progress(
                            done=d, total=t, text=f"Classifying {k}s {d}/{t}…"))
                except Exception as e:
                    print(f"[Classification] {kind}: Skipped/failed: {e}")

            # Extraction gaps: "reuse" means the analysis JSON is already on disk and
            # only SQL was behind — the closing sync below picks it up. "fresh" has to
            # re-analyze the PDF, which is only possible while its source file is known.
            fresh_components = {c for stage, c in (("intro_extract", "intro"),
                                                   ("references_extract", "references"))
                                if decisions.get(stage) == "fresh"}
            if fresh_components and not should_cancel():
                progress(text=f"Finalizing: re-extracting {', '.join(sorted(fresh_components))}…")
                for pdf in to_process:
                    if should_cancel():
                        break
                    try:
                        process_pdf_mode_file(str(pdf), str(MF.folder), components=fresh_components,
                                              doc_converter=doc_converter, eval_mode=eval_mode)
                    except Exception as e:
                        print(f"[Re-extraction] {Path(pdf).name}: {e}")
                if not to_process:
                    print("[Re-extraction] No source PDFs in this run to re-extract from.")

            if decisions and not should_cancel():
                progress(text="Finalizing: saving filled-in data…")
                try:
                    sync_storage_to_sql(MF)
                except Exception as e:
                    print(f"[Database Sync] Skipped/failed: {e}")

            # Build the RAG databases when requested (heavy; last). Text DB and
            # vector DB are separate opt-ins; both syncs are incremental (see
            # db_manager.generate_databases), so re-runs only add missing data.
            if (do_text_db or do_vector_db) and not should_cancel():
                parts = (["text DB"] if do_text_db else []) + (["vector DB"] if do_vector_db else [])
                progress(text=f"Building {' + '.join(parts)}…")
                try:
                    from alr.rag_builders.db_manager import generate_databases as build_rag_databases
                    build_rag_databases(str(MF.folder), do_text=do_text_db, do_vector=do_vector_db,
                                        progress_callback=lambda d, t, txt: progress(
                                            done=d, total=t, text=f"[{d}/{t}] {txt}"))
                except Exception as e:
                    print(f"[RAG DB] Skipped/failed: {e}")

            # Empty files/folders the managers pre-created are pruned by
            # _run_threaded once this pass returns, like every other pass.
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

        A worker that declares a third parameter also receives ``ask(handler)``:
        it runs ``handler(self)`` on the **main** thread (Tk is not thread-safe)
        and blocks until it returns, so a pass can pop a modal mid-run and act on
        the answer. Workers with the plain ``(progress, should_cancel)`` signature
        are called unchanged.
        """
        cancel_event = threading.Event()
        dlg = ProgressDialog(self, title, on_cancel=cancel_event.set)
        q = queue.Queue()
        outcome = {}

        def progress(**kw):
            q.put(("progress", kw))

        def ask(handler):
            """Run ``handler(self)`` on the main thread; block for its result."""
            done = threading.Event()
            holder = {}
            q.put(("ask", (handler, holder, done)))
            done.wait()
            return holder.get("result")

        def worker():
            result, failed = None, False
            try:
                args = [progress, cancel_event.is_set]
                try:
                    if len(inspect.signature(work).parameters) >= 3:
                        args.append(ask)
                except (TypeError, ValueError):
                    pass
                result = work(*args)
            except Exception as e:  # noqa: BLE001 - surface any failure to the UI
                failed = True
                log_path = crash_logger.write_crash_log(
                    *sys.exc_info(), origin=f"background task: {title}")
                q.put(("error", (e, log_path)))

            # Every pass tidies up after itself. The managers build their whole
            # folder tree on construction, so any run can leave empty folders
            # behind; each one registers its root, and this prunes exactly those
            # trees. Runs on this worker thread (it is file I/O), and after a
            # failure or a cancel too -- that is when strays are most likely.
            try:
                progress(text="Cleaning up empty files and folders…")
                from alr.common.artifact_cleanup import prune_touched_folders
                prune_touched_folders()
            except Exception as e:  # noqa: BLE001 - cleanup must never fail a pass
                print(f"[Cleanup] Skipped/failed: {e}")

            if not failed:
                q.put(("done", result))

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
                    elif kind == "ask":
                        # Main-thread modal requested by the worker; it is blocked
                        # on `done` until we hand the answer back.
                        handler, holder, done = payload
                        try:
                            holder["result"] = handler(self)
                        except Exception as e:  # noqa: BLE001 - never strand the worker
                            print(f"[Dialog] {e}")
                            holder["result"] = None
                        finally:
                            done.set()
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
        tab = self._make_scrollable_tab("3. Visualize & Query")

        v_frame = tk.LabelFrame(tab, text="RAG Database Query Engine (Vector DB Layout Profiles)")
        v_frame.pack(fill="x", padx=10, pady=10)

        # Storage Vector Path Layout Selection parameters settings setup
        db_path_frame = ttk.Frame(v_frame)
        db_path_frame.pack(fill="x", padx=5, pady=10)

        ttk.Label(db_path_frame, text="Select Data Storage Engine Source Directory Path:").pack(anchor="w", padx=5)
        self.visualize_storage_entry = ttk.Entry(db_path_frame, width=65)
        self.visualize_storage_entry.pack(side="left", padx=5, pady=5)
        ttk.Button(db_path_frame, text="Browse...", command=lambda: self._browse_folder(self.visualize_storage_entry)).pack(side="left", padx=2, pady=5)

        # Query target: the storage space above, or the combined common DB
        # maintained in the frame below.
        target_frame = ttk.Frame(v_frame)
        target_frame.pack(fill="x", padx=5, pady=(0, 5))
        ttk.Label(target_frame, text="Query target:").pack(side="left", padx=5)
        self.query_target_var = tk.StringVar(value="space")
        ttk.Radiobutton(target_frame, text="Selected storage space", value="space",
                        variable=self.query_target_var).pack(side="left", padx=5)
        ttk.Radiobutton(target_frame, text="Common database (all combined spaces)", value="common",
                        variable=self.query_target_var).pack(side="left", padx=5)

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

        # Query scope: pick exactly which analyzed sections to search —
        # any mix of abstract, Introduction and Results & Conclusion
        # attributes (their DBs must have been built; missing ones are
        # skipped with a console warning).
        ttk.Label(v_frame, text="Sections to query:").pack(anchor="w", padx=10)
        self.query_section_vars = self._make_section_checkbox_grid(v_frame)

        # Top-k: how many best matches to fetch per section index.
        topk_frame = ttk.Frame(v_frame)
        topk_frame.pack(fill="x", padx=5, pady=(0, 5))
        ttk.Label(topk_frame, text="Top matches per section (top-k):").pack(side="left", padx=5)
        self.query_topk_var = tk.StringVar(value="50")
        ttk.Spinbox(topk_frame, from_=1, to=1000, increment=1, width=6,
                    textvariable=self.query_topk_var).pack(side="left", padx=5)

        btn_run_query = ttk.Button(v_frame, text="Generate Query Report", command=self._run_visualization_query_action)
        btn_run_query.pack(pady=20, ipadx=10, ipady=5)

        self._build_common_db_frame(tab)

    def _make_section_checkbox_grid(self, parent, default=True):
        """
        One checkbox per RAG section (abstract + Introduction + Results &
        Conclusion attributes), grouped by their analysis source. Returns
        {section_key: BooleanVar}. Used by both the query-scope selector and
        the Common Database frame.
        """
        from alr.common.sections import ALL_RAG_SECTIONS, RAG_SOURCE_BY_KEY
        group_labels = (("abstract", "Abstract"), ("intro", "Introduction"),
                        ("rescon", "Results & Conclusion"))
        section_vars = {}
        for source, label in group_labels:
            keys = [s.key for s in ALL_RAG_SECTIONS if RAG_SOURCE_BY_KEY[s.key] == source]
            row = ttk.Frame(parent)
            row.pack(fill="x", padx=10, pady=(0, 2))
            ttk.Label(row, text=f"{label}:", width=20).grid(row=0, column=0, sticky="nw")
            for i, key in enumerate(keys):
                var = tk.BooleanVar(value=default)
                section_vars[key] = var
                ttk.Checkbutton(row, text=key, variable=var).grid(
                    row=i // 4, column=1 + i % 4, sticky="w", padx=4, pady=1)
        return section_vars

    def _finalization_gap_dialog(self, gaps):
        """
        Ask, in ONE modal, what to do about each enrichment stage that is still
        missing from the SQL database after a run (see
        ``analysis_precheck.compute_space_gaps``).

        Per stage the user picks: reuse the data an earlier dated file / analysis
        JSON already holds (no LLM cost), run the stage fresh, or skip it.
        "Reuse" is offered only where something is actually reusable, and is the
        default when it covers every missing document. Returns
        ``{stage: "reuse"|"fresh"|"skip"}`` -- all "skip" if the user closes or
        cancels the dialog.
        """
        win = tk.Toplevel(self)
        win.title("Incomplete data found")
        win.transient(self)
        win.resizable(False, False)

        ttk.Label(win, text="Some data is missing from the review database.",
                  font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=12, pady=(12, 2))
        ttk.Label(win, text="Choose what to do for each item. \"Reuse existing\" copies data an\n"
                            "earlier dated file already holds instead of re-running it.").pack(
            anchor="w", padx=12, pady=(0, 8))

        choices = {}
        for stage, info in gaps.items():
            missing, reusable = len(info["missing"]), len(info["reusable"])
            row = ttk.LabelFrame(win, text=info["label"])
            row.pack(fill="x", padx=12, pady=3)

            summary = f"{missing} document(s) missing"
            if reusable:
                summary += f" — {reusable} available in a previous file"
            ttk.Label(row, text=summary).pack(anchor="w", padx=8, pady=(4, 2))

            # Reuse only makes sense when something is reusable, and is the
            # default only when it closes the whole gap.
            var = tk.StringVar(value="reuse" if reusable >= missing else "fresh")
            choices[stage] = var
            btns = ttk.Frame(row)
            btns.pack(anchor="w", padx=8, pady=(0, 5))
            ttk.Radiobutton(btns, text="Reuse existing", value="reuse", variable=var,
                            state=("normal" if reusable else "disabled")).pack(side="left", padx=(0, 10))
            ttk.Radiobutton(btns, text="Run fresh", value="fresh", variable=var).pack(side="left", padx=(0, 10))
            ttk.Radiobutton(btns, text="Skip", value="skip", variable=var).pack(side="left")

        result = {}

        def apply_choices():
            result.update({stage: var.get() for stage, var in choices.items()})
            win.destroy()

        action = ttk.Frame(win)
        action.pack(fill="x", padx=12, pady=(6, 12))
        ttk.Button(action, text="Continue", command=apply_choices).pack(side="right", padx=4)
        ttk.Button(action, text="Skip all", command=win.destroy).pack(side="right", padx=4)

        win.grab_set()
        self.wait_window(win)
        # Closing the window without confirming means "do nothing".
        return result or {stage: "skip" for stage in gaps}

    def _pick_attributes_dialog(self, title, default_keys=None):
        """
        Modal checkbox picker over every analyzed attribute (abstract +
        Introduction + Results & Conclusion). Returns the selected section keys,
        or ``None`` if the user cancels. Used before building the master Excel
        workbook and before enriching a query report, so only the attributes the
        user wants are written.
        """
        from alr.common.sections import ALR_SECTIONS

        # Default: the abstract attributes (what these builders wrote before).
        default_keys = set(default_keys) if default_keys is not None else {
            s.key for s in ALR_SECTIONS}

        win = tk.Toplevel(self)
        win.title(title)
        win.transient(self)
        win.resizable(False, False)

        ttk.Label(win, text="Select the attributes to include:",
                  font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=12, pady=(12, 6))

        body = ttk.Frame(win)
        body.pack(fill="x", padx=2)
        section_vars = self._make_section_checkbox_grid(body, default=False)
        for key, var in section_vars.items():
            var.set(key in default_keys)

        selected = {}

        def confirm():
            selected["keys"] = [k for k, v in section_vars.items() if v.get()]
            win.destroy()

        def toggle_all(value):
            for var in section_vars.values():
                var.set(value)

        action = ttk.Frame(win)
        action.pack(fill="x", padx=12, pady=(8, 12))
        ttk.Button(action, text="All", width=6, command=lambda: toggle_all(True)).pack(side="left", padx=2)
        ttk.Button(action, text="None", width=6, command=lambda: toggle_all(False)).pack(side="left", padx=2)
        ttk.Button(action, text="Cancel", command=win.destroy).pack(side="right", padx=4)
        ttk.Button(action, text="Build", command=confirm).pack(side="right", padx=4)

        win.grab_set()
        self.wait_window(win)
        return selected.get("keys")

    def _build_common_db_frame(self, tab):
        """
        Common Database frame: combine the text + vector DBs of several
        storage spaces into ONE queryable location (the RAG counterpart of
        the app-wide SQL database). The build is incremental — only documents
        not yet in the common DB (by UUID / Title / optionally Filename) are
        added — and prefers each space's SQL-linked data over re-reading files.
        """
        c_frame = tk.LabelFrame(tab, text="Common Database (combine storage spaces into one queryable DB)")
        c_frame.pack(fill="x", padx=10, pady=(0, 10))

        path_row = ttk.Frame(c_frame)
        path_row.pack(fill="x", padx=5, pady=(8, 4))
        ttk.Label(path_row, text="Common DB folder:").pack(side="left", padx=5)
        self.common_db_entry = ttk.Entry(path_row, width=55)
        self.common_db_entry.pack(side="left", padx=5)
        ttk.Button(path_row, text="Browse...",
                   command=lambda: self._browse_folder(self.common_db_entry)).pack(side="left", padx=2)

        ttk.Label(c_frame, text="Source storage spaces to combine:").pack(anchor="w", padx=10)
        list_row = ttk.Frame(c_frame)
        list_row.pack(fill="x", padx=10, pady=(2, 4))
        self.common_sources_list = tk.Listbox(list_row, height=5, selectmode="extended")
        self.common_sources_list.pack(side="left", fill="x", expand=True)
        list_sb = ttk.Scrollbar(list_row, orient="vertical", command=self.common_sources_list.yview)
        self.common_sources_list.configure(yscrollcommand=list_sb.set)
        list_sb.pack(side="left", fill="y")
        btn_col = ttk.Frame(list_row)
        btn_col.pack(side="left", fill="y", padx=(6, 0))
        ttk.Button(btn_col, text="Add space…", command=self._add_common_source).pack(fill="x", pady=1)
        ttk.Button(btn_col, text="Scan folder for spaces…", command=self._scan_common_sources).pack(fill="x", pady=1)
        ttk.Button(btn_col, text="Remove selected", command=self._remove_common_sources).pack(fill="x", pady=1)

        opt_row = ttk.Frame(c_frame)
        opt_row.pack(fill="x", padx=10, pady=(0, 4))
        self.common_match_filename_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_row,
                        text="Also treat matching filenames as duplicates (besides UUID and Title)",
                        variable=self.common_match_filename_var).pack(side="left")

        # Attributes (sections) to include in the common DB — abstract,
        # Introduction and Results & Conclusion analysis data. A document
        # already in the common DB that lacks a newly ticked attribute gets
        # only that attribute copied on the next build; documents whose
        # space has no data for a ticked attribute simply skip it.
        ttk.Label(c_frame, text="Attributes (sections) to include:").pack(anchor="w", padx=10)
        self.common_section_vars = self._make_section_checkbox_grid(c_frame)

        ttk.Button(c_frame, text="Build / Update Common DB",
                   command=self._build_common_db_action).pack(pady=(4, 10), ipadx=10, ipady=4)

        self._load_common_db_config()

    # --- Common DB config persistence (like the fixed SQL DB location) ---

    @staticmethod
    def _common_db_config_path():
        from alr.common.file_manager import ALR_main_folder
        return Path(ALR_main_folder) / "common_db_config.json"

    def _load_common_db_config(self):
        import json as _json
        from alr.common.file_manager import ALR_main_folder
        cfg = {}
        try:
            path = self._common_db_config_path()
            if path.exists():
                cfg = _json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[Common DB] Could not load saved configuration: {e}")
        common_path = cfg.get("common_path") or str(Path(ALR_main_folder) / "00_Common_DB")
        self.common_db_entry.delete(0, "end")
        self.common_db_entry.insert(0, common_path)
        self.common_sources_list.delete(0, "end")
        for src in cfg.get("sources", []):
            self.common_sources_list.insert("end", src)
        self.common_match_filename_var.set(bool(cfg.get("match_filename", True)))
        saved_sections = cfg.get("sections")
        if isinstance(saved_sections, list):
            for key, var in self.common_section_vars.items():
                var.set(key in saved_sections)

    def _save_common_db_config(self):
        import json as _json
        try:
            cfg = {
                "common_path": self.common_db_entry.get().strip(),
                "sources": list(self.common_sources_list.get(0, "end")),
                "match_filename": self.common_match_filename_var.get(),
                "sections": [k for k, v in self.common_section_vars.items() if v.get()],
            }
            self._common_db_config_path().write_text(
                _json.dumps(cfg, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[Common DB] Could not save configuration: {e}")

    # --- Common DB source-list handlers ---

    def _add_common_source(self):
        path = filedialog.askdirectory(title="Select a storage space to combine")
        if not path:
            return
        existing = set(self.common_sources_list.get(0, "end"))
        if path not in existing:
            self.common_sources_list.insert("end", path)

    def _scan_common_sources(self):
        root = filedialog.askdirectory(title="Select a folder to scan for storage spaces")
        if not root:
            return
        from alr.common.storage_scanner import detect_storage_spaces
        spaces = detect_storage_spaces(root)
        existing = set(self.common_sources_list.get(0, "end"))
        added = 0
        for space in spaces:
            if space.path not in existing:
                self.common_sources_list.insert("end", space.path)
                added += 1
        messagebox.showinfo("Scan for storage spaces",
                            f"Found {len(spaces)} storage space(s); added {added} new one(s) to the list.")

    def _remove_common_sources(self):
        for idx in reversed(self.common_sources_list.curselection()):
            self.common_sources_list.delete(idx)

    def _build_common_db_action(self):
        common_path = clean_folder_path(self.common_db_entry.get().strip())
        sources = [clean_folder_path(s) for s in self.common_sources_list.get(0, "end")]

        if not common_path:
            messagebox.showerror("Error", "Please set the Common DB folder first.")
            return
        if not sources:
            messagebox.showerror("Error", "Please add at least one source storage space to combine.")
            return
        section_keys = [k for k, v in self.common_section_vars.items() if v.get()]
        if not section_keys:
            messagebox.showerror("Error", "Please select at least one attribute (section) to include.")
            return

        match_filename = self.common_match_filename_var.get()
        self._save_common_db_config()

        skip_info = {}

        def work(progress, should_cancel):
            from alr.rag_builders.db_manager import build_common_database
            added, skipped, extended = build_common_database(
                sources, common_path, match_filename=match_filename,
                section_keys=section_keys,
                progress_callback=lambda d, t, txt: progress(done=d, total=t, text=txt),
                should_cancel=should_cancel,
            )
            skip_info["skipped"] = skipped
            skip_info["extended"] = extended
            return added

        def on_success(added):
            extended = skip_info.get("extended", 0)
            extended_line = (f"{extended} existing document(s) extended with newly "
                             f"selected attribute(s).\n" if extended else "")
            messagebox.showinfo(
                "Common Database",
                f"Common DB updated: {added or 0} new document(s) added, "
                f"{skip_info.get('skipped', 0)} already in the common DB (skipped, not reprocessed).\n"
                f"{extended_line}\n"
                f"Location: {common_path}")

        self._run_threaded(work, "Building Common Database", "added", on_success=on_success)

    def _run_visualization_query_action(self):
        query_common = getattr(self, "query_target_var", None) and self.query_target_var.get() == "common"
        if query_common:
            storage_choice = self.common_db_entry.get().strip()
        else:
            storage_choice = self.visualize_storage_entry.get().strip()
        query_text = self.query_entry.get().strip()

        if not storage_choice or not query_text:
            if query_common and not storage_choice:
                messagebox.showerror("Error", "Query target is the Common DB, but no Common DB folder is set below.")
            else:
                messagebox.showerror("Error", "Please make sure to supply both data engine reference destination parameters and active query statements strings text models templates.")
            return

        try:
            top_k = int(self.query_topk_var.get().strip())
            if top_k < 1:
                raise ValueError
        except (ValueError, AttributeError):
            messagebox.showerror("Error", "Top-k must be a whole number of 1 or more "
                                          "(how many best matches to fetch per section).")
            return

        query_sections = [k for k, v in self.query_section_vars.items() if v.get()]
        if not query_sections:
            messagebox.showerror("Error", "Please tick at least one section to query.")
            return

        # Which attributes to add as columns on the enriched overview report. This
        # is independent of what was searched (a match on one section can still be
        # reported alongside every other attribute), so ask, defaulting to the
        # sections being queried.
        enrich_keys = self._pick_attributes_dialog(
            "Query report — attributes to include", default_keys=query_sections)
        if enrich_keys is None:
            return
        if not enrich_keys:
            messagebox.showerror("Error", "Please select at least one attribute to include.")
            return

        target_label = "Common DB" if query_common else "storage space"
        print(f"[Query Pipeline Dispatch] Querying the {target_label} at: {storage_choice}")
        print(f"[Query Pipeline Dispatch] Running query text profiling execution match targeting expression: '{query_text}' (top-k={top_k})")
        print(f"[Query Scope] Sections: {', '.join(query_sections)}")
        print(f"[Query Report] Attributes included: {', '.join(enrich_keys)}")

        # The query itself (vector search + report building + JSON harvest) runs
        # on the worker thread with a determinate bar: one tick per searched
        # section plus the overview and harvest steps.
        def work(progress, should_cancel):
            progress(text=f"Querying {len(query_sections)} section(s)…")
            generate_query_report(
                [query_text], storage_choice, top_k=top_k,
                section_keys=query_sections, enrich_keys=enrich_keys,
                progress_callback=lambda d, t, txt: progress(done=d, total=t, text=txt))
            print("Query Generation Suite Logging Executed successfully.")
            return len(query_sections)

        def on_success(n):
            messagebox.showinfo("Query finished",
                                f"Query report built across {n or 0} section(s). "
                                "See the console log for the report location.")

        self._run_threaded(work, "RAG Query", on_success=on_success)

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
    def _make_scrollable_tab(self, title):
        """
        Add a notebook tab whose content can grow past the window height:
        the content lives inside a vertical-scroll canvas. Returns the inner
        frame that tab content should be packed into.
        """
        outer = ttk.Frame(self.notebook)
        self.notebook.add(outer, text=title)
        canvas = tk.Canvas(outer, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        tab = ttk.Frame(canvas)
        tab_window = canvas.create_window((0, 0), window=tab, anchor="nw")
        tab.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(tab_window, width=e.width))
        return tab

    def _build_shared_eval_inputs(self, tab):
        """
        Build one copy of the shared Storage-Space / LLM / Embedding inputs.

        Both the Evaluation and the Enrichment tab carry a copy of these
        widgets; every copy is bound to the same tk variables, so setting the
        storage folder or LLM service on one tab shows on the other as well.
        """
        if not hasattr(self, "eval_storage_var"):
            self.eval_storage_var = tk.StringVar(value="")
            self.eval_llm_var = tk.StringVar(value="B")

        shared = tk.LabelFrame(tab, text="Storage Space & LLM Service (shared between the Evaluation and Enrichment tabs)")
        shared.pack(fill="x", padx=10, pady=(10, 5))

        row = ttk.Frame(shared)
        row.pack(fill="x", padx=5, pady=8)
        ttk.Label(row, text="Storage folder:").pack(side="left", padx=5)
        entry = ttk.Entry(row, width=55, textvariable=self.eval_storage_var)
        entry.pack(side="left", padx=5)
        self.eval_storage_entry = entry
        ttk.Button(row, text="Browse...", command=lambda: self._browse_folder(entry)).pack(side="left", padx=2)

        llm_row = ttk.Frame(shared)
        llm_row.pack(fill="x", padx=5, pady=(0, 8))
        ttk.Label(llm_row, text="LLM Processing Service Engine:").pack(side="left", padx=5)
        combo = ttk.Combobox(llm_row, values=["O", "B"], width=5, state="readonly",
                             textvariable=self.eval_llm_var)
        combo.pack(side="left", padx=5)
        self.llm_choice_eval = combo
        ttk.Button(llm_row, text="Choose Model...",
                   command=lambda: self._choose_model_action(self.eval_llm_var.get())
                   ).pack(side="left", padx=5)
        ttk.Label(llm_row, text="(used by Classify Titles / Classify Abstracts)").pack(side="left", padx=5)

        # Embedding engine (used by the cosine-similarity evaluation and by
        # vector-DB builds/queries; all copies stay in sync).
        self._build_embedding_selector(shared).pack(fill="x", padx=5, pady=(0, 8))

    def _build_evaluation_tab(self):
        tab = self._make_scrollable_tab("5. Evaluation")
        self._build_shared_eval_inputs(tab)

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

        # What to do with documents that already have results for the selected
        # evaluation types: reuse them (only new documents are computed) or
        # recompute everything and rewrite the stored rows in place.
        mode_row = ttk.Frame(eval_frame)
        mode_row.pack(fill="x", padx=5, pady=(2, 2))
        ttk.Label(mode_row, text="Previously evaluated documents:").pack(side="left", padx=6)
        self.eval_mode_var = tk.StringVar(value="copy")
        ttk.Radiobutton(mode_row, text="Use existing results (only evaluate new documents)",
                        value="copy", variable=self.eval_mode_var).pack(side="left", padx=4)
        ttk.Radiobutton(mode_row, text="Rewrite (recompute & update all documents)",
                        value="generate", variable=self.eval_mode_var).pack(side="left", padx=4)

        target_row = ttk.Frame(eval_frame)
        target_row.pack(fill="x", padx=5, pady=(2, 2))
        ttk.Label(target_row, text="Evaluate:").pack(side="left", padx=6)
        self.eval_target_vars = {
            "abstract": tk.BooleanVar(value=True),
            "intro": tk.BooleanVar(value=False),
            "rescon": tk.BooleanVar(value=False),
        }
        ttk.Checkbutton(target_row, text="Abstract data",
                        variable=self.eval_target_vars["abstract"]).pack(side="left", padx=4)
        ttk.Checkbutton(target_row, text="Introduction data",
                        variable=self.eval_target_vars["intro"]).pack(side="left", padx=4)
        ttk.Checkbutton(target_row, text="Results & Conclusion data",
                        variable=self.eval_target_vars["rescon"]).pack(side="left", padx=4)
        ttk.Button(target_row, text="Run Evaluation", command=self._run_evaluation_action).pack(side="left", padx=16)

        ttk.Label(eval_frame,
                  text="(The identified abstract/introduction/results&conclusion text is split into sentences and "
                       "every extracted item is measured against each sentence: the full sentence-level record goes "
                       "to a per-document JSON in 'Metric_Sentence_Details', the dated metric workbooks keep the "
                       "best value per item, and the review database gets only the workbook summary. Tip: run "
                       "'Build Text DB' + 'Build Vector DB' (Analyze tab) first so cosine reuses the stored vectors.)",
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

        # ---- Custom topic classification (user-defined tags) ----
        custom_frame = tk.LabelFrame(tab, text="Custom Topic Classification (user-defined tags)")
        custom_frame.pack(fill="x", padx=10, pady=5)

        crow1 = ttk.Frame(custom_frame)
        crow1.pack(fill="x", padx=5, pady=(8, 2))
        ttk.Label(crow1, text="Topic tag:").pack(side="left", padx=5)
        self.custom_topic_entry = ttk.Entry(crow1, width=28)
        self.custom_topic_entry.pack(side="left", padx=5)
        ttk.Label(crow1, text="Classify:").pack(side="left", padx=(14, 4))
        self.custom_source_var = tk.StringVar(value="title")
        ttk.Radiobutton(crow1, text="Titles", value="title",
                        variable=self.custom_source_var).pack(side="left", padx=2)
        ttk.Radiobutton(crow1, text="Abstracts", value="abstract",
                        variable=self.custom_source_var).pack(side="left", padx=2)
        ttk.Button(crow1, text="Run Custom Classification",
                   command=self._run_custom_classification_action).pack(side="left", padx=14)

        crow2 = ttk.Frame(custom_frame)
        crow2.pack(fill="x", padx=5, pady=(2, 2))
        ttk.Label(crow2, text="Classification tags (comma-separated):").pack(side="left", padx=5)
        self.custom_tags_entry = ttk.Entry(crow2, width=70)
        self.custom_tags_entry.pack(side="left", padx=5, fill="x", expand=True)

        ttk.Label(custom_frame,
                  text="(Classifies every document in the storage space above against YOUR tags: the "
                       "classification prompt is generated from them. Results go to a dated "
                       "'{date}_{Topic}_Classification.xlsx' workbook in the space and into the review "
                       "database under a column named after the topic tag — like 'classification' / "
                       "'abstract_classification'. Example: Topic tag 'UAV Safety', tags 'Drones, "
                       "Collision Avoidance, Certification, Autonomy'.)",
                  wraplength=860, justify="left").pack(anchor="w", padx=8, pady=(0, 6))

    def _build_enrichment_tab(self):
        tab = self._make_scrollable_tab("6. Enrichment")
        self._build_shared_eval_inputs(tab)

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

        targets = [t for t in ("abstract", "intro", "rescon") if self.eval_target_vars[t].get()]
        if not targets:
            messagebox.showerror("Error", "Select at least one data target "
                                          "(Abstract / Introduction / Results & Conclusion).")
            return
        eval_mode = self.eval_mode_var.get()
        print(f"[Evaluation] types: "
              + ", ".join((["substring"] if do_substring else []) + sorted(metric_kinds))
              + f" | target(s): {', '.join(targets)}"
              + f" | existing results: {'reuse (only new docs)' if eval_mode == 'copy' else 'rewrite all'}")

        def work(progress, should_cancel):
            from alr.common.sql_store import sync_storage_to_sql

            # Sync first so evaluation summaries land on existing SQL rows.
            progress(text="Syncing storage into the review database…")
            try:
                sync_storage_to_sql(DataAnalyzeManager(clean_path))
            except Exception as e:
                print(f"[Database Sync] Skipped/failed: {e}")

            n = 0
            target_labels = {"abstract": "abstract", "intro": "introduction",
                             "rescon": "results & conclusion"}
            for t in targets:
                label = target_labels[t]
                if should_cancel():
                    break
                if do_substring:
                    progress(text=f"Substring grounding evaluation ({label})…")
                    from alr.analysis_evaluation.data_evaluator import evaluate_space
                    n = max(n, evaluate_space(
                        clean_path, should_cancel=should_cancel, target=t, mode=eval_mode,
                        progress_callback=lambda d, tot, lab=label: progress(
                            done=d, total=tot, text=f"Substring evaluation ({lab})  {d}/{tot}…")))
                if metric_kinds and not should_cancel():
                    progress(text=f"Metric evaluation ({', '.join(sorted(metric_kinds))}) — {label}…")
                    from alr.analysis_evaluation.metric_evaluator import evaluate_space_metrics
                    n = max(n, evaluate_space_metrics(
                        clean_path, metric_kinds, target=t, should_cancel=should_cancel, mode=eval_mode,
                        progress_callback=lambda d, tot, lab=label: progress(
                            done=d, total=tot, text=f"Metric evaluation ({lab})  {d}/{tot}…")))
            return n

        self._run_threaded(work, "Run Evaluation", "evaluated")

    def _run_custom_classification_action(self):
        """
        Classify every document in the shared storage space against the
        user-defined tags: the classification prompt is generated from the
        tags, results go to a dated '{date}_{Topic}_Classification.xlsx'
        workbook and to a SQL column named after the topic tag.
        """
        folder = self.eval_storage_entry.get().strip()
        if not folder or not Path(folder).is_dir():
            messagebox.showerror("Error", "Select a valid analyzed storage folder first (shared inputs above).")
            return
        topic = self.custom_topic_entry.get().strip()
        if not topic:
            messagebox.showerror("Error", "Enter a Topic tag (it names the output file and the database column).")
            return
        # Accept pasted lists too: strip surrounding quotes from each tag, so
        # '"Hybrid Powertrain", "Electric Motor"' works the same as
        # 'Hybrid Powertrain, Electric Motor'.
        tags = [t.strip().strip('\'"“”‘’').strip()
                for t in self.custom_tags_entry.get().split(",")]
        tags = [t for t in tags if t]
        if not tags:
            messagebox.showerror("Error", "Enter at least one classification tag (comma-separated).")
            return

        # Validate the topic-derived column name up front, on the main thread.
        from alr.common.sql_store import sanitize_column_name, _BASE_COLUMNS
        try:
            col = sanitize_column_name(topic)
            if col in _BASE_COLUMNS:
                raise ValueError(f"'{topic}' collides with the built-in database column '{col}'; "
                                 "choose a different topic name.")
        except ValueError as e:
            messagebox.showerror("Error", str(e))
            return

        service = self.eval_llm_var.get()
        if not self._ensure_api_key(service):
            return
        source = self.custom_source_var.get()
        clean_path = clean_folder_path(folder)
        print(f"[Custom classification] topic: {topic} | tags: {', '.join(tags)} "
              f"| source: {source} | SQL column: {col}")

        def work(progress, should_cancel):
            from alr.common.sql_store import sync_storage_to_sql
            from alr.analysis_evaluation.publication_classification.classify_runner import classify_custom_space
            progress(text="Syncing storage space to the review database…")
            sync_storage_to_sql(clean_path)
            progress(text=f"Classifying against '{topic}' tags…")
            return classify_custom_space(
                clean_path, topic, tags, source=source, service=service,
                should_cancel=should_cancel,
                progress_callback=lambda done, total: progress(
                    done=done, total=total, text=f"Custom classification  {done}/{total}…"))

        self._run_threaded(work, "Custom Topic Classification", "classified")

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
            n = enrich_space_with_doi(
                MF, input_path=input_target, should_cancel=should_cancel,
                progress_callback=lambda d, t, name: progress(
                    done=d, total=t, text=f"[{d}/{t}] DOI / metadata: {name}"))
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

        # Ask which analyzed attributes to consolidate before building the master
        # workbook (abstract + Introduction + Results & Conclusion). Main thread,
        # before the worker starts.
        master_section_keys = None
        if mode == "master_excel":
            master_section_keys = self._pick_attributes_dialog(
                "Master Excel — attributes to include")
            if master_section_keys is None:
                return
            if not master_section_keys:
                messagebox.showerror("Error", "Please select at least one attribute to include.")
                return

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
                n = enrich_from_download_logs(
                    root, should_cancel=should_cancel,
                    progress_callback=lambda d, t: progress(
                        done=d, total=t, text=f"Enriching from download logs {d}/{t}…"))
                print(f"[Evaluate] Download-log enrichment updated {n} document(s).")
                return n

            if mode == "abstract":
                from alr.data_analysis.Folder_Data_Analyzer import process_abstract
                progress(text="Re-running abstract analysis pass…")
                print("[Evaluate] Re-running abstract analysis pass...")
                process_abstract(DataAnalyzeManager(clean_path),
                                 progress_callback=lambda d, t, name: progress(
                                     done=d, total=t, text=f"[{d}/{t}] Abstract: {name}"))
                print("[Evaluate] Abstract analysis pass finished.")
                return 0

            if mode == "references":
                from alr.data_analysis.Folder_Data_Analyzer import process_references
                progress(text="Re-running reference extraction pass…")
                print("[Evaluate] Re-running reference extraction pass...")
                process_references(DataAnalyzeManager(clean_path),
                                   progress_callback=lambda d, t, name: progress(
                                       done=d, total=t, text=f"[{d}/{t}] References: {name}"))
                print("[Evaluate] Reference extraction pass finished.")
                return 0

            if mode == "evaluate":
                from alr.common.sql_store import sync_storage_to_sql
                from alr.analysis_evaluation.data_evaluator import evaluate_space
                progress(text="Syncing storage to DB…")
                print("[Evaluate] Syncing storage to DB, then building analysis-evaluation databases...")
                sync_storage_to_sql(DataAnalyzeManager(clean_path))
                progress(text="Building analysis-evaluation databases…")
                n = evaluate_space(clean_path, should_cancel=should_cancel,
                                   progress_callback=lambda d, t: progress(
                                       done=d, total=t, text=f"Evaluating documents {d}/{t}…"))
                print("[Evaluate] Analysis-evaluation databases built.")
                return n

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
                written, master_path = build_master_excel_db(
                    clean_path, section_keys=master_section_keys,
                    should_cancel=should_cancel,
                    progress_callback=lambda d, t: progress(
                        done=d, total=t, text=f"Master Excel: document {d}/{t}…"))
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
            out = question_score_space(
                manager, source=source, download_log=download_log,
                progress_callback=lambda d, t, title_txt: progress(
                    done=d, total=t, text=f"[{d}/{t}] Scoring: {title_txt}"))
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
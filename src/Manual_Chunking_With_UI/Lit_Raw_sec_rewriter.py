import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


class JSONRestructurerUI:

    def __init__(self, root):
        self.root = root
        self.root.title("JSON Section Restructurer & Editor")
        self.root.geometry("750x700")

        # Underlying state data
        self.file_path = None
        self.json_data = []  # Holds list of section dictionaries
        
        # Tracking variables for change detection
        self.current_idx = -1
        self.loaded_name_snapshot = ""
        self.loaded_content_snapshot = ""

        self.create_widgets()

    def create_widgets(self):
        # --- 1. File Selection Section ---
        file_frame = ttk.LabelFrame(self.root, text="1. Select Input File", padding=10)
        file_frame.pack(fill="x", padx=15, pady=10)

        self.file_label = tk.Label(
            file_frame, text="No file selected (Start fresh or browse)...", anchor="w", fg="gray"
        )
        self.file_label.pack(side="left", fill="x", expand=True, padx=5)

        browse_btn = tk.Button(
            file_frame, text="Browse JSON", command=self.browse_file
        )
        browse_btn.pack(side="right", padx=5)

        # --- 2. Section Selection & Management Section ---
        section_frame = ttk.LabelFrame(
            self.root, text="2. Select or Add Sections", padding=10
        )
        section_frame.pack(fill="x", padx=15, pady=5)

        combo_label_frame = tk.Frame(section_frame)
        combo_label_frame.pack(fill="x", pady=2)
        tk.Label(combo_label_frame, text="Available Sections:").pack(side="left")

        # Combobox for section navigation
        self.section_combobox = ttk.Combobox(section_frame, state="readonly")
        self.section_combobox.pack(side="left", fill="x", expand=True, pady=5, padx=(0, 10))
        
        # Intercepting click/selection actions to validate changes before switching
        self.section_combobox.bind("<<ComboboxSelected>>", self.on_section_dropdown_switch)

        # Button to append a blank individual section
        add_section_btn = tk.Button(
            section_frame,
            text="+ Add Empty Section",
            command=self.add_empty_section,
            bg="#3498db",
            fg="white"
        )
        add_section_btn.pack(side="right", pady=5)

        # --- 3. Editing Content Section ---
        content_frame = ttk.LabelFrame(
            self.root, text="3. Edit Selected Section Details", padding=10
        )
        content_frame.pack(fill="both", expand=True, padx=15, pady=10)

        # Editable Section Name Field
        name_label_frame = tk.Frame(content_frame)
        name_label_frame.pack(fill="x", pady=5)
        tk.Label(name_label_frame, text="Section Name:").pack(side="left", padx=2)
        
        self.section_name_entry = tk.Entry(name_label_frame, font=("Arial", 10, "bold"))
        self.section_name_entry.pack(side="left", fill="x", expand=True, padx=5)

        # Editable Section Content Field
        tk.Label(content_frame, text="Section Content:").pack(anchor="w", pady=(10, 2))
        self.content_text = tk.Text(content_frame, wrap="word", height=12)
        self.content_text.pack(fill="both", expand=True, pady=5)

        # --- Action Buttons ---
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(fill="x", padx=15, pady=10)

        self.save_btn = tk.Button(
            btn_frame,
            text="Save / Export File",
            command=self.confirm_and_save,
            bg="#2ecc71",
            fg="white",
            font=("Arial", 10, "bold"),
            state="disabled"
        )
        self.save_btn.pack(side="right", padx=5)

    def browse_file(self):
        filename = filedialog.askopenfilename(
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")]
        )
        if filename:
            self.file_path = filename
            self.file_label.config(text=filename, fg="black")
            self.load_json_data()

    def load_json_data(self):
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.json_data = data if isinstance(data, list) else [data]

            self.refresh_dropdown()
            if self.json_data:
                self.current_idx = 0
                self.section_combobox.current(0)
                self.load_section_into_ui(0)

            messagebox.showinfo("Success", "JSON file loaded successfully!")
        except Exception as e:
            messagebox.showerror(
                "Error", f"Failed to parse or load JSON file:\n{str(e)}"
            )

    def refresh_dropdown(self):
        """Refreshes the drop-down menu choices matching current dataset state"""
        sections = [item.get("Section Name", "Untitled Section") for item in self.json_data]
        self.section_combobox["values"] = sections
        if self.json_data:
            self.save_btn.config(state="normal")

    def has_unsaved_changes(self):
        """Checks if the currently visible text differs from the snapshot data"""
        if self.current_idx == -1 or not self.json_data:
            return False
        
        current_ui_name = self.section_name_entry.get()
        current_ui_content = self.content_text.get("1.0", "end-1c")
        
        return (current_ui_name != self.loaded_name_snapshot or 
                current_ui_content != self.loaded_content_snapshot)

    def check_and_prompt_unsaved_changes(self):
        """Prompts user to save changes if deviations are found. Returns False if cancelled."""
        if self.has_unsaved_changes():
            response = messagebox.askyesnocancel(
                "Unsaved Changes",
                f"You have unsaved changes in '{self.loaded_name_snapshot}'.\nDo you want to save them before switching?"
            )
            if response is True:  # User clicked 'Yes'
                self.commit_current_ui_to_memory()
                return True
            elif response is False:  # User clicked 'No' (Discard)
                return True
            else:  # User clicked 'Cancel'
                return False
        return True

    def on_section_dropdown_switch(self, event):
        """Triggered when the user picks a different item in the combobox."""
        new_idx = self.section_combobox.current()
        if new_idx == self.current_idx:
            return

        # Check for modifications in the previous section before completing the switch
        if self.check_and_prompt_unsaved_changes():
            # If validated or discarded, update index context and display the new section
            self.current_idx = new_idx
            self.load_section_into_ui(new_idx)
        else:
            # If cancelled, force the dropdown index position back to the previous section selection
            self.section_combobox.current(self.current_idx)

    def load_section_into_ui(self, index):
        """Populates the text areas with data from memory and saves snapshots for comparison."""
        if index < 0 or index >= len(self.json_data):
            return
        
        selected_section = self.json_data[index]

        # Populate Section Name
        name = selected_section.get("Section Name", "")
        self.section_name_entry.delete(0, tk.END)
        self.section_name_entry.insert(0, name)
        self.loaded_name_snapshot = name

        # Populate Content Text
        content = selected_section.get("Text_Content", "")
        self.content_text.delete("1.0", tk.END)
        self.content_text.insert("1.0", content)
        self.loaded_content_snapshot = content

    def commit_current_ui_to_memory(self):
        """Saves current fields down into the target dictionary element index context"""
        if self.current_idx >= 0 and self.json_data:
            final_name = self.section_name_entry.get().strip()
            final_text = self.content_text.get("1.0", "end-1c")

            name_to_assign = final_name if final_name else "Untitled Section"
            
            self.json_data[self.current_idx]["Section Name"] = name_to_assign
            self.json_data[self.current_idx]["Text_Content"] = final_text
            self.json_data[self.current_idx]["Chunks"] = [[1, final_text]] if final_text.strip() else []

            # Update snapshots to match current state
            self.loaded_name_snapshot = name_to_assign
            self.loaded_content_snapshot = final_text
            
            # Refresh dropdown list options to reflect the new text title layout right away
            self.refresh_dropdown()

    def add_empty_section(self):
        """Appends a fresh section to the tracking array list after checking current modifications"""
        if not self.check_and_prompt_unsaved_changes():
            return

        new_index = len(self.json_data) + 1
        new_section_placeholder = {
            "Section Name": f"NEW_SECTION_{new_index}",
            "Text_Content": "",
            "Chunks": []
        }
        self.json_data.append(new_section_placeholder)
        
        self.refresh_dropdown()
        self.current_idx = len(self.json_data) - 1
        self.section_combobox.current(self.current_idx)
        self.load_section_into_ui(self.current_idx)
        self.section_name_entry.focus_set()

    def confirm_and_save(self):
        if not self.json_data:
            messagebox.showwarning("Warning", "No structured content available to save!")
            return

        # Commit active interface text elements to memory array arrays
        self.commit_current_ui_to_memory()

        answer = messagebox.askyesno(
            "Confirm Save",
            "Are you sure you want to finalize changes and export the updated JSON file?",
        )

        if answer:
            initial_file_path = self.file_path if self.file_path else "restructured_sections.json"
            
            output_filename = filedialog.asksaveasfilename(
                defaultextension=".json",
                filetypes=[("JSON Files", "*.json")],
                initialfile=initial_file_path,
            )

            if output_filename:
                try:
                    with open(output_filename, "w", encoding="utf-8") as f:
                        json.dump(self.json_data, f, indent=4, ensure_ascii=False)
                    
                    self.file_path = output_filename
                    self.file_label.config(text=output_filename, fg="black")
                    messagebox.showinfo("Saved", "JSON document saved successfully!")
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to save file structure:\n{str(e)}")


if __name__ == "__main__":
    root = tk.Tk()
    app = JSONRestructurerUI(root)
    root.mainloop()
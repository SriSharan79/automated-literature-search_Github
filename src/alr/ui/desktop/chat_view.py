"""
alr.ui.desktop.chat_view
========================

A small, self-contained **AI chat** window shared by the main tool and the
Review tool. The user picks the service (Blablador / Chat AI / DLR Ollama) and
model and just asks questions; every turn goes through the same
``llm_utils.llm_call`` the rest of the app uses.

The window optionally shows a **context panel** on the left. The Review tool
plugs in a :class:`DocumentContextController` so the user can attach the data of
one or several analyzed documents (of chosen attributes) as context for the
query; the main tool opens the window without one for a plain chat.

Nothing here imports ``main_window`` (both tools import this module), so the
provider maps are defined locally.
"""

import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from alr.common.llm_utils import (
    list_available_models, get_selected_model, set_selected_model,
)

# Provider code <-> display label / service name (same preference order as the
# rest of the app: Blablador, then Chat AI, then DLR Ollama).
CODE_TO_DISPLAY = {"B": "Blablador", "C": "Chat AI", "O": "DLR Ollama"}
DISPLAY_TO_CODE = {v: k for k, v in CODE_TO_DISPLAY.items()}
CODE_TO_SERVICE = {"B": "BlaBla", "C": "Chat AI", "O": "DLR Ollama"}

CHAT_SYS_PROMPT = (
    "You are a helpful research assistant embedded in a literature-review tool. "
    "Answer the user's questions clearly and concisely. If document data is "
    "attached as context, use it as the primary source when it is relevant, and "
    "say so when the answer is not covered by the attached data."
)

# Attached document context is capped so a few large documents/attributes can't
# blow past the model's context window. Truncation happens at a token boundary
# and the user is told (in the transcript + the picker status line).
DEFAULT_CONTEXT_TOKEN_BUDGET = 6000


# ---------------------------------------------------------------------------
# Token estimation / budget guard (tiktoken when available, char heuristic else)
# ---------------------------------------------------------------------------
_ENCODING = None


def _get_encoding():
    """Lazily load a tiktoken encoding once; False if tiktoken is unavailable."""
    global _ENCODING
    if _ENCODING is None:
        try:
            import tiktoken
            _ENCODING = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _ENCODING = False
    return _ENCODING or None


def estimate_tokens(text) -> int:
    """Approximate token count of ``text`` (tiktoken, or ~4 chars/token)."""
    if not text:
        return 0
    enc = _get_encoding()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)


def truncate_to_tokens(text, max_tokens):
    """
    Return ``(text, was_truncated)`` trimmed to at most ``max_tokens`` tokens.
    ``max_tokens`` None/<=0 disables the guard.
    """
    if not text or not max_tokens or max_tokens <= 0:
        return text, False
    enc = _get_encoding()
    if enc is not None:
        try:
            toks = enc.encode(text)
            if len(toks) <= max_tokens:
                return text, False
            return enc.decode(toks[:max_tokens]), True
        except Exception:
            pass
    limit = max_tokens * 4  # ~4 chars/token fallback
    if len(text) <= limit:
        return text, False
    return text[:limit], True


# ---------------------------------------------------------------------------
# Pure logic (no Tk) — easy to unit-test.
# ---------------------------------------------------------------------------
def format_document_context(store, uuids, attributes) -> str:
    """
    Build a plain-text context block from selected documents and attributes.

    ``store`` is any object exposing ``get_document(uuid) -> dict``. Empty /
    missing attribute values are skipped. Returns "" when nothing is selected.
    """
    blocks = []
    for i, uuid in enumerate(uuids, 1):
        doc = store.get_document(uuid) or {}
        heading = doc.get("title") or doc.get("filename") or uuid
        lines = [f"[Document {i}: {heading}]"]
        for attr in attributes:
            val = doc.get(attr)
            if val is None or str(val).strip() == "":
                continue
            lines.append(f"{attr}: {val}")
        if len(lines) > 1:  # at least one real attribute
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


class ChatSession:
    """Conversation state + the LLM call. Holds no Tk objects."""

    def __init__(self, service_code="B", model=None, history_turns=8):
        self.service_code = service_code
        self.model = model
        self.history = []            # list of (role, text)
        self._history_turns = history_turns

    def _convo_text(self, user_text):
        parts = []
        for role, txt in self.history[-2 * self._history_turns:]:
            parts.append(f"{'User' if role == 'user' else 'Assistant'}: {txt}")
        parts.append(f"User: {user_text}")
        parts.append("Assistant:")
        return "\n".join(parts)

    def ask(self, user_text, context_text=""):
        """Send one turn; append both sides to history; return the response text."""
        from alr.common.llm_utils import llm_call  # local import: easy to patch in tests
        sys_prompt = CHAT_SYS_PROMPT
        if context_text:
            sys_prompt += (
                "\n\nThe user has attached data from one or more analyzed documents "
                "below. Treat it as the primary context for the question.\n\n"
                "=== Attached document data ===\n" + context_text)
        prompt = self._convo_text(user_text)
        resp = llm_call(prompt, sys_prompt, self.service_code,
                        model=self.model or None, allow_fallback=True)
        self.history.append(("user", user_text))
        self.history.append(("assistant", resp if resp is not None else "(no response)"))
        return resp


# ---------------------------------------------------------------------------
# Review-tool context: pick documents + attributes to attach.
# ---------------------------------------------------------------------------
class DocumentContextController:
    """
    Builds the left-hand context panel for the Review tool's chat: a searchable
    multi-select list of documents and a set of attribute checkboxes. Its
    :meth:`get_context` renders the current selection into a context string.
    """

    DEFAULT_ATTRS = ["title", "abstract_text", "publication_year",
                     "publication_type", "classification", "abstract_classification"]

    def __init__(self, store):
        self.store = store
        self._doc_index = []     # listbox row -> uuid
        self._attr_vars = {}     # attribute -> BooleanVar
        self.status_var = None
        self.search_var = None
        self.listbox = None

    def build_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="Attach document data as context")

        srow = ttk.Frame(frame)
        srow.pack(fill="x", padx=6, pady=(6, 2))
        ttk.Label(srow, text="Find:").pack(side="left")
        self.search_var = tk.StringVar()
        ent = ttk.Entry(srow, textvariable=self.search_var)
        ent.pack(side="left", fill="x", expand=True, padx=4)
        ent.bind("<Return>", lambda e: self._reload())
        ttk.Button(srow, text="Search", command=self._reload).pack(side="left")

        dl = ttk.LabelFrame(frame, text="Documents (Ctrl/Shift-click for several)")
        dl.pack(fill="both", expand=True, padx=6, pady=4)
        sb = ttk.Scrollbar(dl, orient="vertical")
        self.listbox = tk.Listbox(dl, selectmode="extended", height=10, exportselection=False,
                                  yscrollcommand=sb.set)
        sb.config(command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", lambda e: self._update_status())

        af = ttk.LabelFrame(frame, text="Attributes to include")
        af.pack(fill="x", padx=6, pady=4)
        try:
            attrs = list(self.store.available_fields())
        except Exception:
            attrs = list(self.DEFAULT_ATTRS)
        for i, col in enumerate(attrs):
            var = tk.BooleanVar(value=col in self.DEFAULT_ATTRS)
            self._attr_vars[col] = var
            ttk.Checkbutton(af, text=col, variable=var, command=self._update_status).grid(
                row=i // 3, column=i % 3, sticky="w", padx=4, pady=1)

        self.status_var = tk.StringVar(value="No documents selected.")
        ttk.Label(frame, textvariable=self.status_var, foreground="#555").pack(
            anchor="w", padx=6, pady=(0, 6))

        self._reload()
        return frame

    def _reload(self):
        search = (self.search_var.get() or "").strip() or None
        try:
            docs = self.store.list_documents(search)
        except Exception:
            docs = []
        self._doc_index = []
        self.listbox.delete(0, tk.END)
        for d in docs:
            uuid = d.get("uuid")
            label = d.get("title") or d.get("filename") or uuid or ""
            self.listbox.insert(tk.END, str(label)[:140])
            self._doc_index.append(uuid)
        self._update_status()

    def selected_uuids(self):
        return [self._doc_index[i] for i in self.listbox.curselection()] if self.listbox else []

    def selected_attrs(self):
        return [c for c, v in self._attr_vars.items() if v.get()]

    def _update_status(self):
        if self.status_var is None:
            return
        uuids, attrs = self.selected_uuids(), self.selected_attrs()
        ntok = estimate_tokens(self.get_context()) if (uuids and attrs) else 0
        msg = (f"{len(uuids)} document(s) × {len(attrs)} attribute(s) "
               f"≈ {ntok} tokens")
        if ntok > DEFAULT_CONTEXT_TOKEN_BUDGET:
            msg += f"  (over {DEFAULT_CONTEXT_TOKEN_BUDGET}-token budget — will be truncated)"
        self.status_var.set(msg)

    def get_context(self):
        uuids, attrs = self.selected_uuids(), self.selected_attrs()
        if not uuids or not attrs:
            return ""
        return format_document_context(self.store, uuids, attrs)


# ---------------------------------------------------------------------------
# The window.
# ---------------------------------------------------------------------------
class ChatWindow:
    def __init__(self, master, title="AI Chat", context_controller=None,
                 context_token_budget=DEFAULT_CONTEXT_TOKEN_BUDGET):
        self.context = context_controller
        self.context_token_budget = context_token_budget
        self.session = ChatSession()
        self._busy = False

        self.top = tk.Toplevel(master) if master is not None else tk.Tk()
        self.top.title(title)
        self.top.geometry("900x620" if context_controller else "620x560")
        self.top.minsize(560, 420)
        try:
            ttk.Style(self.top).theme_use("clam")
        except tk.TclError:
            pass

        # --- provider / model row ---
        bar = ttk.Frame(self.top)
        bar.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(bar, text="Service:").pack(side="left")
        self.service_var = tk.StringVar(value=CODE_TO_DISPLAY["B"])
        combo = ttk.Combobox(bar, values=list(CODE_TO_DISPLAY.values()), width=14,
                             state="readonly", textvariable=self.service_var)
        combo.pack(side="left", padx=4)
        combo.bind("<<ComboboxSelected>>", lambda e: self._on_service_change())
        ttk.Button(bar, text="Choose Model…", command=self._choose_model).pack(side="left", padx=4)
        self.model_lbl = ttk.Label(bar, text="", foreground="#555")
        self.model_lbl.pack(side="left", padx=8)

        # --- body: optional context panel (left) + chat (right) ---
        if context_controller is not None:
            split = ttk.PanedWindow(self.top, orient="horizontal")
            split.pack(fill="both", expand=True, padx=10, pady=4)
            left = ttk.Frame(split)
            split.add(left, weight=2)
            context_controller.build_panel(left).pack(fill="both", expand=True)
            chat_area = ttk.Frame(split)
            split.add(chat_area, weight=3)
        else:
            chat_area = ttk.Frame(self.top)
            chat_area.pack(fill="both", expand=True, padx=10, pady=4)

        tr = ttk.Frame(chat_area)
        tr.pack(fill="both", expand=True)
        tsb = ttk.Scrollbar(tr, orient="vertical")
        self.transcript = tk.Text(tr, wrap="word", height=14, state="disabled",
                                  yscrollcommand=tsb.set, padx=8, pady=6)
        tsb.config(command=self.transcript.yview)
        tsb.pack(side="right", fill="y")
        self.transcript.pack(side="left", fill="both", expand=True)
        self.transcript.tag_configure("who", font=("TkDefaultFont", 9, "bold"), spacing1=6)
        self.transcript.tag_configure("msg", spacing3=4, lmargin1=8, lmargin2=8)
        self.transcript.tag_configure("err", foreground="#b00020")

        # --- input row ---
        inp = ttk.Frame(chat_area)
        inp.pack(fill="x", pady=(6, 0))
        self.input = tk.Text(inp, height=3, wrap="word")
        self.input.pack(side="left", fill="both", expand=True)
        self.input.bind("<Return>", self._on_return)
        self.input.bind("<Shift-Return>", lambda e: None)  # newline
        btns = ttk.Frame(inp)
        btns.pack(side="right", fill="y", padx=(6, 0))
        self.send_btn = ttk.Button(btns, text="Send", command=self._send)
        self.send_btn.pack(fill="x")
        ttk.Button(btns, text="Clear", command=self._clear).pack(fill="x", pady=(4, 0))

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(self.top, textvariable=self.status_var, anchor="w",
                  foreground="#555").pack(fill="x", padx=10, pady=(2, 8))

        self._on_service_change()  # sets the model label + session service
        hint = ("Ask a question. " +
                ("Tick documents and attributes on the left to attach their data as context. "
                 if context_controller else "") +
                "Enter sends; Shift+Enter for a new line.")
        self._append("System", hint)
        self.input.focus_set()

    # -- provider / model --------------------------------------------------
    def _service_code(self):
        return DISPLAY_TO_CODE.get(self.service_var.get(), "B")

    def _on_service_change(self):
        code = self._service_code()
        self.session.service_code = code
        service = CODE_TO_SERVICE.get(code, "BlaBla")
        try:
            current = get_selected_model(service)
        except Exception:
            current = None
        self.session.model = current or None
        self.model_lbl.config(text=f"Model: {current or 'service default'}")

    def _choose_model(self):
        code = self._service_code()
        service = CODE_TO_SERVICE.get(code, "BlaBla")
        try:
            models = list_available_models(service)
        except Exception as e:
            messagebox.showerror("Model list failed", f"Could not read models for {service}:\n{e}")
            return

        dlg = tk.Toplevel(self.top)
        dlg.title(f"Select {service} Model")
        dlg.geometry("520x400")
        dlg.transient(self.top)
        dlg.grab_set()
        header = ttk.Label(dlg, font=("TkDefaultFont", 10, "bold"))
        header.pack(padx=10, pady=8, anchor="w")
        lf = ttk.Frame(dlg)
        lf.pack(fill="both", expand=True, padx=10, pady=5)
        sb = ttk.Scrollbar(lf, orient="vertical")
        box = tk.Listbox(lf, yscrollcommand=sb.set)
        sb.config(command=box.yview)
        sb.pack(side="right", fill="y")
        box.pack(side="left", fill="both", expand=True)
        state = {"models": list(models)}

        def _populate():
            box.delete(0, tk.END)
            for m in state["models"]:
                box.insert(tk.END, m)
            cur = get_selected_model(service)
            header.config(text=(f"Available {service} models (current: {cur}):"
                                if state["models"]
                                else f"No stored models for {service} — press “Refresh list”."))
            if cur in state["models"]:
                idx = state["models"].index(cur)
                box.selection_set(idx)
                box.see(idx)

        def _refresh():
            header.config(text=f"Refreshing {service} models…")
            dlg.update_idletasks()
            try:
                state["models"] = list(list_available_models(service, force_refresh=True))
            except Exception as e:
                messagebox.showerror("Refresh failed", f"Could not fetch models for {service}:\n{e}")
                return
            _populate()

        def _select():
            sel = box.curselection()
            if not sel:
                return
            model = state["models"][sel[0]]
            set_selected_model(service, model)
            self.session.model = model
            self.model_lbl.config(text=f"Model: {model}")
            dlg.destroy()

        row = ttk.Frame(dlg)
        row.pack(fill="x", padx=10, pady=8)
        ttk.Button(row, text="Refresh list", command=_refresh).pack(side="left")
        ttk.Button(row, text="Select", command=_select).pack(side="right")
        ttk.Button(row, text="Cancel", command=dlg.destroy).pack(side="right", padx=6)
        _populate()

    # -- conversation ------------------------------------------------------
    def _append(self, who, text, error=False):
        self.transcript.config(state="normal")
        self.transcript.insert("end", f"{who}\n", ("who",))
        self.transcript.insert("end", f"{text}\n\n", ("err",) if error else ("msg",))
        self.transcript.config(state="disabled")
        self.transcript.see("end")

    def _on_return(self, event):
        if event.state & 0x0001:  # Shift held -> newline
            return None
        self._send()
        return "break"

    def _clear(self):
        self.session.history.clear()
        self.transcript.config(state="normal")
        self.transcript.delete("1.0", "end")
        self.transcript.config(state="disabled")
        self.status_var.set("Conversation cleared.")

    def _send(self):
        if self._busy:
            return
        text = self.input.get("1.0", "end").strip()
        if not text:
            return
        context = self.context.get_context() if self.context else ""
        truncated = False
        if context:
            context, truncated = truncate_to_tokens(context, self.context_token_budget)
        self._append("You", text)
        if context:
            n_docs = len(self.context.selected_uuids())
            note = f"(attached {n_docs} document(s) as context, ~{estimate_tokens(context)} tokens"
            if truncated:
                note += (f"; truncated to the {self.context_token_budget}-token budget — "
                         "deselect some documents or attributes for full coverage")
            note += ")"
            self._append("System", note)
        self.input.delete("1.0", "end")

        self._busy = True
        self.send_btn.config(state="disabled")
        self.status_var.set("Assistant is thinking…")

        q = queue.Queue()

        def worker():
            try:
                q.put(("ok", self.session.ask(text, context)))
            except Exception as e:  # noqa: BLE001 - surface to the UI
                q.put(("err", str(e)))

        def poll():
            try:
                kind, payload = q.get_nowait()
            except queue.Empty:
                self.top.after(80, poll)
                return
            self._busy = False
            try:
                self.send_btn.config(state="normal")
            except tk.TclError:
                return  # window closed mid-flight
            if kind == "ok":
                self._append("Assistant", payload or "(no response)")
                self.status_var.set(self.model_lbl.cget("text"))
            else:
                self._append("Assistant", f"[error] {payload}", error=True)
                self.status_var.set("Error — see message above.")

        threading.Thread(target=worker, daemon=True).start()
        self.top.after(80, poll)


def open_chat_window(master=None, title="AI Chat", context_controller=None,
                     context_token_budget=DEFAULT_CONTEXT_TOKEN_BUDGET):
    """Open the AI chat window; returns the Toplevel."""
    return ChatWindow(master, title=title, context_controller=context_controller,
                      context_token_budget=context_token_budget).top

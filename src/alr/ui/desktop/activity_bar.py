"""
alr.ui.desktop.activity_bar
==========================

An always-visible strip that shows live model activity by polling
``alr.common.activity_monitor``: which chat/embedding model is running right
now (with an animated bar), plus running totals — tokens processed by
text-generation models and vectors produced by embedding models.

Both the main window and the Review tool mount one at the top.
"""

import tkinter as tk
from tkinter import ttk

from alr.common import activity_monitor


class ActivityBar:
    def __init__(self, parent, interval_ms=600):
        self._interval = interval_ms
        self._running = False

        self.frame = ttk.Frame(parent)
        self.bar = ttk.Progressbar(self.frame, mode="indeterminate", length=110)
        self.bar.pack(side="left", padx=(8, 8), pady=2)
        self.label = ttk.Label(self.frame, text="No model activity yet.", anchor="w")
        self.label.pack(side="left", fill="x", expand=True)

        self._tick()

    def pack(self, **kw):
        self.frame.pack(**kw)
        return self.frame

    def grid(self, **kw):
        self.frame.grid(**kw)
        return self.frame

    @staticmethod
    def format(snap) -> str:
        parts = []
        a = snap.get("active")
        if a:
            verb = "Embedding" if a.get("kind") == "embed" else "Generating"
            parts.append(f"⏳ {verb} · {a.get('model') or '?'} ({a.get('service') or '?'})")
        c = snap.get("chat", {})
        if c.get("calls"):
            parts.append(
                f"LLM {c.get('last_model') or '?'}: {c['calls']} call(s), "
                f"{c.get('in_tokens', 0):,} in / {c.get('out_tokens', 0):,} out tok")
        e = snap.get("embed", {})
        if e.get("calls"):
            parts.append(
                f"Embed {e.get('last_model') or '?'}: {e['calls']} call(s), "
                f"{e.get('vectors', 0):,} vectors")
        return "     |     ".join(parts) if parts else "No model activity yet."

    def _tick(self):
        try:
            snap = activity_monitor.snapshot()
            active = snap.get("active") is not None
            if active and not self._running:
                self.bar.start(12)
                self._running = True
            elif not active and self._running:
                self.bar.stop()
                self._running = False
            self.label.config(text=self.format(snap))
            self.frame.after(self._interval, self._tick)
        except tk.TclError:
            return  # widget destroyed — stop polling

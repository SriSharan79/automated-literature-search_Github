"""
alr.ui.desktop._async
======================

Shared UI plumbing used by both the main window and the Review tool:

  * :class:`ProgressDialog` - a small status/progress/Cancel dialog (modal or not).
  * :func:`run_threaded`    - run a long pass on a background thread with a
    progress dialog, a busy guard, a mid-run ``ask`` round-trip, and automatic
    empty-folder cleanup.
  * :func:`make_scrollable_tab` - add a notebook tab whose content can grow past
    the window height (vertical-scroll canvas).

Both tools previously carried their own near-identical copies of this code; the
main window's richer, non-modal implementation is the one kept here so the
Review tool inherits the busy guard, the ``ask`` round-trip and the artifact
cleanup for free.
"""

import inspect
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from alr.common import crash_logger


class ProgressDialog:
    """A small dialog with a status message, progress bar and Cancel.

    Modal by default; pass ``modal=False`` to let the rest of the app stay
    interactive (e.g. browse other tabs) while a long pass runs.
    """

    def __init__(self, master, title="Working…", on_cancel=None, modal=True):
        self.top = tk.Toplevel(master)
        self.top.title(title)
        self.top.geometry("440x160")
        self.top.transient(master)
        self._modal = modal
        if modal:
            self.top.grab_set()
        self.top.resizable(False, False)
        # Closing the window acts as Cancel (if cancellable), else no-op.
        self.top.protocol("WM_DELETE_WINDOW", (lambda: self._cancel()) if on_cancel else (lambda: None))

        self._on_cancel = on_cancel
        self.label = ttk.Label(self.top, text="Starting…", wraplength=410, anchor="w", justify="left")
        self.label.pack(padx=16, pady=(18, 8), fill="x")
        self.bar = ttk.Progressbar(self.top, mode="indeterminate", length=406)
        self.bar.pack(padx=16, pady=8)
        self.bar.start(12)

        if on_cancel:
            self.cancel_btn = ttk.Button(self.top, text="Cancel", command=self._cancel)
            self.cancel_btn.pack(pady=(4, 8))

    def _cancel(self):
        if self._on_cancel:
            self._on_cancel()
        self.label.config(text="Cancelling — finishing the current item…")
        if hasattr(self, "cancel_btn"):
            self.cancel_btn.config(state="disabled", text="Cancelling…")

    def apply(self, done=None, total=None, text=None):
        if text is not None:
            self.label.config(text=text)
        if done is not None and total:
            # Switch to a determinate bar once we know the item count, and reset
            # the maximum whenever the total changes (e.g. across phases).
            if str(self.bar.cget("mode")) != "determinate" or int(self.bar.cget("maximum")) != int(total):
                self.bar.stop()
                self.bar.config(mode="determinate", maximum=total)
            self.bar.config(value=done)

    def close(self):
        try:
            self.bar.stop()
            if self._modal:
                self.top.grab_release()
            self.top.destroy()
        except tk.TclError:
            pass


def make_scrollable_tab(notebook, title):
    """
    Add a notebook tab whose content can grow past the window height: the
    content lives inside a vertical-scroll canvas. Returns the inner frame that
    tab content should be packed into.
    """
    outer = ttk.Frame(notebook)
    notebook.add(outer, text=title)
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


def run_threaded(host, work, title, result_word="processed", on_success=None,
                 on_cancelled=None, *, modal=False, cleanup=True,
                 status=None, after=None):
    """
    Run ``work(progress, should_cancel)`` on a background thread with a progress
    dialog. The worker only touches a thread-safe queue; all Tk access happens
    on the main thread via an ``after``-scheduled poller (calling Tk from a
    worker thread is unsafe).

    ``progress(done=?, total=?, text=?)`` enqueues an update; ``should_cancel()``
    returns True once Cancel is pressed; ``work`` returns a value passed to
    ``on_success`` (or, by default, treated as an int document count).

    ``host`` is the Tk widget/window used to parent the dialog and schedule the
    poller; a ``host._busy`` flag guards against a second pass while one runs.

    ``on_success(result)``, if given, replaces the default "processed N
    document(s)" message box. ``on_cancelled(result)``, if given, replaces the
    default "cancelled after N" box.

    A worker that declares a third parameter also receives ``ask(handler)``: it
    runs ``handler(host)`` on the **main** thread and blocks until it returns,
    so a pass can pop a modal mid-run and act on the answer.

    ``status(text, ok)``, if given, records a one-line status (main window's
    last-result strip). ``after()``, if given, runs at the very end (Review
    tool's view refresh).
    """
    # Only one background pass at a time (the dialog may be non-modal, so the
    # user could otherwise launch a second on top of a running one).
    if getattr(host, "_busy", False):
        messagebox.showinfo("Please wait",
                            "A task is already running. Let it finish (or cancel it) "
                            "before starting another.")
        return
    host._busy = True

    cancel_event = threading.Event()
    dlg = ProgressDialog(host, title, on_cancel=cancel_event.set, modal=modal)
    q = queue.Queue()
    outcome = {}

    def progress(**kw):
        q.put(("progress", kw))

    def ask(handler):
        """Run ``handler(host)`` on the main thread; block for its result."""
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
        if cleanup:
            try:
                progress(text="Cleaning up empty files and folders…")
                from alr.common.artifact_cleanup import prune_touched_folders
                prune_touched_folders()
            except Exception as e:  # noqa: BLE001 - cleanup must never fail a pass
                print(f"[Cleanup] Skipped/failed: {e}")

        if not failed:
            q.put(("done", result))

    def finish():
        host._busy = False
        dlg.close()
        if "error" in outcome:
            if status:
                status(f"{title}: failed — {outcome['error']}", ok=False)
            msg = str(outcome["error"])
            if outcome.get("error_log"):
                msg += f"\n\nA full traceback was saved to:\n{outcome['error_log']}"
            messagebox.showerror(title, msg)
        elif cancel_event.is_set():
            if status:
                status(f"{title}: cancelled.", ok=False)
            if on_cancelled is not None:
                on_cancelled(outcome.get("n"))
            else:
                messagebox.showinfo(title, f"{title}: cancelled after {result_word} "
                                           f"{outcome.get('n', 0)} document(s).")
        elif on_success is not None:
            if status:
                status(f"{title}: done.")
            on_success(outcome.get("n"))
        else:
            if status:
                status(f"{title}: {result_word} {outcome.get('n', 0)} document(s).")
            messagebox.showinfo(title, f"{title}: {result_word} {outcome.get('n', 0)} document(s).")
        if after is not None:
            after()

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
                        holder["result"] = handler(host)
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
        host.after(80, poll)

    threading.Thread(target=worker, daemon=True).start()
    host.after(80, poll)

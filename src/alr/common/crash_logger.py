"""
Automatic crash logging for the Automated Literature Review apps.

Whenever the application crashes -- an uncaught exception on the main thread,
inside a Tkinter callback (button handlers, ``after`` jobs), or on a background
thread -- a timestamped log file with the full traceback is written to
``~/Automated Literature Review/00_Crash_Logs/`` so the failure can be
diagnosed after the window has closed.

Three integration points:

* ``install(app_name)``           -- process-wide ``sys.excepthook`` +
                                     ``threading.excepthook`` (idempotent).
* ``attach_to_tk(root, app_name)``-- routes Tk callback exceptions
                                     (``report_callback_exception``) into the
                                     crash log and shows the user the log path.
* ``write_crash_log(...)``        -- used directly by worker wrappers (e.g.
                                     ``_run_threaded``) that already catch
                                     exceptions but want the full traceback
                                     persisted, not just ``str(e)``.

Every function here is defensive: logging a crash must never raise a new one.
The main window redirects ``sys.stdout``/``sys.stderr`` into the in-app
console, so notices are echoed to the *original* ``sys.__stderr__``.
"""

from __future__ import annotations

import platform
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

from alr.common.file_manager import ALR_main_folder

CRASH_LOG_DIR = Path(ALR_main_folder) / "00_Crash_Logs"

_installed = False
_app_name = "Automated Literature Review"


def write_crash_log(exc_type, exc_value, exc_tb, origin="unhandled exception"):
    """Write one crash-report file with the full traceback; return its path.

    Never raises: returns ``None`` if the log cannot be written (e.g. the
    home folder is read-only) so callers can still show the plain error.
    """
    try:
        CRASH_LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now()
        log_path = CRASH_LOG_DIR / f"{stamp:%Y-%m-%d_%H-%M-%S}_crash.log"
        header = "\n".join([
            "=" * 70,
            f"CRASH REPORT — {_app_name}",
            f"Time     : {stamp:%Y-%m-%d %H:%M:%S}",
            f"Origin   : {origin}",
            f"Thread   : {threading.current_thread().name}",
            f"Python   : {sys.version.split()[0]} ({sys.executable})",
            f"Platform : {platform.platform()}",
            f"Argv     : {' '.join(sys.argv)}",
            "=" * 70,
            "",
        ])
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        # Append-mode: if two crashes land in the same second they share a file
        # instead of one overwriting the other.
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(header + tb_text + "\n")
        _notify_console(f"[crash] Traceback saved to: {log_path}")
        return str(log_path)
    except Exception:  # noqa: BLE001 - crash logging must never crash the app
        return None


def _notify_console(message):
    """Echo to the real terminal, bypassing the in-app stdout redirection."""
    try:
        (sys.__stderr__ or sys.stderr).write(message + "\n")
    except Exception:  # noqa: BLE001
        pass


def install(app_name=None):
    """Install process-wide crash hooks (main thread + background threads).

    Safe to call more than once; only the first call installs the hooks.
    ``KeyboardInterrupt`` is passed through unlogged (Ctrl+C is not a crash).
    Previous hooks are chained so default stderr output is preserved.
    """
    global _installed, _app_name
    if app_name:
        _app_name = app_name
    if _installed:
        return
    _installed = True

    previous_excepthook = sys.excepthook
    previous_threading_hook = threading.excepthook

    def _excepthook(exc_type, exc_value, exc_tb):
        if not issubclass(exc_type, KeyboardInterrupt):
            write_crash_log(exc_type, exc_value, exc_tb, origin="main thread")
        previous_excepthook(exc_type, exc_value, exc_tb)

    def _threading_hook(args):
        if args.exc_type is not SystemExit:
            thread_name = args.thread.name if args.thread else "unknown thread"
            write_crash_log(args.exc_type, args.exc_value, args.exc_traceback,
                            origin=f"background thread '{thread_name}'")
        previous_threading_hook(args)

    sys.excepthook = _excepthook
    threading.excepthook = _threading_hook


def attach_to_tk(root, app_name=None):
    """Route Tk callback exceptions on ``root`` into the crash log.

    Exceptions raised inside Tkinter callbacks (button commands, ``after``
    jobs, event bindings) never reach ``sys.excepthook`` -- Tk swallows them
    via ``report_callback_exception``. This override logs the traceback and
    tells the user where it was saved, then keeps the app running (matching
    Tk's default behaviour of not tearing the window down).
    """
    global _app_name
    if app_name:
        _app_name = app_name

    def _report(exc_type, exc_value, exc_tb):
        log_path = write_crash_log(exc_type, exc_value, exc_tb, origin="Tk callback")
        _notify_console("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
        try:
            from tkinter import messagebox
            detail = f"\n\nA full traceback was saved to:\n{log_path}" if log_path else ""
            messagebox.showerror(
                "Unexpected error",
                f"{exc_type.__name__}: {exc_value}{detail}",
                parent=root,
            )
        except Exception:  # noqa: BLE001 - never let the reporter itself crash
            pass

    root.report_callback_exception = _report

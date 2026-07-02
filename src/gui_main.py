"""
Desktop GUI entry point for the Automated Literature Review tool.

This is the script PyInstaller bundles (see UI_pipeline.spec). It launches the
Tkinter desktop application. All application logic lives in the ``alr`` package.
"""

import sys

from alr.ui.desktop.main_window import AutomatedLiteratureUI


def main():
    # AutomatedLiteratureUI redirects sys.stdout/stderr to its in-window log.
    # Keep the originals so we can restore them on exit and avoid a noisy
    # "lost sys.stderr" traceback when the window closes.
    original_stdout, original_stderr = sys.stdout, sys.stderr
    try:
        app = AutomatedLiteratureUI()
        app.mainloop()
    finally:
        sys.stdout, sys.stderr = original_stdout, original_stderr


if __name__ == "__main__":
    main()

"""
Standalone entry point for the Review tool.

Launches the review application in its own window, independent of the main
Automated Literature Review app. Bundled by PyInstaller as a second executable
(see UI_pipeline.spec).
"""

import tkinter as tk

from alr.common import crash_logger
from alr.ui.desktop.review_app import ReviewApp


def main():
    crash_logger.install("Automated Literature Review — Review Tool")
    root = tk.Tk()
    crash_logger.attach_to_tk(root)
    root.title("Automated Literature Review — Review Tool")
    root.geometry("1050x760")
    ReviewApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

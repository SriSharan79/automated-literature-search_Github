"""
Standalone entry point for the Review tool.

Launches the review application in its own window, independent of the main
Automated Literature Review app. Bundled by PyInstaller as a second executable
(see UI_pipeline.spec).
"""

import tkinter as tk

from alr.ui.desktop.review_app import ReviewApp


def main():
    root = tk.Tk()
    root.title("Automated Literature Review — Review Tool")
    root.geometry("1050x760")
    ReviewApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

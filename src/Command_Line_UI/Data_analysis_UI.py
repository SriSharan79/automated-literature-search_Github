
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from pathlib import Path


@dataclass
class PDFPathAnalysis:
    input_path: str
    kind: str  # "pdf_file" | "folder" | "not_found" | "not_supported"
    pdf_paths: List[str]
    pdf_count: int
    is_recursive: bool
    errors: List[str]


def analyse_pdf_input_path(path_input: str, recursive: bool = True) -> PDFPathAnalysis:
    """
    Analyse a path to determine whether it is:
      - a single PDF file, or
      - a folder containing PDFs (optionally with subfolders)

    Returns a structured result including:
      - list of discovered PDF paths
      - PDF count
      - any errors encountered
    """
    p = Path(path_input).expanduser()

    errors: List[str] = []

    if not p.exists():
        return PDFPathAnalysis(
            input_path=str(p),
            kind="not_found",
            pdf_paths=[],
            pdf_count=0,
            is_recursive=recursive,
            errors=[f"Path does not exist: {p}"],
        )

    # Case 1: direct PDF file
    if p.is_file():
        if p.suffix.lower() == ".pdf":
            return PDFPathAnalysis(
                input_path=str(p),
                kind="pdf_file",
                pdf_paths=[str(p.resolve())],
                pdf_count=1,
                is_recursive=False,
                errors=[],
            )
        return PDFPathAnalysis(
            input_path=str(p),
            kind="not_supported",
            pdf_paths=[],
            pdf_count=0,
            is_recursive=False,
            errors=[f"File is not a PDF: {p.name}"],
        )

    # Case 2: directory containing PDFs (possibly nested)
    if p.is_dir():
        pattern = "**/*.pdf" if recursive else "*.pdf"
        pdfs = sorted({x.resolve() for x in p.glob(pattern) if x.is_file() and x.suffix.lower() == ".pdf"})

        if not pdfs:
            errors.append("No PDF files found in the folder." if recursive else "No PDF files found in the folder (non-recursive).")

        return PDFPathAnalysis(
            input_path=str(p),
            kind="folder",
            pdf_paths=[str(x) for x in pdfs],
            pdf_count=len(pdfs),
            is_recursive=recursive,
            errors=errors,
        )

    # Anything else (symlink edge-cases, etc.)
    return PDFPathAnalysis(
        input_path=str(p),
        kind="not_supported",
        pdf_paths=[],
        pdf_count=0,
        is_recursive=recursive,
        errors=[f"Unsupported path type: {p}"],
    )

def get_file_or_folder():
    File_or_folder_Message = """ 
To support you with the literature analysis
Please let me know what is your input

1. Pdf File - Publication paper or any literature source (Single File)
2. Multiple Files- A set of pdf files in a specific location (Multiple Files)

Please input the file path of your choice (example: /path/to/input)
Input file path: """

    while True:
        raw = input(File_or_folder_Message).strip()

        # Remove surrounding quotes if the user pasted "..."
        if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
            raw = raw[1:-1].strip()

        # Don't force as_posix() for existence checks
        file_path = str(Path(raw).expanduser())

        # print("Normalised path:", file_path)

        result = analyse_pdf_input_path(file_path, recursive=True)
        print("\n input detected:", result.kind)

        if result.kind in ("pdf_file", "folder"):
            return result

        print("\nInvalid input. Please enter a valid file or folder path.")


if __name__ == "__main__":
    input_path="U:\Literature\Lai-et-al.-2021-Integrating-Safety-Analysis-into-Model-Based-Systems-Engineering-for-Aircraft-Systems-A-Literature-Review-and-Met2.pdf"
    res = analyse_pdf_input_path(input_path, recursive=True)
    print(res.kind, res.pdf_count)
    print(res.pdf_paths[:5])
    print(res.errors)
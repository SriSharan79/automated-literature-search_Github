import sys
import re
import json
from pathlib import Path

import fitz  # PyMuPDF


# Regex: matches headings like "1", "1.", "1.2", "1.2.3", etc., followed by some text
HEADING_PATTERN = re.compile(r"""^
    (?P<num>(\d+)(\.\d+)*\.?)   # 1 or 1.2 or 1.2.3 or 1.
    \s+
    (?P<title>.+)               # heading text
$""", re.VERBOSE)


def extract_pages_text(pdf_path: Path) -> str:
    doc = fitz.open(pdf_path)
    parts = []
    for page in doc:
        parts.append(page.get_text("text"))
    doc.close()
    return "\n".join(parts)


def parse_sections(text: str):
    """
    Build a nested dict of sections based on numeric headings.
    Example structure:
    {
        "1": {"title": "...", "text": "...", "subsections": {...}},
        "2": {...}
    }
    """
    lines = text.splitlines()
    root = {}
    stack = []  # each element: (level, section_dict)

    def create_section(number: str, title: str):
        return {
            "number": number,
            "title": title.strip(),
            "text": "",
            "subsections": {}
        }

    for line in lines:
        stripped = line.strip()
        if not stripped:
            # blank line -> treat as paragraph break
            if stack:
                stack[-1][1]["text"] += "\n"
            continue

        m = HEADING_PATTERN.match(stripped)
        if m:
            num = m.group("num").rstrip(".")
            title = m.group("title")
            level = num.count(".") + 1

            section = create_section(num, title)

            # Place this section at the right level
            # Pop stack until parent level found
            while stack and stack[-1][0] >= level:
                stack.pop()

            if not stack:
                # top-level section
                root[num] = section
            else:
                parent_dict = stack[-1][1]["subsections"]
                parent_dict[num] = section

            stack.append((level, section))
        else:
            # normal text line -> append to current section
            if stack:
                # add space if previous char is not newline
                current = stack[-1][1]
                if current["text"] and not current["text"].endswith(("\n", " ")):
                    current["text"] += " "
                current["text"] += stripped

    return root


def pdf_to_json(pdf_path_str: str):
    pdf_path = Path(pdf_path_str)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    text = extract_pages_text(pdf_path)
    sections = parse_sections(text)

    # Output JSON in same folder, same base name
    out_path = pdf_path.with_suffix(".json")
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(sections, f, ensure_ascii=False, indent=2)

    print(f"Saved sections JSON to: {out_path}")


if __name__ == "__main__":

    pdf_path = "/localdata/user/kata_du/Automated Literature Survey/downloads/Test_Folder/2020-A systematic literature review of cross-domain mod.pdf"
    pdf_to_json(pdf_path)

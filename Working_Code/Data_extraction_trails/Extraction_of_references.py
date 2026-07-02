import sys
import json
import re
from pathlib import Path

import fitz  # PyMuPDF


# Common titles for references section (case-insensitive)
REFERENCES_TITLES = {
    "references", "bibliography", "reference list", "literature cited",
    "works cited", "sources", "literatur", "références"
}


def extract_pages_text(pdf_path: Path) -> str:
    doc = fitz.open(pdf_path)
    parts = []
    for page in doc:
        parts.append(page.get_text("text"))
    doc.close()
    return "\n".join(parts)


def find_references_section(text: str):
    """
    Use HEADING_PATTERN to find section titles, then check if title contains 
    references keywords. Extract text until next heading.
    """
    lines = text.splitlines()
    
    # Same pattern as before
    HEADING_PATTERN = re.compile(r"""^
        (?P<num>(\d+)(\.\d+)*\.?)   # 1 or 1.2 or 1.2.3 or 1.
        \s+
        (?P<title>.+)               # heading text
    $""", re.VERBOSE)
    
    REFERENCES_KEYWORDS = {"references", "bibliography", "reference list", 
                          "literature cited", "works cited", "sources", 
                          "literatur", "références"}
    
    current_section_text = ""
    refs_section = None
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            current_section_text += "\n"
            continue
            
        match = HEADING_PATTERN.match(stripped)
        if match:
            # Save previous section if it was references
            if refs_section is not None:
                return refs_section
            
            # Check new heading
            title = match.group("title").lower()
            if any(keyword in title for keyword in REFERENCES_KEYWORDS):
                refs_section = current_section_text.strip()
            
            # Start new section
            current_section_text = ""
        else:
            current_section_text += stripped + " "
    
    # Check final section
    if refs_section is not None:
        return refs_section
    return None



def pdf_extract_references(pdf_path_str: str):
    pdf_path = Path(pdf_path_str)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    text = extract_pages_text(pdf_path)
    refs_text = find_references_section(text)

    if refs_text is None:
        print("No References section found.")
        return

    # Save as simple JSON: {"references_section": "full text here"}
    data = {"references_section": refs_text}
    out_path = pdf_path.with_suffix(".references.json")
    
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"Saved References section to: {out_path}")


if __name__ == "__main__":

    pdf_path = "/localdata/user/kata_du/Automated Literature Survey/downloads/Test_Folder/2018-Survey of methods for design of collaborative robo.pdf"
    pdf_extract_references(pdf_path)

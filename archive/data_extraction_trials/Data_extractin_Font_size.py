import sys
import json
from pathlib import Path
from collections import defaultdict, Counter
import fitz  # PyMuPDF


def analyze_font_sizes(page_dict):
    """Find all used font sizes and their usage frequency."""
    sizes = []
    for block in page_dict["blocks"]:
        if block["type"] == 0:  # text block
            for line in block["lines"]:
                for span in line["spans"]:
                    sizes.append(round(span["size"], 1))  # round to avoid floating point noise
    counter = Counter(sizes)
    return counter


def is_heading_span(span, body_size, tolerance=1.5):
    """Check if span qualifies as heading based on font size."""
    size = round(span["size"], 1)
    return size > body_size * tolerance


def extract_sections(pdf_path: Path):
    """Extract sections using font size hierarchy."""
    doc = fitz.open(pdf_path)
    
    # First pass: determine body font size (most common)
    all_sizes = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        page_dict = page.get_text("dict")
        sizes = analyze_font_sizes(page_dict)
        all_sizes.extend(sizes)
    
    if not all_sizes:
        return {}
    
    body_size = Counter(all_sizes).most_common(1)[0][0]
    
    # Second pass: extract structured text
    sections = []
    current_section = None
    stack = []  # for subsections
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        page_dict = page.get_text("dict")
        
        for block in page_dict["blocks"]:
            if block["type"] != 0:
                continue
                
            for line in block["lines"]:
                # Check if line is a heading
                spans = line["spans"]
                heading_spans = [s for s in spans if is_heading_span(s, body_size)]
                
                if heading_spans:
                    # Create new section from heading spans
                    heading_text = "".join(s["text"].strip() for s in heading_spans).strip()
                    if len(heading_text) > 1:  # ignore tiny headings
                        
                        # Determine level based on font size (bigger = higher level)
                        heading_size = max(s["size"] for s in heading_spans)
                        level = 1
                        if heading_size <= body_size * 2.5:
                            level = 2
                        elif heading_size <= body_size * 1.8:
                            level = 3
                        
                        new_section = {
                            "level": level,
                            "title": heading_text,
                            "text": "",
                            "subsections": []
                        }
                        
                        # Pop stack to find parent level
                        while stack and stack[-1]["level"] >= level:
                            stack.pop()
                        
                        if stack:
                            stack[-1]["subsections"].append(new_section)
                        else:
                            sections.append(new_section)
                        
                        stack.append(new_section)
                        current_section = new_section
                        
                else:
                    # Normal text - add to current section
                    if current_section:
                        line_text = "".join(s["text"] for s in spans).strip()
                        if line_text:
                            if current_section["text"]:
                                current_section["text"] += " "
                            current_section["text"] += line_text
    
    doc.close()
    return {"document": pdf_path.name, "body_font_size": body_size, "sections": sections}


def pdf_to_json(pdf_path_str: str):
    pdf_path = Path(pdf_path_str)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    sections_data = extract_sections(pdf_path)
    
    # Save JSON in same folder
    out_path = pdf_path.with_suffix(".json")
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(sections_data, f, ensure_ascii=False, indent=2)
    
    print(f"Saved sections JSON to: {out_path}")
    print(f"Detected body font size: {sections_data['body_font_size']}")
    print(f"Found {len(sections_data['sections'])} sections")


if __name__ == "__main__":
    pdf_path = "/localdata/user/kata_du/Automated Literature Survey/downloads/Test_Folder/2020-A systematic literature review of cross-domain mod.pdf"
    pdf_to_json(pdf_path)

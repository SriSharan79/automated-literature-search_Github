import re
import json
import fitz  # PyMuPDF
from pathlib import Path
import os

# Section patterns for organizations (hierarchical headings)
ORGANIZATION_PATTERNS = {
    'IEEE': {
        'title': r'^[A-Z\s]{10,}$',
        'abstract': r'Abstract[—\-]\s*',
        'keywords': r'(Keywords|Index Terms)[:\-]\s*',
        'headings': [
            r'^\s*[IVX]+\.\s+[A-Z]',  # I. INTRODUCTION
            r'^\s*[A-Z]\.\s+[A-Z]',   # A. Subsection
            r'^\s*\d+\.\s+[A-Za-z]',  # 1. Subsubsection
            r'^\s*[a-z]\)\s+[A-Za-z]'
        ],
        'references': r'(References|Bibliography)[\s\-:]*'
    },
    'INCOSE': {
        'title': r'^[A-Z][A-Za-z\s\.\,\-\&]{10,}$',
        'abstract': r'Abstract[\s\-:]*',
        'headings': [
            r'^\s*[1-9]\s+[A-Z]',
            r'^\s*[A-Z][A-Za-z\s]+$', 
            r'^\s*[1-9]\.\d+\s+'
        ],
        'references': r'(References|Bibliography)[\s\-:]*'
    },
    'ASME': {
        'title': r'^[A-Z][A-Za-z\s\.\,\-\&]{10,}$',
        'abstract': r'(ABSTRACT|Abstract)[\s\-:]*',
        'headings': [r'^\s*[1-9]\s+[A-Z]', r'^\s*[A-Z][A-Z\s]+$']
    },
    'ACM': {
        'title': r'^[A-Z][A-Za-z\s]{10,}$',
        'abstract': r'Abstract[\s\-:]*',
        'keywords': r'(CCS Concepts|Keywords|Categories)[:\-]\s*',
        'headings': [r'^\s*[1-9]\s+[A-Z]', r'^\s*\d+\.\d+\s+']
    },
    'AIAA': {
        'title': r'^[A-Z][A-Za-z\s]{10,}$',
        'abstract': r'(ABSTRACT|Abstract)[\s\-:]*',
        'headings': [r'^\s*[IVXLC]+\.\s+', r'^\s*[A-Z]+\s+[A-Z]']
    }
}

def detect_organization(text):
    """Detect organization from text patterns"""
    for org, patterns in ORGANIZATION_PATTERNS.items():
        if re.search(patterns.get('title', ''), text, re.MULTILINE):
            return org
    return 'Generic'

def extract_pdf_sections(pdf_path):
    """Extract sections from PDF"""
    doc = fitz.open(pdf_path)
    full_text = ""
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        full_text += page.get_text() + "\n\n"
    
    org = detect_organization(full_text)
    sections = parse_sections(full_text, org)
    
    return {
        'filename': Path(pdf_path).name,
        'organization': org,
        'total_pages': len(doc),
        'sections': sections
    }

def parse_sections(text, organization):
    """Parse text into sections based on organization patterns"""
    patterns = ORGANIZATION_PATTERNS.get(organization, {})
    
    sections = {
        'title': '',
        'authors': '',
        'abstract': '',
        'keywords': '',
        'body_sections': [],
        'references': ''
    }
    
    lines = text.split('\n')
    current_section = 'other'
    section_content = []
    
    for i, line in enumerate(lines):
        line_stripped = line.strip()
        
        # Title (first major heading)
        if not sections['title'] and re.match(patterns.get('title', r'^[A-Z]{10,}'), line_stripped):
            sections['title'] = line_stripped
            continue
            
        # Abstract
        if re.search(patterns.get('abstract', r'Abstract'), line_stripped, re.IGNORECASE):
            if section_content:
                sections[current_section] = sections.get(current_section, '') + '\n'.join(section_content)
            current_section = 'abstract'
            section_content = [line_stripped]
            continue
            
        # Keywords
        if re.search(patterns.get('keywords', r'Keywords'), line_stripped, re.IGNORECASE):
            sections['abstract'] = '\n'.join(section_content) if section_content else ''
            current_section = 'keywords'
            section_content = [line_stripped]
            continue
            
        # References
        if re.search(patterns.get('references', r'References'), line_stripped, re.IGNORECASE):
            sections[current_section] = '\n'.join(section_content) if section_content else ''
            current_section = 'references'
            section_content = [line_stripped]
            continue
            
        # Body headings
        is_heading = False
        for heading_pattern in patterns.get('headings', []):
            if re.match(heading_pattern, line_stripped, re.MULTILINE):
                if section_content and current_section != 'other':
                    sections[current_section] = '\n'.join(section_content)
                
                section_name = line_stripped.split('.')[0].strip()[:50]
                sections['body_sections'].append({'title': line_stripped, 'content': ''})
                current_section = f"section_{len(sections['body_sections'])-1}"
                section_content = []
                is_heading = True
                break
        
        if not is_heading and line_stripped:
            section_content.append(line)
    
    if section_content:
        sections[current_section] = '\n'.join(section_content)
    
    return sections

def extract_sections(pdf_path, output_path=None):
    """Main function: PDF path -> JSON file (auto or specified)"""
    result = extract_pdf_sections(pdf_path)
    
    # Auto-generate output path if not provided
    if output_path is None:
        output_path = f"{Path(pdf_path).stem}_sections.json"
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    
    print(f"✅ Extracted sections from {result['filename']}")
    print(f"📋 Detected organization: {result['organization']}")
    print(f"📄 Total pages: {result['total_pages']}")
    print(f"📂 Saved to: {output_path}")
    print(f"📋 Body sections found: {len(result['sections']['body_sections'])}")
    return output_path

# ✅ HARDCODED PDF PATH - Just run the script!
if __name__ == "__main__":
    # Your specific PDF path
    pdf_path = "/localdata/user/kata_du/Automated Literature Survey/downloads/Test_Folder/2018-Survey of methods for design of collaborative robo.pdf"
    
    # Check if file exists
    if not os.path.exists(pdf_path):
        print(f"❌ Error: PDF file not found: {pdf_path}")
        print("Please check the file path!")
    else:
        # ✅ AUTOMATIC: Same name as PDF + "_sections.json"
        output_dir = os.path.dirname(pdf_path)
        pdf_name = Path(pdf_path).stem  # "nan_R Al Fawares_ITDTP"
        output_filename = f"{pdf_name}_sections.json"  # "nan_R Al Fawares_ITDTP_sections.json"
        output_path = os.path.join(output_dir, output_filename)
        
        extract_sections(pdf_path, output_path)
        print(f"\n🎉 Done! Check: {output_path}")

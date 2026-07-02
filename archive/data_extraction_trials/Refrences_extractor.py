import json
import sys
import os

def main(json_path):
    keywords = ["references", "bibliography", "reference list", 
                   "literature cited", "works cited", "sources", 
                   "literatur", "références"]  # Keywords to match partially
    
    all_lines = []  # Collect all lines here
    
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if isinstance(data, list):
            for obj in data:
                section_name = obj.get('Section Name', '').lower()
                text_content = obj.get('Text_Content', '')
                if section_name in keywords and isinstance(text_content, str):
                    # Remove all newlines before splitting
                    cleaned_content = text_content.replace('\\n', '').replace('\n', '')
                    lines = cleaned_content.split('-')
                    all_lines.extend(line.strip() for line in lines if line.strip())
        elif isinstance(data, dict):
            for section_name, text_content in data.items():
                if isinstance(text_content, str):
                    section_lower = section_name.lower()
                    if section_lower in keywords:
                        # Remove all newlines before splitting
                        cleaned_content = text_content.replace('\\n', '').replace('\n', '')
                        lines = cleaned_content.split('-')
                        all_lines.extend(line.strip() for line in lines if line.strip())
        else:
            print("Error: Invalid JSON structure - expected dict or list.", file=sys.stderr)
            return
        
        # Write to output.txt
        output_path = os.path.splitext(json_path)[0] + '_output.txt'
        with open(output_path, 'w', encoding='utf-8') as f:
            for line in all_lines:
                f.write(line + '\n')
        
        print(f"Lines written to {output_path}: {len(all_lines)} lines")
    
    except FileNotFoundError:
        print(f"Error: File '{json_path}' not found.", file=sys.stderr)
    except json.JSONDecodeError:
        print("Error: Invalid JSON file.", file=sys.stderr)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)

if __name__ == "__main__":

    main("/localdata/user/kata_du/Automated Literature Survey/downloads/Test_Folder/Section_JSON_Files/8a6b8ab3_sections.json")

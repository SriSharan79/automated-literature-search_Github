import json
import difflib
import sys

def json_similarity(file1_path, file2_path):
    # Load JSON files
    with open(file1_path, 'r') as f1:
        data1 = json.load(f1)
    with open(file2_path, 'r') as f2:
        data2 = json.load(f2)
    
    # Serialize to canonical strings (sorted keys, indent=2 for consistency)
    str1 = json.dumps(data1, sort_keys=True, indent=2)
    str2 = json.dumps(data2, sort_keys=True, indent=2)
    
    # Compute similarity ratio (0.0 to 1.0)
    matcher = difflib.SequenceMatcher(None, str1, str2)
    ratio = matcher.ratio()
    
    # Convert to percentage
    percentage = round(ratio * 100, 2)
    print(f"Matching percentage: {percentage}%")
    return percentage

if __name__ == "__main__":


    file1_path="/localdata/user/kata_du/Automated Literature Survey/downloads/Test_Folder/2020-A systematic literature review of cross-domain mod_Docling_sections.json"
    file2_path="/localdata/user/kata_du/Automated Literature Survey/downloads/Test_Folder/2020-A systematic literature review of cross-domain mod_Docling_sections.json"
    
    
    json_similarity(file1_path, file2_path)

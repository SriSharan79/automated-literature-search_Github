import os
import pandas as pd

def rename_pdfs(folder_path, excel_file, existing_col, new_name_col):
    """
    Renames PDF files based on a mapping in an Excel file.
    Only the text after the last '_' in the filename is replaced.

    Args:
        folder_path   : Path to the folder containing PDFs
        excel_file    : Path to the Excel file with rename mapping
        existing_col  : Column name for current file names
        new_name_col  : Column name for new suffix (after last '_')

    Returns:
        dict with 'renamed' (list of tuples) and 'not_found' (list)
    """
    df = pd.read_excel(excel_file)
    results = {"renamed": [], "not_found": []}

    for _, row in df.iterrows():
        existing_name = str(row[existing_col]).strip()
        new_suffix = str(row[new_name_col]).strip()

        # Ensure .pdf extension
        if not existing_name.endswith('.pdf'):
            existing_name += '.pdf'

        old_path = os.path.join(folder_path, existing_name)

        if not os.path.exists(old_path):
            results["not_found"].append(existing_name)
            continue

        base_name = existing_name[:-4]  # remove .pdf
        if '_' in base_name:
            prefix = base_name.rsplit('_', 1)[0]  # everything before last '_'
            new_base = f"{prefix}_{new_suffix[:40]}"
        else:
            new_base = new_suffix  # no underscore found, replace whole name

        new_name = new_base + '.pdf'
        new_path = os.path.join(folder_path, new_name)

        os.rename(old_path, new_path)
        results["renamed"].append((existing_name, new_name))

    # Print summary
    print(f"✅ Renamed {len(results['renamed'])} file(s).")
    if results["not_found"]:
        print(f"⚠️  Not found ({len(results['not_found'])}): {results['not_found']}")

    return results

if __name__ == "__main__":

    results = rename_pdfs(
    folder_path   = "/remotedata/U/DLR+kata_du/ALR DATA/00_Container/Combined_DB/AI_SE_Domain/Overviews_pdfsto be read/Pdfs",
    excel_file    = "/remotedata/U/DLR+kata_du/ALR DATA/00_Container/Combined_DB/AI_SE_Domain/Overviews_pdfsto be read/Overview_report.xlsx",
    existing_col  = "Filename",
    new_name_col  = "Title"
)

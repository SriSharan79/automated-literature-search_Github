import os
import sys

# faiss is heavyweight; imported lazily inside the functions that use it.
from colorama import Fore, Back, Style, init
# ADD to the imports:
from alr.common.sections import build_sections_map_full, build_sections_map_ra_kc
import pandas as pd
from pathlib import Path
from alr.common.excel_utils import extract_column, get_column_value
import json
from datetime import datetime
from alr.common.file_manager import DataAnalyzeManager, Vec_DB_Manager
from alr.common.file_handlers import move_matching_pdfs,copy_file,copy_matching_pdfs,copy_matching_jsons,sanitize_path_length
from alr.common.excel_utils import aggregate_query_excel_data
from alr.rag_builders.vector_db_updater import search_similar
# Initialize colorama (autoreset ensures colors don't bleed into the next line)
init(autoreset=True)



def batch_enrich_reports(base_storage_path):
    """
    Scans base_storage_path for *_query_Overview_report.xlsx,
    locates the associated Abstract_Json_files folder, and enriches them.
    """
    storage_root = Path(base_storage_path)
    
    if not storage_root.exists():
        print(f"{Fore.RED}Error: Storage path {base_storage_path} does not exist.")
        return

    print(f"{Fore.CYAN}{Style.BRIGHT}--- Starting Batch Enrichment Scan ---")
    print(f"{Fore.LIGHTBLACK_EX}Searching in: {storage_root}\n")

    # 1. Identify all matching Excel files recursively
    # This looks for any file ending in '_query_Overview_report.xlsx'
    report_files = list(storage_root.rglob("*_query_Overview_report.xlsx"))

    if not report_files:
        print(f"{Fore.YELLOW}No overview reports found.")
        return

    print(f"{Fore.GREEN}Found {len(report_files)} report(s) to process.")

    for excel_path in report_files:
        print(f"\n{Fore.BLUE}Processing: {excel_path.name}")
        
        # 2. Identify the Abstract_Json_files folder
        # Based on your structure: parent_dir / "Abstract_Json_files"
        json_folder = excel_path.parent / "Abstract_Json_files"
        
        if json_folder.exists() and json_folder.is_dir():
            print(f"{Fore.LIGHTBLACK_EX}Found JSON source: {json_folder}")
            
            # 3. Call your enrichment function
            try:
                enrich_overview_with_abstracts(excel_path, json_folder)
                print(f"{Fore.GREEN}Successfully enriched {excel_path.name}")
            except Exception as e:
                print(f"{Fore.RED}Failed to enrich {excel_path.name}: {e}")
        else:
            print(f"{Fore.RED}Skipping: Abstract_Json_files folder not found at {json_folder}")

    print(f"\n{Fore.CYAN}{Style.BRIGHT}--- Batch Processing Complete ---")

def generate_query_report_RA_KC(query_list, Storage_path, top_k: int = 20):
    import faiss

    VDB = Vec_DB_Manager(Storage_path)
    sec_map = build_sections_map_ra_kc(VDB)
    results_storage = VDB.results

    for query in query_list:
        VDB.update_query_folder(query)
        for attr, (_ex, _j, bin_path) in sec_map.items():
            if (not bin_path.exists()) or bin_path.stat().st_size == 0:                
                print(f'No Vector database for {attr}')
                continue
            
            index = faiss.read_index(str(bin_path))
            VDB.update_key_folder(attr)

            # Extract content from the Excel file
            strings = extract_column(_ex, "Content")
            
            if len(strings) != index.ntotal:
                print(f'Data matching error \n Strings : {len(strings)}\n Index length : {index.ntotal}')
                continue
            
            # Perform similarity search
            scores, ids = search_similar(bin_path, query, top_k=top_k)

            # Prepare results
            print("Top matches:")
            result_data = []
            for s, i in zip(scores, ids):
                if i < 0:
                    continue  # FAISS pads with -1 when top_k > indexed vectors
                matched_text = strings[i] if i < len(strings) else '(newly added item)'
                print(f"idx={i}  cosine={s:.4f}  text={matched_text}")
                
                # Extract the existing data (UUID, Title, etc.)
                uuid = get_column_value(_ex, "UUID", i)
                original_uuid = get_column_value(_ex, "Original_UUID", i)
                title = get_column_value(_ex, "Title", i)
                filename = get_column_value(_ex, "Filename", i)
                count = get_column_value(_ex, "Count", i)  
                content = get_column_value(_ex, "Content", i)
                
                result_data.append({
                    "UUID": uuid,
                    "Original_UUID": original_uuid,
                    "Title": title,
                    "Filename": filename,
                    "Count": count,
                    "Content": content,
                    "Cosine Similarity": s
                })

            # Create DataFrame with results including original data
            df = pd.DataFrame(result_data)
            
            # Generate a unique filename for the output Excel file
            query_safe = query.replace(" ", "_")  # Safe version of the query for filenames
            file_name = f"query_{query_safe}_report.xlsx"
            file_path = Path(VDB.key_folder) / file_name

            q_file_path=Path(VDB.query_storage) / file_name

            # Save results to a new Excel file
            with pd.ExcelWriter(q_file_path, engine="openpyxl", mode="w") as writer:
                df.to_excel(writer, index=False, sheet_name="Results")
            
            # Save results to a new Excel file
            with pd.ExcelWriter(file_path, engine="openpyxl", mode="w") as writer:
                df.to_excel(writer, index=False, sheet_name="Results")
            
            print(f"Report saved at: {file_path}")

        overview_File_path =  Path(VDB.query_storage) /f"{attr}_query_Overview_report.xlsx"
        aggregate_query_excel_data(Path(VDB.query_storage), "Title", overview_File_path)   

        print(f"Report saved at: {VDB.query_storage}")        


def generate_query_report(query_list, storage_path, search_root='/remotedata/U/DLR+kata_du/ALR DATA', top_k: int = 50):
    print(f"{Fore.CYAN}{Style.BRIGHT}--- Initializing Report Generation for {len(query_list)} queries ---")
    
    vdb = Vec_DB_Manager(storage_path)
    mf = DataAnalyzeManager(storage_path)
    
    print(f"{Fore.YELLOW}Building sections map...")
    sec_map = build_sections_map_full(vdb)
    print(f"{Fore.GREEN}Sections map built with {len(sec_map)} attributes.")

    for idx, query in enumerate(query_list, 1):        
        print(f"\n{Back.BLUE}{Fore.WHITE}{Style.BRIGHT} [{idx}/{len(query_list)}] Processing query: '{query}' ")
        vdb.update_query_folder(query)
        
        # 1. Generate individual attribute reports
        print(f"{Fore.CYAN} > [Step 1] Generating individual attribute reports...")
        for attr, (_ex, _j, bin_path) in sec_map.items():
            process_attribute_query(query, attr, _ex, bin_path, vdb, top_k=top_k)

        # 2. Generate the Overview Report
        print(f"{Fore.CYAN} > [Step 2] Aggregating results into Overview Report...")
        overview_path = Path(vdb.query_storage) / f"{query}_query_Overview_report.xlsx"
        overview_path = sanitize_path_length(overview_path)
        aggregate_query_excel_data(vdb.query_storage, "Title", overview_path)
        print(f"{Fore.GREEN}   - Overview saved: {overview_path}")

        # 3. Harvest associated files
        print(f"{Fore.CYAN} > [Step 3] Harvesting associated resources (PDFs/JSONs)...")
        harvest_query_resources(overview_path, search_root, vdb, mf)

        print(f"{Fore.GREEN}{Style.BRIGHT}Workflow complete for: '{query}'")
        print(f"{Fore.LIGHTBLACK_EX}Path: {vdb.query_storage}")

    print(f"\n{Fore.MAGENTA}{Style.BRIGHT}--- ALL TASKS FINISHED ---")


def process_attribute_query(query, attr, excel_ref, bin_path, vdb, top_k: int = 50):
    import faiss

    if not bin_path.exists() or bin_path.stat().st_size == 0:
        print(f"{Fore.RED}   [!] Warning: No Vector DB for '{attr}'. Skipping.")
        return

    print(f"{Fore.LIGHTBLUE_EX}   - Processing attribute: {attr}")
    vdb.update_key_folder(attr)
    index = faiss.read_index(str(bin_path))
    strings = extract_column(excel_ref, "Content")

    if len(strings) != index.ntotal:
        print(f"{Fore.RED}{Style.BRIGHT}   [!] Data matching error for {attr}")
        return

    scores, ids = search_similar(bin_path, query, top_k=top_k)

    result_data = []
    for s, i in zip(scores, ids):
        if i < 0:
            continue  # FAISS pads with -1 when top_k > indexed vectors
        result_data.append({
            "Original_UUID": get_column_value(excel_ref, "Original_UUID", i),
            "Title": get_column_value(excel_ref, "Title", i),
            "Filename": get_column_value(excel_ref, "Filename", i),
            "Content": strings[i] if i < len(strings) else '(newly added item)',
            "Cosine Similarity": s
        })

    df = pd.DataFrame(result_data)
    query_safe = query.replace(" ", "_")
    file_name = f"{attr}_query_{query_safe}_report.xlsx"
    
    for target_dir in [vdb.key_folder, vdb.query_storage]:
        save_path = Path(target_dir) / file_name
        save_path = sanitize_path_length(save_path)
        df.to_excel(save_path, index=False, engine="openpyxl")

def enrich_overview_with_abstracts(overview_path, json_folder):
    """
    Reads the overview Excel, fetches details from local JSON files based on UUID,
    and updates the Excel with research-specific columns.
    """
    print(f"{Fore.YELLOW}Updating Overview with Abstract details...")
    
    df = pd.read_excel(overview_path)
    
    # Define the fields we want to extract from the JSON
    abstract_fields = ["Research Problem","Research_Areas","Key_Concepts", "Objective", "Methodology", "Results", "Conclusion"]
    
    # Initialize columns if they don't exist
    for field in abstract_fields:
        if field not in df.columns:
            df[field] = None

    updated_count = 0

    for index, row in df.iterrows():
        uuid = str(row['Original_UUID'])
        json_file = Path(json_folder) / f"{uuid}_Abstract.json"
        
        if json_file.exists():
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                for field in abstract_fields:
                    # Get data; handle lists by joining them into a single string
                    val = data.get(field, "")
                    if isinstance(val, list):
                        val = "\n •  ".join(val)
                    df.at[index, field] = val
                
                updated_count += 1
            except Exception as e:
                print(f"{Fore.RED}   [!] Error reading JSON for UUID {uuid}: {e}")
        else:
            # Optional: Log missing JSONs if needed
            pass

    # Save the enriched dataframe back to the same path
    df.to_excel(overview_path, index=False, engine="openpyxl")
    print(f"{Fore.GREEN}   - Enrichment complete. {updated_count} rows updated with Abstract data.")

def harvest_query_resources(overview_path, search_root, vdb, mf):
    df_overview = pd.read_excel(overview_path)

    def abs_json_file_list_formulator(UUID_list):
        return [s + "_Abstract.json" for s in UUID_list]
    
    unique_filenames = df_overview['Filename'].unique().tolist()
    org_uuids = df_overview['Original_UUID'].unique().tolist()
    UUID_abs_json_files = abs_json_file_list_formulator(org_uuids)

    # print(f"{Fore.LIGHTBLACK_EX}     * Copying {len(unique_filenames)} PDFs...")
    # copy_matching_pdfs(unique_filenames, search_root, Path(vdb.querry_storage_pdfs))

    print(f"{Fore.LIGHTBLACK_EX}     * Copying {len(UUID_abs_json_files)} Abstract JSONs...")
    abs_json_dest = Path(vdb.querry_storage_Abs_jsons)
    abs_json_dest.mkdir(parents=True, exist_ok=True)
    copy_matching_jsons(UUID_abs_json_files, search_root, abs_json_dest)

    # 3. New Step: Enrich Excel with data from those JSONs
    print(f"{Fore.CYAN} > [Step 4] Merging JSON metadata into Excel...")
    enrich_overview_with_abstracts(overview_path, abs_json_dest)



if __name__ == "__main__":
    
    storage_path='/remotedata/U/DLR+kata_du/ALR DATA/00_Container/Combined_DB/AI_SE_Domains' 

    # enrich_overview_with_abstracts(overview_path, abs_json_dest)
    # batch_enrich_reports(storage_path)

    aircraft_systems_engineering_topics = [
    "Systems Engineering for Aircraft system development",
    "Safety Engineering for Aircraft system development",
    "Model based system Engineering (MBSE) for Aircraft system development",
    "Model based safety assessments (MBSA) for Aircraft system development",
    "Certification of aircraft systems",
    "Application of AI in Aircraft system development and Safety assessments",
    "Usage of LLM and NLP concepts for the Documentation of Aircraft System development and certification",
    "Applications of AI in different Model based system Engineering (MBSE) Workflows",
    "Applications of AI in different Safety assessments",
    "LLMs applications in Model based system Engineering (MBSE)",
    "LLMs applications in Model based Safety assessments (MBSA)" ]

    systems_engineering_topics = [
    "Systems Engineering",
    "Safety Engineering" ,
    "Model based system Engineering",
    "Model based safety assessments",
    "Requirements Engineering",
    "Risk Assessment",
    "Aircraft Certification","Hazard analysis",
    "Large Language Models" ]

    test=["Hazard analysis"]

    generate_query_report(['Aircraft System Development'],storage_path)



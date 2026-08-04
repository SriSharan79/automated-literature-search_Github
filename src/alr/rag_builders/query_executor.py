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
from alr.common.file_handlers import move_matching_pdfs,copy_file,copy_matching_pdfs,copy_matching_jsons,sanitize_path_length,index_jsons_under_root
from alr.common.excel_utils import aggregate_query_excel_data
from alr.rag_builders.vector_db_updater import search_similar
from alr.rag_builders.master_excel_db_builder import _append_skiplog, _skip_row
from datetime import datetime as dt      # Alias avoids conflicts
# Initialize colorama (autoreset ensures colors don't bleed into the next line)
init(autoreset=True)

# Attributes/queries a query run could not search or report. One failure never
# stops the run: it is skipped, recorded here (beside the query results) and
# the run continues.
QUERY_SKIPPED_LOG = "Query_not_added.xlsx"



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


def generate_query_report(query_list, storage_path, search_root='/remotedata/U/DLR+kata_du/ALR DATA', top_k: int = 50,
                          section_keys=None, enrich_keys=None, harvest_files=False,
                          progress_callback=None, resolve_search_root=None):
    """
    section_keys: optional iterable of section keys to query (any mix of
    abstract, Introduction and Results & Conclusion attributes — see
    sections.ALL_RAG_SECTIONS). Defaults to the abstract sections. Sections
    whose vector DB doesn't exist in this storage location are skipped with
    a warning (see process_attribute_query).

    enrich_keys: optional iterable of section keys to add as **columns** on the
    overview report (independent of which sections were searched). Defaults to
    the abstract attributes.

    harvest_files: file harvesting is a USER CHOICE and off by default — no
    PDFs or analysis JSONs are copied into the query folder; the overview is
    enriched by reading the JSONs directly from the storage space's analysis
    folders. Pass True to also copy the matched JSONs next to the report
    (the old behaviour, via harvest_query_resources).

    progress_callback(done, total, text): optional; called after every unit of
    work — one per section searched plus the overview aggregation and the
    enrich/harvest step of each query — so a UI can drive a determinate bar.
    """
    print(f"{Fore.CYAN}{Style.BRIGHT}--- Initializing Report Generation for {len(query_list)} queries ---")

    vdb = Vec_DB_Manager(storage_path)
    mf = DataAnalyzeManager(storage_path)

    print(f"{Fore.YELLOW}Building sections map...")
    sec_map = build_sections_map_full(vdb, only=section_keys)
    print(f"{Fore.GREEN}Sections map built with {len(sec_map)} attributes.")

    # Work units: each section search + the overview step + the harvest step.
    total_units = len(query_list) * (len(sec_map) + 2)
    done_units = 0

    def tick(text):
        nonlocal done_units
        done_units += 1
        if progress_callback:
            progress_callback(done_units, total_units, text)

    not_added = []
    for idx, query in enumerate(query_list, 1):
        print(f"\n{Back.BLUE}{Fore.WHITE}{Style.BRIGHT} [{idx}/{len(query_list)}] Processing query: '{query}' ")
        started_at = datetime.now()
        try:
            vdb.update_query_folder(query)
        except Exception as e:
            print(f"{Fore.RED}   [!] Could not prepare the query folder for '{query}' "
                  f"({type(e).__name__}: {e}) — skipped, continuing with the next query.")
            not_added.append(_skip_row("query", e, title=query))
            continue

        # 1. Generate individual attribute reports (into the per-query
        #    Attribute_Query_Results sub-folder). One attribute that cannot be
        #    searched (no index, index/Excel mismatch, unreadable file) is
        #    skipped and recorded; the query still produces its report.
        print(f"{Fore.CYAN} > [Step 1] Generating individual attribute reports...")
        for attr, (_ex, _j, bin_path) in sec_map.items():
            try:
                reason = process_attribute_query(query, attr, _ex, bin_path, vdb, top_k=top_k)
            except Exception as e:
                print(f"{Fore.RED}   [!] Attribute '{attr}' failed ({type(e).__name__}: {e}) "
                      f"— skipped, continuing with the next attribute.")
                reason = f"{type(e).__name__}: {e}"
            if reason:
                not_added.append(_skip_row("attribute", reason, title=query, sections=attr))
            tick(f"Searched section: {attr}")

        try:
            # 2. Generate the Overview Report from the attribute Excels (which now
            #    live in their own sub-folder, so the overview sits alone in the
            #    query folder root instead of amongst repeated attribute Excels).
            print(f"{Fore.CYAN} > [Step 2] Aggregating results into Overview Report...")
            overview_path = Path(vdb.query_storage) / f"{query}_query_Overview_report.xlsx"
            overview_path = sanitize_path_length(overview_path)
            aggregate_query_excel_data(vdb.query_attr_storage, "Title", overview_path)
            print(f"{Fore.GREEN}   - Overview saved: {overview_path}")
            tick("Aggregated the overview report")

            # 3. Enrich the overview with the analyzed attribute data. This ALWAYS
            #    runs -- data enrichment no longer depends on the optional file
            #    harvest. Harvesting (copying the JSON files next to the report) is
            #    still the user's choice; when it's off we read the JSONs straight
            #    from the space's analysis folders, falling back to a search of
            #    ``search_root`` so a common/combined DB (whose own analysis folders
            #    are empty) still gets enriched.
            space_folders = {
                "abstract": mf.AD_Abstract,
                "intro": mf.AD_Intro,
                "rescon": mf.AD_ResCon,
            }
            if harvest_files:
                print(f"{Fore.CYAN} > [Step 3] Harvesting associated resources (PDFs/JSONs) and enriching...")
                enriched = harvest_query_resources(overview_path, search_root, vdb, mf, enrich_keys=enrich_keys)
            else:
                print(f"{Fore.CYAN} > [Step 3] No file harvest (user choice) — enriching the "
                      "overview from the space's analysis JSONs (with a search-root fallback)...")
                enriched = enrich_overview_with_abstracts(
                    overview_path, space_folders, enrich_keys=enrich_keys, search_root=search_root)

            # If nothing could be enriched (the space holds no analysis JSONs and the
            # current search root didn't turn any up), ask the caller for a better
            # search root and retry once. The UI pops a folder picker and remembers
            # the choice for the next queries.
            if not enriched and resolve_search_root is not None:
                print(f"{Fore.YELLOW}   - No attribute data found under the current search root; "
                      "asking for a search location...")
                new_root = resolve_search_root()
                if new_root:
                    search_root = new_root
                    enriched = enrich_overview_with_abstracts(
                        overview_path, space_folders, enrich_keys=enrich_keys, search_root=new_root)
            tick("Enriched the report")

            # 4. Append this query's details to the space's rolling query log.
            elapsed = (datetime.now() - started_at).total_seconds()
            _log_query_run(vdb.query_log_excel, {
                "Timestamp": started_at.strftime("%Y-%m-%d %H:%M:%S"),
                "Query": query,
                "Space Queried": str(storage_path),
                "Model Used": _embedding_model_label(),
                "Sections Queried": ", ".join(sec_map.keys()),
                "Attributes Included": ", ".join(
                    enrich_keys if enrich_keys is not None else [s.key for s in _default_enrich_sections()]),
                "Top K": top_k,
                "Files Harvested": "Yes" if harvest_files else "No",
                "Time Taken (s)": round(elapsed, 2),
                "Overview Excel": Path(overview_path).name,
                "Overview Path": str(overview_path),
            })

            print(f"{Fore.GREEN}{Style.BRIGHT}Workflow complete for: '{query}'")
            print(f"{Fore.LIGHTBLACK_EX}Path: {vdb.query_storage}")
        except Exception as e:
            print(f"{Fore.RED}   [!] Query '{query}' failed after the attribute search "
                  f"({type(e).__name__}: {e}) — skipped, continuing with the next query.")
            not_added.append(_skip_row("report", e, title=query))
            continue

    if not_added:
        log_path = _append_skiplog(Path(vdb.results) / QUERY_SKIPPED_LOG, not_added)
        print(f"{Fore.YELLOW}{Style.BRIGHT}⚠️ {len(not_added)} attribute(s)/query(ies) were skipped"
              + (f" — see {log_path}" if log_path else ""))

    print(f"\n{Fore.MAGENTA}{Style.BRIGHT}--- ALL TASKS FINISHED ---")
    return not_added


def process_attribute_query(query, attr, excel_ref, bin_path, vdb, top_k: int = 50):
    """Search one attribute's index and write its result Excel.

    Returns None on success, or a short reason string when the attribute was
    skipped (no index, index/Excel mismatch) so the caller can record it."""
    import faiss

    if not bin_path.exists() or bin_path.stat().st_size == 0:
        print(f"{Fore.RED}   [!] Warning: No Vector DB for '{attr}'. Skipping.")
        return "no vector index built for this attribute"

    print(f"{Fore.LIGHTBLUE_EX}   - Processing attribute: {attr}")
    index = faiss.read_index(str(bin_path))
    strings = extract_column(excel_ref, "Content")

    if len(strings) != index.ntotal:
        print(f"{Fore.RED}{Style.BRIGHT}   [!] Data matching error for {attr}")
        return (f"index/Excel mismatch: {index.ntotal} vectors vs {len(strings)} rows "
                f"(rebuild this attribute's index)")

    scores, ids = search_similar(bin_path, query, top_k=top_k)

    # Read the section Excel ONCE. This used to call get_column_value per
    # column per hit, i.e. a full re-read of the workbook for every single
    # result row (3 x top_k reads per attribute per query).
    ref_df = pd.read_excel(excel_ref, engine="openpyxl")

    def cell(column, row_idx):
        if column in ref_df.columns and 0 <= row_idx < len(ref_df):
            return ref_df[column].iloc[row_idx]
        return None

    result_data = []
    for s, i in zip(scores, ids):
        if i < 0:
            continue  # FAISS pads with -1 when top_k > indexed vectors
        result_data.append({
            "Original_UUID": cell("Original_UUID", i),
            "Title": cell("Title", i),
            "Filename": cell("Filename", i),
            "Content": strings[i] if i < len(strings) else '(newly added item)',
            "Cosine Similarity": s
        })

    df = pd.DataFrame(result_data)
    query_safe = query.replace(" ", "_")
    file_name = f"{attr}_query_{query_safe}_report.xlsx"

    # Attribute result Excels live in the per-query "Attribute_Query_Results"
    # sub-folder only -- kept out of the query folder root so they are not
    # scattered next to (or repeated alongside) the enriched overview.
    attr_dir = Path(vdb.query_attr_storage)
    attr_dir.mkdir(parents=True, exist_ok=True)
    save_path = sanitize_path_length(attr_dir / file_name)
    df.to_excel(save_path, index=False, engine="openpyxl")

# Which harvested JSON file holds each analysis source, and the folder attribute
# on Vec_DB_Manager the harvest step copies it into.
_ENRICH_SOURCES = {
    "abstract": ("_Abstract.json", "querry_storage_Abs_jsons"),
    "intro": ("_Intro.json", "querry_storage_Intro_jsons"),
    "rescon": ("_Results_Conclusion.json", "querry_storage_ResCon_jsons"),
}


def _remove_if_empty(folder) -> bool:
    """Delete ``folder`` when it exists and holds nothing. True if it was removed."""
    folder = Path(folder)
    try:
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
            return True
    except OSError:
        pass
    return False


def _enrich_keys_by_source(enrich_keys=None):
    """
    Group the requested attribute keys by the analysis JSON they come from.
    Defaults to the abstract attributes. Returns ``{source: [section_key, ...]}``
    holding only sources that actually have keys selected.
    """
    from alr.common.sections import ALR_SECTIONS, RAG_SOURCE_BY_KEY

    keys = list(enrich_keys) if enrich_keys is not None else [s.key for s in ALR_SECTIONS]
    grouped = {}
    for key in keys:
        source = RAG_SOURCE_BY_KEY.get(key)
        if source:
            grouped.setdefault(source, []).append(key)
    return grouped


def _default_enrich_sections():
    """The abstract sections, used as the default attribute set for enrich/log."""
    from alr.common.sections import ALR_SECTIONS
    return ALR_SECTIONS


def _embedding_model_label() -> str:
    """A human label for the embedding backend used by the vector search, so
    the query log records which model produced the matches."""
    try:
        from alr.common.llm_utils import get_embedding_backend, embedding_model_repo_id
        cfg = get_embedding_backend()
        if cfg.get("method") == "local":
            return f"local ({embedding_model_repo_id})"
        service = cfg.get("service") or "unknown"
        return f"api ({service})"
    except Exception:  # noqa: BLE001 - logging must never break the query
        return "unknown"


def _log_query_run(log_path, row: dict) -> None:
    """Append one row of query details to the rolling query-log Excel, creating
    it on first use. Never raises -- logging is best-effort."""
    try:
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.exists():
            try:
                existing = pd.read_excel(log_path)
            except Exception:
                existing = pd.DataFrame()
            df = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
        else:
            df = pd.DataFrame([row])
        df.to_excel(log_path, index=False, engine="openpyxl")
        print(f"{Fore.LIGHTBLACK_EX}   - Query logged to: {log_path}")
    except Exception as e:  # noqa: BLE001
        print(f"{Fore.RED}   [!] Could not write the query log: {e}")


# The search-root fallback lives in file_handlers so the storage->SQL sync
# resolves a document's analysis JSON exactly the way this enrichment does.
_index_jsons_under_root = index_jsons_under_root


def enrich_overview_with_abstracts(overview_path, json_folders, enrich_keys=None, search_root=None):
    """
    Add one column per selected analyzed attribute to the query overview Excel,
    reading each document's analysis JSONs by ``Original_UUID``.

    ``json_folders`` maps an analysis source ("abstract" / "intro" / "rescon") to
    the folder its harvested JSONs were copied into; a bare path is accepted as
    the abstract folder for backward compatibility. ``enrich_keys`` selects the
    attributes (any mix of abstract, Introduction and Results & Conclusion keys
    from ``sections.ALL_RAG_SECTIONS``); it defaults to the abstract attributes.

    The section key **is** the JSON field name, so the registry is the single
    source of truth for both the column header and the lookup.
    """
    print(f"{Fore.YELLOW}Updating Overview with analyzed attributes...")

    if not isinstance(json_folders, dict):  # legacy call: just the abstract folder
        json_folders = {"abstract": json_folders}

    grouped = _enrich_keys_by_source(enrich_keys)
    if not grouped:
        print(f"{Fore.YELLOW}   - No attributes selected; overview left unchanged.")
        return

    df = pd.read_excel(overview_path)
    for keys in grouped.values():
        for field in keys:
            if field not in df.columns:
                df[field] = None

    # Fallback index: for each source, the JSON filenames we may need that are
    # NOT already sitting in the provided folder are located once under
    # search_root. This lets data enrichment succeed even when the queried
    # space (e.g. a common/combined DB) holds no analysis JSONs of its own.
    uuids = [str(u) for u in df['Original_UUID'].tolist()]
    fallback_index = {}
    for source, keys in grouped.items():
        suffix, _ = _ENRICH_SOURCES[source]
        folder = json_folders.get(source)
        missing = []
        for uuid in uuids:
            name = f"{uuid}{suffix}"
            if not (folder and (Path(folder) / name).exists()):
                missing.append(name)
        if missing:
            fallback_index[source] = _index_jsons_under_root(missing, search_root)
        else:
            fallback_index[source] = {}

    updated_count = 0

    for index, row in df.iterrows():
        uuid = str(row['Original_UUID'])
        found_any = False

        for source, keys in grouped.items():
            suffix, _ = _ENRICH_SOURCES[source]
            folder = json_folders.get(source)
            json_file = Path(folder) / f"{uuid}{suffix}" if folder else None
            if json_file is None or not json_file.exists():
                # Not in the space's own folder -- try the search-root fallback.
                json_file = fallback_index.get(source, {}).get(f"{uuid}{suffix}")
            if not json_file or not Path(json_file).exists():
                continue
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception as e:
                print(f"{Fore.RED}   [!] Error reading {source} JSON for UUID {uuid}: {e}")
                continue

            for field in keys:
                # Get data; handle lists by joining them into a single string
                val = data.get(field, "")
                if isinstance(val, list):
                    val = "\n •  ".join(str(v) for v in val)
                df.at[index, field] = val
            found_any = True

        if found_any:
            updated_count += 1

    # Save the enriched dataframe back to the same path
    df.to_excel(overview_path, index=False, engine="openpyxl")
    print(f"{Fore.GREEN}   - Enrichment complete. {updated_count} rows updated "
          f"across {len(grouped)} analysis source(s).")
    return updated_count

def harvest_query_resources(overview_path, search_root, vdb, mf, enrich_keys=None):
    """
    Copy the analysis JSONs behind the matched documents next to the query report
    and merge the selected attributes into the overview Excel. Only the JSON kinds
    the selected attributes need are harvested (see :func:`_enrich_keys_by_source`).
    """
    df_overview = pd.read_excel(overview_path)

    org_uuids = df_overview['Original_UUID'].unique().tolist()
    grouped = _enrich_keys_by_source(enrich_keys)

    # print(f"{Fore.LIGHTBLACK_EX}     * Copying {len(unique_filenames)} PDFs...")
    # copy_matching_pdfs(unique_filenames, search_root, Path(vdb.querry_storage_pdfs))

    json_folders = {}
    for source in grouped:
        suffix, folder_attr = _ENRICH_SOURCES[source]
        dest = Path(getattr(vdb, folder_attr))
        wanted = [f"{uuid}{suffix}" for uuid in org_uuids]
        print(f"{Fore.LIGHTBLACK_EX}     * Copying {len(wanted)} {source} JSONs...")
        # copy_matching_jsons creates the destination itself; drop it again when
        # the search turned up nothing, so a query never leaves an empty folder.
        copy_matching_jsons(wanted, search_root, dest)
        if not _remove_if_empty(dest):
            json_folders[source] = dest

    # 3. New Step: Enrich Excel with data from those JSONs (search_root stays
    #    available as a fallback for any UUID the harvest copy missed).
    print(f"{Fore.CYAN} > [Step 4] Merging JSON metadata into Excel...")
    return enrich_overview_with_abstracts(overview_path, json_folders,
                                          enrich_keys=enrich_keys, search_root=search_root)



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



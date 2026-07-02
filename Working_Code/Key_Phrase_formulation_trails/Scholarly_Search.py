import pandas as pd
import os
from scholarly import scholarly ,ProxyGenerator
import time
from Excel_File_Keyword_Updation import*


# EXCEL_FILE_PATH ='GoogleScholar_Publications.xlsx'

COLUMNS = [
    'Occurrence', 
    'Search Phrase',
    'Publication Name',
    'Keywords in Title', 
    'Abstract',
    'Link', 
    'Organization', 
    'Publication Year', 
    'Authors'
]

# pg = ProxyGenerator()
# # success = pg.FreeProxies()
# scholarly.use_proxy(pg)

def scrape_scholar_data(search_query):
    """
    Searches Google Scholar for publications matching the combined keywords 
    and returns the data as a list of dictionaries.
    """
    
    print(f"Searching Google Scholar for: '{search_query}'")
    
    publications_data = [] # Stores the raw scraped data
    
    try:
        search_results = scholarly.search_pubs(search_query)
        MAX_RESULTS = 10
        
        for i, pub in enumerate(search_results):
            if i >= MAX_RESULTS:
                print(f"\nStopped after processing {MAX_RESULTS} results.")
                break
                
            try:
                # Safely extract the required information
                title = pub.get('bib', {}).get('title', 'N/A')
                authors = ', '.join(pub.get('bib', {}).get('author', ['N/A']))
                pub_year = pub.get('bib', {}).get('pub_year', 'N/A')
                venue = pub.get('bib', {}).get('venue', 'N/A')
                
                # Simplified link extraction
                link_pub = pub.get('eprint_url') or pub.get('pub_url') or pub.get('doi') or pub.get('url') or 'N/A'
                
                abstract = pub.get('bib', {}).get('abstract', 'N/A')
                # keywords_in_title=find_keywords_in_phrase(title,Total_keywords)

                # Append data directly with all required keys (including the fixes)
                publications_data.append({
                    'Occurrence': 1, # Default for new entry
                    'Search Phrase': search_query, # Pass the search phrase
                    'Publication Name': title,
                    'Keywords in Title': keywords_in_title,
                    'Abstract': abstract,
                    'Link': link_pub,
                    'Organization': venue,
                    'Publication Year': pub_year, 
                    'Authors': authors # Corrected spelling
                })
                print(f"  Extracted result {i+1}: {title}...")
                
            except Exception as e:
                print(f"  Error processing publication: {e}")
            
            # Sleep to avoid rate-limiting
            time.sleep(1)  
            
        # The logic below is now redundant because the data is collected correctly above
        # The final cleanup block from the original code has been removed.
        return publications_data # Return the collected list

    except Exception as e:
        print(f"An error occurred during scholarly search: {e}")
        print("You might be rate-limited by Google Scholar. Try again later or use an API.")
        return [] # Return empty list on fatal error


# Old Function
def save_to_excel(data, filename='GoogleScholar_Publications.xlsx'):
    """
    Converts the list of publication data into a pandas DataFrame and saves it to an Excel file.
    """
    if not data:
        print("No data to save.")
        return
        
    df = pd.DataFrame(data)
    df.to_excel(filename, index=False)
    print(f"\nSuccessfully saved {len(df)} publications to '{filename}'")


def aggregate_and_update_excel(new_publications,EXCEL_FILE_PATH):
    """
    Handles the core logic: loading existing data, performing deduplication,
    updating occurrence counts, appending new search phrases, and saving the file.
    """
    
    if os.path.exists(EXCEL_FILE_PATH):
        # Read the existing data
        print(f"Loading existing data from {EXCEL_FILE_PATH}...")
        try:
            existing_df = pd.read_excel(EXCEL_FILE_PATH, engine='openpyxl')
        except Exception as e:
            print(f"Error reading Excel file: {e}. Starting with an empty DataFrame.")
            existing_df = pd.DataFrame(columns=COLUMNS)
    else:
        # Initialize a new DataFrame if the file doesn't exist
        print(f"File not found. Creating new data structure at {EXCEL_FILE_PATH}...")
        existing_df = pd.DataFrame(columns=COLUMNS)

    
    # Convert existing DataFrame to a list of dicts for easier indexing/updates
    existing_records = existing_df.to_dict('records')
    
    # Process the new search results
    for new_pub in new_publications:
        pub_name = new_pub['Publication Name']
        pub_link = new_pub['Link']
        
        # Check if this publication already exists in the records
        found = False
        for i, existing_rec in enumerate(existing_records):
            
            # Use Publication Name as the unique identifier for matching
            if existing_rec['Publication Name'] == pub_name and existing_rec['Link']== pub_link :
                found = True
                
                # 1. Increment Occurrence
                existing_records[i]['Occurrence'] += 1
                
                # 2. Update Input Search Phrase Used (new line separated)
                existing_phrase_list = existing_rec['Search Phrase'].split('\n')
                new_phrase = new_pub['Search Phrase']
                
                if new_phrase not in existing_phrase_list:
                    # Append the new phrase on a new line
                    existing_records[i]['Search Phrase'] += '\n' + new_phrase
                
                print(f"UPDATED: '{pub_name}' (Occurrence: {existing_records[i]['Occurrence']})")
                break
        
        # If not found, add the new publication as a new record
        if not found:
            existing_records.append(new_pub)
            print(f"ADDED NEW: '{pub_name}'")

    # Convert the updated list of records back to a DataFrame
    final_df = pd.DataFrame(existing_records, columns=COLUMNS)
    
    # Save the final DataFrame to the Excel file
    try:
        final_df.to_excel(EXCEL_FILE_PATH, index=False, engine='openpyxl')
        print(f"\nSuccessfully saved/updated data to {EXCEL_FILE_PATH}")
    except Exception as e:
        print(f"\nFATAL ERROR: Could not write to Excel file. Check permissions or if the file is open. Error: {e}")

if __name__ == "__main__":
    
    # --- Main Execution ---
    search_results = scholarly.search_pubs("natural language processing")

    print(search_results)
    # 1. Define your keywords as a list of strings
    # keywords = ["machine learning for climate modeling",
    # "natural language processing", 
    # "sentiment analysis"
    # ]

    # for keyword in keywords:
        
    #     publication_results = scrape_scholar_data(keyword)

    #     aggregate_and_update_excel(publication_results) 

    # # 2. Scrape the data
    # publication_results = scrape_scholar_data(keywords)

    # # 3. Save the output to an Excel file
    # aggregate_and_update_excel(publication_results)
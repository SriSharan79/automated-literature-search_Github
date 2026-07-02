import requests
import time
import os
from dotenv import load_dotenv
from typing import List, Dict, Any

# Load environment variables (optional, for email)
load_dotenv()

def search_openalex_publications(keyword: str, max_results: int = 50, per_page: int = 50) -> List[Dict[str, Any]]:
    """
    Search OpenAlex publications using a keyword (searches title and abstract).
    
    Args:
        keyword: Search term (e.g., "machine learning")
        max_results: Maximum number of results to return
        per_page: Results per page (max 100)
    
    Returns:
        List of publication dictionaries with key metadata
    """
    BASE_URL = "https://api.openalex.org/"
    EMAIL = os.environ.get("EMAIL", "your.email@example.com")  # Replace with your email
    
    endpoint = "works"
    all_results = []
    
    # Initial search parameters
    params = {
        "filter": f'title.search:"{keyword}",abstract.search:"{keyword}"',
        "per_page": min(per_page, 100),
        "mailto": EMAIL
    }
    
    url = BASE_URL + endpoint
    
    while len(all_results) < max_results:
        print(f"Fetching page... ({len(all_results)}/{max_results})")
        
        response = requests.get(url, params=params)
        if response.status_code != 200:
            print(f"Error: {response.status_code} - {response.text}")
            break
            
        data = response.json()
        
        # Add current page results
        page_results = data["results"][:max_results - len(all_results)]
        all_results.extend(page_results)
        
        # Check if more pages
        if "next" not in data["meta"] or len(page_results) == 0:
            break
            
        # Update for next page
        url = data["meta"]["next"]
        params = {}  # Next URL already has parameters
        
        # Rate limiting (10 req/sec recommended)
        time.sleep(0.1)
    
    return all_results[:max_results]

def print_publication_summary(publications: List[Dict[str, Any]], top_n: int = 10):
    """Print formatted summary of top publications."""
    print(f"\n📚 Top {min(top_n, len(publications))} Publications:\n")
    print("-" * 80)
    
    for i, pub in enumerate(publications[:top_n], 1):
        title = pub.get("title", "No title")
        authors = ", ".join([a.get("author", {}).get("display_name", "Unknown") 
                           for a in pub.get("authorships", [])[:3]])
        year = pub.get("publication_year", "N/A")
        doi = pub.get("doi", "No DOI")
        cited_by = pub.get("cited_by_count", 0)
        
        print(f"{i:2d}. {title}")
        print(f"    Authors: {authors}{'...' if len(pub.get('authorships', [])) > 3 else ''}")
        print(f"    Year: {year} | Citations: {cited_by:,} | DOI: {doi}")
        print()

# Example usage - FIXED FUNCTION NAME
if __name__ == "__main__":
    # Search for publications
    keyword = "MBSE"  # Change this keyword
    results = search_openalex_publications(keyword, max_results=100)  # CORRECTED NAME
    
    if results:
        print(f"Found {len(results)} publications for '{keyword}'\n")
        print_publication_summary(results)
        
        # Save to CSV (optional)
        import csv
        with open(f"{keyword.replace(' ', '_')}_publications.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["title", "doi", "publication_year", "cited_by_count"])
            writer.writeheader()
            for pub in results:
                writer.writerow({
                    "title": pub.get("title", ""),
                    "doi": pub.get("doi", ""),
                    "publication_year": pub.get("publication_year", ""),
                    "cited_by_count": pub.get("cited_by_count", 0)
                })
        print("✅ Results saved to CSV file!")
    else:
        print("No results found.")

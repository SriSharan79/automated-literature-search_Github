"""
Publication-search backends for the collection pipeline.

Primary backend: **OpenAlex** (https://api.openalex.org) - an official, free
REST API with title/abstract/DOI/year/venue/author metadata and a generous
rate limit. No API key needed; setting the ALR_OPENALEX_MAILTO environment
variable (any contact email) routes requests into OpenAlex's faster "polite
pool".

Fallback backend: **Google Scholar** via the `scholarly` package. Scholar has
no official API, so `scholarly` scrapes it - and after modest volumes Google
starts answering with CAPTCHA pages. We do not try to bypass those: when a
block is detected the Scholar backend is put on a cooldown
(SCHOLAR_COOLDOWN_SECONDS) and collection continues on the OpenAlex API, so a
run always completes with whatever each backend could legitimately provide.

Every returned row carries a 'Source' column recording which backend actually
produced it.
"""

import os
import time
import traceback
from typing import List

import pandas as pd
import requests
from colorama import Fore, Style

OPENALEX_BASE_URL = "https://api.openalex.org/works"
# Optional contact email for OpenAlex's polite pool (faster, more reliable).
OPENALEX_MAILTO = os.environ.get("ALR_OPENALEX_MAILTO", "")

# How long the Scholar backend stays disabled after Google blocks it.
SCHOLAR_COOLDOWN_SECONDS = 15 * 60
_scholar_blocked_until = 0.0


def find_keywords_in_phrase(text: str, keywords: List[str]) -> str:
    if pd.isna(text):
        return ""  # Handle NaN/empty cells

    # Ensure the text is a string and convert to lowercase for case-insensitive matching
    text_lower = str(text).lower()

    found_keywords = []
    for keyword in keywords:
        # Check if the keyword is in the text (case-insensitive)
        if keyword.lower() in text_lower:
            found_keywords.append(keyword)

    # Join the found keywords with a comma and space (e.g., "apple, banana")
    return ", ".join(found_keywords)


# ---------------------------------------------------------------------------
# Google Scholar availability tracking (CAPTCHA / block handling)
# ---------------------------------------------------------------------------
def scholar_available() -> bool:
    """True when the Scholar backend is not sitting out a block cooldown."""
    return time.time() >= _scholar_blocked_until


def mark_scholar_blocked(reason: str) -> None:
    """Put Scholar on cooldown after Google blocked the scraper."""
    global _scholar_blocked_until
    _scholar_blocked_until = time.time() + SCHOLAR_COOLDOWN_SECONDS
    print(Fore.YELLOW
          + f"⚠️ Google Scholar blocked the scraper ({reason}). "
          + f"Scholar is disabled for {SCHOLAR_COOLDOWN_SECONDS // 60} minutes; "
          + "collection continues via the OpenAlex API."
          + Style.RESET_ALL)


# ---------------------------------------------------------------------------
# OpenAlex backend
# ---------------------------------------------------------------------------
def _reconstruct_openalex_abstract(inverted_index) -> str:
    """
    OpenAlex stores abstracts as an inverted index {word: [positions]};
    rebuild the plain text from it. Returns 'N/A' when absent/unreadable.
    """
    if not isinstance(inverted_index, dict) or not inverted_index:
        return "N/A"
    try:
        positions = []
        for word, idxs in inverted_index.items():
            for idx in idxs:
                positions.append((idx, word))
        positions.sort()
        return " ".join(word for _, word in positions) or "N/A"
    except Exception:
        return "N/A"


def _openalex_get(params: dict, timeout: int = 30, max_retries: int = 3) -> dict:
    """GET from OpenAlex with a native timeout and bounded retry on 429/5xx."""
    if OPENALEX_MAILTO:
        params = {**params, "mailto": OPENALEX_MAILTO}
    delay = 2.0
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(OPENALEX_BASE_URL, params=params, timeout=(10, timeout))
            if resp.status_code in (429, 500, 502, 503, 504):
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        pass
                last_exc = requests.HTTPError(f"OpenAlex: HTTP {resp.status_code}", response=resp)
            else:
                resp.raise_for_status()
                return resp.json()
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
        if attempt < max_retries:
            print(Fore.YELLOW
                  + f"⚠️ OpenAlex request failed ({last_exc}); retrying in {delay:.0f}s "
                  + f"(attempt {attempt}/{max_retries})..." + Style.RESET_ALL)
            time.sleep(delay)
            delay *= 2
    raise last_exc


def search_openalex(search_query: str, Num_Results: int, Total_keywords: List[str]) -> list:
    """
    Search the OpenAlex API and return rows in the same shape as the Scholar
    scraper (the publications-Excel COLUMNS), plus 'Source': 'OpenAlex'.
    """
    print(f"Searching OpenAlex for: '{search_query}'")
    per_page = max(1, min(int(Num_Results), 200))
    data = _openalex_get({"search": search_query, "per-page": per_page})

    publications_data = []
    for work in data.get("results", [])[:per_page]:
        try:
            title = work.get("display_name") or "N/A"
            authorships = work.get("authorships") or []
            authors = ", ".join(
                a.get("author", {}).get("display_name", "")
                for a in authorships if a.get("author", {}).get("display_name")
            ) or "N/A"
            primary = work.get("primary_location") or {}
            source = primary.get("source") or {}
            venue = source.get("display_name") or "N/A"
            link = (primary.get("landing_page_url")
                    or work.get("doi")
                    or work.get("id")
                    or "N/A")
            abstract = _reconstruct_openalex_abstract(work.get("abstract_inverted_index"))

            publications_data.append({
                'Occurrence': 1,
                'Search Phrase': search_query,
                'Publication Name': title,
                'Keywords in Title': find_keywords_in_phrase(title, Total_keywords),
                'Abstract': abstract,
                'Link': link,
                'Organization': venue,
                'Publication Year': work.get("publication_year") or "N/A",
                'Authors': authors,
                'Source': 'OpenAlex',
            })
            print(f"  Extracted result {len(publications_data)}: {title}...")
        except Exception as e:
            print(f"  Error processing OpenAlex work: {e}")
            traceback.print_exc()

    return publications_data


# ---------------------------------------------------------------------------
# Google Scholar backend (scraping via `scholarly` - fragile by nature)
# ---------------------------------------------------------------------------
def scrape_scholar_data(search_query, Num_Results, Total_keywords):
    """
    Searches Google Scholar for publications matching the combined keywords
    and returns the data as a list of dictionaries (plus 'Source' column).

    When Google blocks the scraper (CAPTCHA / MaxTriesExceededException) the
    backend is put on cooldown via mark_scholar_blocked() and whatever rows
    were collected before the block are returned instead of being discarded.
    """
    try:
        # Lazy import: scholarly is slow to import and only needed here.
        from scholarly import scholarly
        try:
            from scholarly import MaxTriesExceededException
        except ImportError:  # older scholarly versions
            class MaxTriesExceededException(Exception):
                pass
    except ImportError as e:
        print(Fore.RED + f"❌ scholarly is not installed ({e}); Scholar backend unavailable." + Style.RESET_ALL)
        return []

    print(f"Searching Google Scholar for: '{search_query}'")

    publications_data = []  # Stores the raw scraped data

    try:
        search_results = scholarly.search_pubs(search_query)

        MAX_RESULTS = Num_Results

        for i, pub in enumerate(search_results):
            time.sleep(10)
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
                keywords_in_title = find_keywords_in_phrase(title, Total_keywords)

                # Append data directly with all required keys (including the fixes)
                publications_data.append({
                    'Occurrence': 1,  # Default for new entry
                    'Search Phrase': search_query,  # Pass the search phrase
                    'Publication Name': title,
                    'Keywords in Title': keywords_in_title,
                    'Abstract': abstract,
                    'Link': link_pub,
                    'Organization': venue,
                    'Publication Year': pub_year,
                    'Authors': authors,
                    'Source': 'Google Scholar',
                })
                print(f"  Extracted result {i+1}: {title}...")

            except Exception as e:
                print(f"  Error processing publication: {e}")
                traceback.print_exc()

            # Sleep to avoid rate-limiting
            time.sleep(1)

    except MaxTriesExceededException as e:
        # Google is serving CAPTCHAs / refusing the scraper: cool down and
        # keep whatever was collected before the block.
        mark_scholar_blocked(str(e) or "MaxTriesExceededException")
    except Exception as e:
        print(Fore.RED + f"❌ Google Scholar search failed: {e}" + Style.RESET_ALL)
        traceback.print_exc()

    return publications_data  # Return the collected list


# ---------------------------------------------------------------------------
# Backend orchestrator
# ---------------------------------------------------------------------------
def collect_publications(search_query, Num_Results, Total_keywords, backend: str = "auto") -> list:
    """
    Collect publications for one search phrase.

      backend='auto'     -> OpenAlex first; Google Scholar only when OpenAlex
                            returned nothing (and Scholar isn't on cooldown).
      backend='openalex' -> OpenAlex only.
      backend='scholar'  -> Google Scholar preferred; automatically fails over
                            to OpenAlex when Scholar is blocked/empty.

    Always returns a list (possibly empty); each row's 'Source' records the
    backend that produced it.
    """
    backend = (backend or "auto").strip().lower()

    if backend == "scholar":
        if scholar_available():
            rows = scrape_scholar_data(search_query, Num_Results, Total_keywords)
            if rows:
                return rows
            print(Fore.YELLOW + "⚠️ Scholar returned nothing; failing over to OpenAlex." + Style.RESET_ALL)
        else:
            wait_min = max(0.0, (_scholar_blocked_until - time.time()) / 60)
            print(Fore.YELLOW
                  + f"⚠️ Scholar is on block-cooldown for another {wait_min:.0f} min; using OpenAlex."
                  + Style.RESET_ALL)
        try:
            return search_openalex(search_query, Num_Results, Total_keywords)
        except Exception as e:
            print(Fore.RED + f"❌ OpenAlex failover also failed: {e}" + Style.RESET_ALL)
            return []

    # 'auto' and 'openalex': OpenAlex is the primary backend.
    rows = []
    try:
        rows = search_openalex(search_query, Num_Results, Total_keywords)
    except Exception as e:
        print(Fore.RED + f"❌ OpenAlex search failed: {e}" + Style.RESET_ALL)

    if rows or backend == "openalex":
        return rows

    if not scholar_available():
        wait_min = max(0.0, (_scholar_blocked_until - time.time()) / 60)
        print(Fore.YELLOW
              + f"⚠️ No OpenAlex results and Scholar is on block-cooldown "
              + f"for another {wait_min:.0f} min; returning what we have."
              + Style.RESET_ALL)
        return rows

    print("No OpenAlex results; trying Google Scholar as fallback.")
    return scrape_scholar_data(search_query, Num_Results, Total_keywords)

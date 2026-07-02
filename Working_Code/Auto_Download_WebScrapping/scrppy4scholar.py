import requests
from bs4 import BeautifulSoup
import time
import random
from urllib.parse import quote_plus

search_query = "python web scraping"
encoded_query = quote_plus(search_query)
url = f"https://scholar.google.com/scholar?q={encoded_query}"

headers_list = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
]
headers = {"User-Agent": random.choice(headers_list)}

session = requests.Session()
session.headers.update(headers)

try:
    time.sleep(random.uniform(5, 10))  # Initial delay
    response = session.get(url, timeout=15)
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        soup = BeautifulSoup(response.text, 'html.parser')
        titles = [el.get_text().strip() for el in soup.select('.gs_rt')][:5]
        for title in titles:
            print(f"Title: {title}")
    elif response.status_code == 429:
        print("Blocked. Wait 30+ mins or use VPN/API.")
    else:
        print("Unexpected error.")
except Exception as e:
    print(f"Error: {e}")

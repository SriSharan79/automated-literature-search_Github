import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import fitz  # PyMuPDF
import time
import random

import re
from bs4 import BeautifulSoup
import requests
import os

def extract_real_pdf_url(html_text):
    # 1) Try iframe src
    soup = BeautifulSoup(html_text, "html.parser")
    iframe = soup.find("iframe")
    if iframe and iframe.get("src") and ".pdf" in iframe["src"]:
        return iframe["src"]

    # 2) Fallback: regex search for any .pdf link
    m = re.search(r'https://[^"\']+\.pdf[^"\']*', html_text)
    if m:
        return m.group(0)

    return None


def download_pdf(url, file_path):
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        session = requests.Session()
        # 1) Visit paper page (without .pdf) to set cookies
        paper_url = url.replace(".pdf", "")
        session.get("https://ieeexplore.ieee.org/", timeout=10)
        session.get(paper_url, timeout=15)

        headers = {
            "User-Agent": "Mozilla/5.0 ...",
            "Referer": paper_url,
            "Accept": "text/html,application/pdf,*/*;q=0.8",
        }

        # 2) First request to the .pdf URL
        resp = session.get(url, headers=headers, timeout=30)
        content_type = resp.headers.get("Content-Type", "").lower()

        # If HTML, extract real PDF URL
        if "html" in content_type:
            real_url = extract_real_pdf_url(resp.text)
            if not real_url:
                # Save HTML for inspection
                with open(file_path + ".html", "w", encoding="utf-8") as f:
                    f.write(resp.text)
                return "Failed", "HTML wrapper received; no PDF URL found"
            # Some src are relative; make absolute
            if real_url.startswith("/"):
                real_url = "https://ieeexplore.ieee.org" + real_url

            print(f"🔁 Found real PDF URL: {real_url}")
            resp = session.get(real_url, headers=headers, stream=True, timeout=60)

        resp.raise_for_status()

        if "pdf" not in resp.headers.get("Content-Type", "").lower():
            return "Failed", f"Not a PDF: {resp.headers.get('Content-Type')}"

        with open(file_path, "wb") as f:
            for chunk in resp.iter_content(8192):
                if chunk:
                    f.write(chunk)

        return "Downloaded", "Valid PDF saved"

    except Exception as e:
        return "Failed", str(e)
    finally:
        if "session" in locals():
            session.close()


if __name__ == "__main__":
    
    # Example 1: Download your specific IEEE paper
    ieee_url = "https://ieeexplore.ieee.org/iel7/7731016/7753113/07753138.pdf"
    output_dir = "Publication_Files/IEEE_Papers"
    filename = "test_papaer.pdf"
    file_path = os.path.join(output_dir, filename)
    


    status, msg = download_pdf(ieee_url, file_path)
    print(status, msg)

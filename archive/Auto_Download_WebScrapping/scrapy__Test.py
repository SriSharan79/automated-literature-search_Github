#!/usr/bin/env python3
"""
Fixed: Auto-downloads IEEE PDF to custom folder (no manual save).
Firefox opens PDF → auto-saves to 'downloads/' after load.
"""

import os
import time
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

def download_pdf_auto(url, download_dir="downloads", timeout=60):
    options = Options()
    
    # COMPLETE Firefox download prefs for PDF auto-save
    prefs = {
        "browser.download.folderList": 2,                    # custom dir
        "browser.download.manager.showWhenStarting": False,  # no popup
        "browser.download.dir": os.path.abspath(download_dir),
        "browser.download.useDownloadDir": True,             # use the dir!
        "browser.helperApps.neverAsk.saveToDisk": "application/pdf,application/octet-stream,text/plain",
        "pdfjs.disabled": True,                              # disable viewer
        "browser.download.panel.shown": False,
        "browser.download.manager.focusWhenStarting": False,
        "browser.download.manager.useWindow": False,
        "browser.download.manager.showAlertOnComplete": False,
        "browser.download.manager.closeWhenDone": True,
    }
    
    # Apply ALL prefs
    for key, value in prefs.items():
        options.set_preference(key, value)
    
    options.add_argument("--headless=False")  # set True to hide browser
    
    driver = webdriver.Firefox(options=options)
    
    try:
        print(f"🦊 Loading PDF: {url}")
        driver.get(url)
        
        # Wait for page/PDF load
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        
        # Extra wait for PDF content/render
        time.sleep(5)
        
        print("⏳ Waiting for auto-download...")
        
        # Monitor download folder for new PDF
        files_before = set(f for f in os.listdir(download_dir) if f.lower().endswith('.pdf'))
        start_time = time.time()
        
        while time.time() - start_time < 30:  # 30s timeout
            files_now = set(f for f in os.listdir(download_dir) if f.lower().endswith('.pdf'))
            new_files = files_now - files_before
            
            if new_files:
                latest = max(new_files, key=lambda f: os.path.getctime(os.path.join(download_dir, f)))
                full_path = os.path.abspath(os.path.join(download_dir, latest))
                
                # Verify it's a real PDF
                if os.path.getsize(full_path) > 10000:  # >10KB
                    print(f"✅ PDF downloaded: {full_path}")
                    print(f"   Size: {os.path.getsize(full_path):,} bytes")
                    return full_path
            
            time.sleep(1)
        
        print("❌ Timeout: No PDF downloaded")
        return None
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return None
        
    finally:
        driver.quit()

if __name__ == "__main__":
    ieee_url = "https://ieeexplore.ieee.org/iel5/5326/4359283/05722047.pdf"
    os.makedirs("downloads", exist_ok=True)
    
    result = download_pdf_auto(ieee_url)

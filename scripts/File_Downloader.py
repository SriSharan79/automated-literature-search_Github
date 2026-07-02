
from pathlib import Path
import os
import shutil
import pandas as pd
import requests
from PyPDF2 import PdfReader

home_folder = Path.home()
ALR_main_folder= home_folder/ "Automated Literature Review"
ALR_main_folder.mkdir(parents=True, exist_ok=True)

# -----------------------------
# PDF validation helpers
# -----------------------------
def _looks_like_pdf(file_path: str) -> bool:
    """Quick magic-header check for '%PDF'."""
    try:
        with open(file_path, "rb") as f:
            head = f.read(5)
        return head == b"%PDF-"
    except Exception:
        return False


def _pdf_text_is_readable(file_path: str, max_pages: int = 2):
    """Best-effort check whether text can be extracted from the PDF."""
    if PdfReader is None:
        return False, "PyPDF2 not available to validate text-extraction."

    try:
        reader = PdfReader(file_path)

        if getattr(reader, "is_encrypted", False):
            try:
                # Attempt empty password (some PDFs are 'encrypted' but unlock with empty string)
                reader.decrypt("")
            except Exception:
                return False, "PDF appears to be encrypted; cannot extract text."

        pages_to_check = min(max_pages, len(reader.pages))
        extracted_any = False
        total_chars = 0

        for i in range(pages_to_check):
            try:
                t = reader.pages[i].extract_text() or ""
            except Exception:
                t = ""
            t = t.strip()
            if t:
                extracted_any = True
                total_chars += len(t)

        if extracted_any and total_chars >= 20:
            return True, "Text extraction OK."
        return False, "No extractable text detected (might be scanned/image-only PDF)."

    except Exception as e:
        return False, f"PDF text check failed: {e}"


# -----------------------------
# Download helpers
# -----------------------------
def _ensure_parent_dir(file_path: str) -> None:
    os.makedirs(os.path.dirname(file_path), exist_ok=True)


def _make_requests_session() -> tuple[requests.Session, dict]:
    session = requests.Session()
    headers = {
        # Some publishers block requests without a UA
        "User-Agent": "Mozilla/5.0 (compatible; PDFDownloader/1.0)"
    }
    return session, headers


def _stream_to_file(response: requests.Response, file_path: str, chunk_size: int = 8192) -> None:
    with open(file_path, "wb") as pdf_file:
        for chunk in response.iter_content(chunk_size=chunk_size):
            if chunk:  # avoid keep-alive chunks
                pdf_file.write(chunk)


def _validate_downloaded_pdf(file_path: str):
    # --- Validate the downloaded file is really a PDF ---
    if not _looks_like_pdf(file_path):
        print(
            f"❌ Downloaded file does not look like a valid PDF (missing %PDF header): {os.path.abspath(file_path)}"
        )
        try:
            os.remove(file_path)
        except Exception:
            pass
        return "Failed", "Downloaded file is not a valid PDF."

    # --- Check whether text can be read (best-effort) ---
    text_ok, text_msg = _pdf_text_is_readable(file_path)
    if not text_ok:
        print(f"⚠️  PDF downloaded but text may not be readable: {text_msg}")
        return "Downloaded", text_msg

    print("✅ PDF validation passed (PDF format + text readable).")
    return "Downloaded", "Downloaded"


def download_pdf(url, file_path):
    """
    Downloads a PDF file from a given URL and saves it to the specified file path.
    After download, validates that the file is a real PDF and checks whether text is readable.
    """
    try:
        _ensure_parent_dir(file_path)

        session, headers = _make_requests_session()
        response = session.get(
            url,
            stream=True,
            timeout=30,
            allow_redirects=True,
            headers=headers,
        )
        response.raise_for_status()

        # Check if the content type is a PDF (optional)
        content_type = response.headers.get("Content-Type", "")
        if "application/pdf" not in content_type:
            print(f"Warning: URL may not point to a PDF. Content-Type is '{content_type}'")

        _stream_to_file(response, file_path)

        print(f"✅ Download complete! File saved as: {os.path.abspath(file_path)}")

        return _validate_downloaded_pdf(file_path)

    except requests.exceptions.RequestException as e:
        print(f"❌ An error occurred during download: {e}")
        return "Failed", f"Download Error: {e}"
    except Exception as e:
        print(f"❌ An unexpected error occurred: {e}")
        return "Failed", f"Error: {e}"


# -----------------------------
# NEW: classification + moving
# -----------------------------
def _ensure_classification_folders(prefix: Path) -> tuple[Path, Path]:
    readable_dir = prefix / "Text_Readable"
    not_readable_dir = prefix / "Text_Not_Readable"
    readable_dir.mkdir(parents=True, exist_ok=True)
    not_readable_dir.mkdir(parents=True, exist_ok=True)
    return readable_dir, not_readable_dir


def _infer_text_readable_from_download(download_result: str, dwld_error: str) -> bool:
    """
    Preserve existing return values from download_pdf() and infer readability:
    - ("Downloaded", "Downloaded") => readable
    - ("Downloaded", <warning msg>) => not readable
    - everything else => not readable/unknown
    """
    return (download_result == "Downloaded") and (dwld_error == "Downloaded")


def _detect_text_readable_from_file(file_path: str) -> tuple[bool, str]:
    """
    For 'Already Exists' cases (or any existing file), detect readability directly.
    """
    if not os.path.exists(file_path):
        return False, "File not found on disk."

    if not _looks_like_pdf(file_path):
        return False, "Missing %PDF header; file does not look like a valid PDF."

    return _pdf_text_is_readable(file_path)


def _move_to_classified_folder(file_path_full: str, prefix: Path, is_text_readable: bool) -> str:
    """
    Move the file into prefix/Text_Readable or prefix/Text_Not_Readable.
    Returns the new absolute path (as a string) to store in the log.
    """
    readable_dir, not_readable_dir = _ensure_classification_folders(prefix)

    target_dir = readable_dir if is_text_readable else not_readable_dir
    target_path = target_dir / Path(file_path_full).name

    # If it's already in the right spot, do nothing
    try:
        if Path(file_path_full).resolve() == target_path.resolve():
            return str(target_path)
    except Exception:
        pass

    # Avoid overwrite collisions
    if target_path.exists():
        base = target_path.stem
        suffix = target_path.suffix
        k = 1
        while True:
            candidate = target_dir / f"{base}__dup{k}{suffix}"
            if not candidate.exists():
                target_path = candidate
                break
            k += 1

    os.makedirs(target_dir, exist_ok=True)
    shutil.move(file_path_full, str(target_path))
    return str(target_path)


# -----------------------------
# Excel + logging helpers
# -----------------------------
def _get_excel_prefix(excel_path: str) -> str:
    base_name = os.path.basename(excel_path)
    return base_name.split("_Publications")[0]


def _prepare_storage_folders(storage_path: str, ex_prefix: str) -> tuple[Path, Path, str]:
    storage_folder = Path(storage_path)
    storage_folder.mkdir(parents=True, exist_ok=True)
    
    pdf_folder=None
    prefix=None
    
    if ALR_main_folder==storage_folder:
        pdf_folder = storage_folder / "Downloaded Pdfs"
        pdf_folder.mkdir(parents=True, exist_ok=True)
        prefix = pdf_folder / ex_prefix
    else:
        pdf_folder=storage_folder
        ending=ex_prefix+'_Pdfs'
        prefix = pdf_folder / ending
        
    os.makedirs(prefix, exist_ok=True)



    download_log_path = os.path.join(pdf_folder, f"{ex_prefix}_download_log.xlsx")
    return storage_folder, prefix, download_log_path


def _create_new_log(download_log_path: str) -> pd.DataFrame:
    print(f"⚠️  Download log file not found at {download_log_path}")
    print("Creating new download log file...")

    log_df = pd.DataFrame(
        columns=[
            "Publication Name",
            "Link",
            "Publication Year",
            "Authors",
            "Pub_Name",
            "Downloaded",
            "Download Error",
            "First_Author",
            "File_Name",
            "File_Path",
        ]
    )
    log_df.to_excel(download_log_path, index=False)
    print(f"✅ New download log file created at: {os.path.abspath(download_log_path)}")
    return log_df


def _read_existing_log(download_log_path: str) -> pd.DataFrame:
    log_df = pd.read_excel(download_log_path)
    print(f"✅ Reading existing download log from: {os.path.abspath(download_log_path)}")
    return log_df


def _ensure_log_columns(log_df: pd.DataFrame) -> pd.DataFrame:
    for col in ["Pub_Name", "Downloaded", "Download Error", "First_Author", "File_Name", "File_Path"]:
        if col not in log_df.columns:
            log_df[col] = ""
    return log_df


def _row_already_processed(log_df: pd.DataFrame, idx: int, pub_name: str) -> bool:
    if len(log_df) > idx and pd.notna(log_df.loc[idx, "File_Path"]) and log_df.loc[idx, "File_Path"] != "":
        print(f"⏭️  Already processed: {pub_name}")
        return True
    return False


def _make_pub_short_name(pub_name: str) -> str:
    words = str(pub_name).replace("_", " ").split()
    return "".join(word[0].upper() for word in words if word)


def _first_author_from_authors(authors: str) -> str:
    return str(authors).split(",")[0].strip()


def _normalise_year(year_value) -> str:
    if pd.notna(year_value) and isinstance(year_value, (int, float)):
        return str(int(year_value))
    return str(year_value)


def _build_file_name(year: str, first_author: str, pub_short: str) -> str:
    return f"{year}_{first_author}_{pub_short}.pdf"


def _file_already_exists(file_path_full: str, log_df: pd.DataFrame, prefix: Path, file_name_str: str) -> bool:
    if os.path.exists(file_path_full) or (file_path_full in log_df["File_Path"].values):
        print(f"⏭️  File already exists in folder '{prefix}' or recorded in log: {file_name_str}")
        return True
    return False


def _append_or_update_log(
    log_df: pd.DataFrame,
    idx: int,
    pub_name: str,
    pub_link: str,
    year: str,
    authors: str,
    download_result: str,
    dwld_error: str,
    first_author: str,
    file_name_str: str,
    file_path_full: str,
) -> pd.DataFrame:
    if len(log_df) <= idx:
        log_df = pd.concat(
            [
                log_df,
                pd.DataFrame(
                    [
                        {
                            "Publication Name": pub_name,
                            "Link": pub_link,
                            "Publication Year": year,
                            "Authors": authors,
                            "Downloaded": download_result,
                            "Download Error": dwld_error,
                            "First_Author": first_author,
                            "File_Name": file_name_str,
                            "File_Path": file_path_full,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
    else:
        log_df.loc[idx, "Publication Name"] = pub_name
        log_df.loc[idx, "Link"] = pub_link
        log_df.loc[idx, "Publication Year"] = year
        log_df.loc[idx, "Authors"] = authors
        log_df.loc[idx, "Downloaded"] = download_result
        log_df.loc[idx, "Download Error"] = dwld_error
        log_df.loc[idx, "First_Author"] = first_author
        log_df.loc[idx, "File_Name"] = file_name_str
        log_df.loc[idx, "File_Path"] = file_path_full

    return log_df


def _save_log(log_df: pd.DataFrame, download_log_path: str, idx: int) -> None:
    log_df.to_excel(download_log_path, index=False)
    print(f"✅ Download log updated for row {idx + 1}")


# -----------------------------
# Main function (logic preserved, adds sorting + log path update)
# -----------------------------
def download_pubs_from_excel(file_path, Storage_path=ALR_main_folder):
    # Read the excel file
    df = pd.read_excel(file_path)

    # Prepare folders
    ex_prefix = _get_excel_prefix(file_path)
    storage_folder, prefix, download_log_path = _prepare_storage_folders(Storage_path, ex_prefix)

    # Ensure classification folders exist upfront
    _ensure_classification_folders(prefix)

    # Check if download log file exists, if not create it
    if not os.path.exists(download_log_path):
        log_df = _create_new_log(download_log_path)
    else:
        log_df = _read_existing_log(download_log_path)

    # Ensure columns exist
    log_df = _ensure_log_columns(log_df)

    for idx, row in df.iterrows():
        pub_name = row["Publication Name"]

        # Check if this row already exists in log
        if _row_already_processed(log_df, idx, pub_name):
            continue

        pub_name_short = _make_pub_short_name(pub_name)
        authors = str(row["Authors"])
        first_author = _first_author_from_authors(authors)
        year = _normalise_year(row["Publication Year"])
        pub_link = str(row["Link"])

        file_name_str = _build_file_name(year, first_author, pub_name_short)
        file_path_full = os.path.join(prefix, file_name_str)

        # Check if file already exists on disk OR in the File_Path column in log
        if _file_already_exists(file_path_full, log_df, prefix, file_name_str):
            download_result = "Already Exists"
            dwld_Error = "No Error"

            # If it exists but is not yet in the classified folders, classify + move it now
            if os.path.exists(file_path_full):
                text_ok, text_msg = _detect_text_readable_from_file(file_path_full)
                # Keep "No Error" for the existing logic, but if we detect an issue, store it
                if not text_ok:
                    dwld_Error = text_msg

                new_path = _move_to_classified_folder(file_path_full, prefix, text_ok)
                print(f"✅ Sorted existing file into: {os.path.abspath(new_path)}")
                file_path_full = new_path

        else:
            # Download the PDF (and validate it)
            download_result, dwld_Error = download_pdf(pub_link, file_path_full)

            # If download succeeded, sort into readable / not readable folders and update log path
            if download_result == "Downloaded" and os.path.exists(file_path_full):
                text_ok = _infer_text_readable_from_download(download_result, dwld_Error)
                new_path = _move_to_classified_folder(file_path_full, prefix, text_ok)
                print(f"✅ Sorted downloaded file into: {os.path.abspath(new_path)}")
                file_path_full = new_path

        # Add or update row in log_df (note: file_path_full now points to the sorted location)
        log_df = _append_or_update_log(
            log_df=log_df,
            idx=idx,
            pub_name=pub_name,
            pub_link=pub_link,
            year=year,
            authors=authors,
            download_result=download_result,
            dwld_error=dwld_Error,
            first_author=first_author,
            file_name_str=file_name_str,
            file_path_full=file_path_full,
        )

        print(f"File Name of {pub_name}: {file_name_str}")

        # Save the updated download log file after EVERY row
        _save_log(log_df, download_log_path, idx)

    print(f"✅ Download process completed: {os.path.abspath(download_log_path)}")
    return log_df


# --- Run the function ---
if __name__ == "__main__":
    excel_path= '/remotedata/U/DLR+kata_du/ALR DATA/AI_RM/AI_REQ_Publications.xlsx'
    storage_path='/remotedata/U/DLR+kata_du/ALR DATA/AI_RM'
    download_pubs_from_excel(excel_path,storage_path)

import pandas as pd
import os
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path

# ---------------------------------------------------------------------------
# Cached workbook reads
# ---------------------------------------------------------------------------
# The per-document pipeline asks the SAME small workbooks (the registry, the
# per-target analysis logs) dozens of times per PDF -- every lookup used to
# re-parse the whole file, which is O(rows) each and turns a 1000-document run
# into tens of thousands of full parses.
#
# The cache is keyed on the file's identity **and** its modification time and
# size, so a workbook written by anything at all -- another pass, the Review
# tool, the user in Excel, a different process -- is re-read on the next
# access. That makes it transparent: no session to open, no way to serve stale
# data, and nothing to unwind when a pass fails half way.
_CACHE = {}
_CACHE_LOCK = threading.RLock()
_MAX_CACHED = 64


def _stamp(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def read_excel_cached(path, sheet_name=0, **kwargs):
    """
    Read a workbook, reusing the last parse while the file is unchanged.

    The returned DataFrame is the cached object: **callers must not mutate it**
    (use ``.copy()`` first, or :func:`write_excel_cached`, which does).
    Any keyword beyond ``sheet_name`` bypasses the cache -- those reads are
    rare and not worth a key per option combination.
    """
    if kwargs:
        return pd.read_excel(path, sheet_name=sheet_name, **kwargs)

    key = (str(Path(path)), sheet_name)

    with _CACHE_LOCK:
        # Inside a deferred session the in-memory image is newer than the file
        # and IS the truth; never fall back to what is still on disk.
        if _DIRTY.get(key) and key in _CACHE:
            return _CACHE[key][1]

    stamp = _stamp(path)
    if stamp is None:                      # missing file: let pandas raise as before
        return pd.read_excel(path, sheet_name=sheet_name)

    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]

    df = pd.read_excel(path, sheet_name=sheet_name)

    with _CACHE_LOCK:
        # Re-stamp after the read: a file rewritten *while* we were parsing
        # must not be cached under the pre-write stamp.
        _CACHE[key] = (_stamp(path), df)
        if len(_CACHE) > _MAX_CACHED:
            for stale in list(_CACHE)[:-_MAX_CACHED]:
                _CACHE.pop(stale, None)
    return df


def write_excel_cached(df, path, sheet_name=0, **kwargs):
    """
    Write a workbook and keep the cache in step with what is now on disk.

    Inside a :func:`workbook_session` for this path the write is **deferred**:
    the frame becomes the session's in-memory image and reaches disk at the
    next :func:`checkpoint` or when the session closes.
    """
    key = (str(Path(path)), sheet_name)
    with _CACHE_LOCK:
        deferred = str(Path(path)) in _DEFERRED
        if deferred:
            # No stamp: the file on disk is now older than what we hold, so a
            # stamp would make the next read look valid and serve stale data.
            _CACHE[key] = (None, df)
            _DIRTY[key] = True
            return
    df.to_excel(path, index=False, **kwargs)
    with _CACHE_LOCK:
        _CACHE[key] = (_stamp(path), df)


def invalidate_excel_cache(path=None):
    """Drop cached parses (all of them, or just one file)."""
    with _CACHE_LOCK:
        if path is None:
            _CACHE.clear()
        else:
            for key in [k for k in _CACHE if k[0] == str(Path(path))]:
                _CACHE.pop(key, None)


# ---------------------------------------------------------------------------
# Deferred (write-behind) workbook sessions
# ---------------------------------------------------------------------------
# Reading is cached above, but each *update* still rewrote the whole workbook,
# and the per-document pipeline updates the registry and the analysis logs
# several times per PDF. Rewriting a 5000-row registry six times per document
# is most of what is left of the quadratic cost.
#
# Inside a session those writes accumulate in memory and are flushed on a
# checkpoint (so a crash or a cancel costs at most the documents since the last
# one, never the whole run) and again on close. Reads inside the session see
# the in-memory image, so nothing observes a half-written state -- as long as
# every reader goes through read_excel_cached, which is why the pipeline's
# readers were converted first.
_DEFERRED = {}     # path -> open session count (nested/overlapping sessions)
_DIRTY = {}        # (path, sheet) -> True


def _flush_locked(paths=None):
    """
    Write dirty deferred workbooks (all of them, or just ``paths``). Caller
    holds the lock.
    """
    written = []
    for key in list(_DIRTY):
        path, sheet_name = key
        if paths is not None and path not in paths:
            continue
        entry = _CACHE.get(key)
        if entry is None:
            _DIRTY.pop(key, None)
            continue
        df = entry[1]
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            df.to_excel(path, index=False)
            _CACHE[key] = (_stamp(path), df)
            _DIRTY.pop(key, None)
            written.append(path)
        except Exception as e:  # noqa: BLE001 - keep it dirty and try again later
            print(f"⚠️ Could not write {path}: {e}")
    return written


# A checkpoint rewrites whole workbooks, so its cost grows with the space while
# the interval between checkpoints does not: at "every 25 documents" a pass over
# N documents pays N/25 rewrites of an ever-larger file — quadratic, and by a
# few thousand documents a single checkpoint stalls for tens of seconds with the
# progress bar frozen (nothing reports progress *inside* one). Checkpoints are
# therefore also spaced in TIME: at most one per CHECKPOINT_MIN_SECONDS, which
# bounds the work a crash can lose by that many seconds instead of by a document
# count, and bounds the number of rewrites by the pass's duration instead of by
# its size.
CHECKPOINT_EVERY = 25
CHECKPOINT_MIN_SECONDS = 30.0


class Checkpointer:
    """Decides when a batch pass should write its buffers to disk.

    ``due(i)`` is True at most once per ``min_seconds``, and only on a multiple
    of ``every`` — so a short pass still checkpoints on the familiar document
    boundary, while a long one stops paying for a full rewrite every 25
    documents. The caller always flushes once more when the pass ends, so
    nothing depends on a checkpoint having happened.
    """

    def __init__(self, every=CHECKPOINT_EVERY, min_seconds=CHECKPOINT_MIN_SECONDS):
        self.every = max(1, int(every))
        self.min_seconds = float(min_seconds)
        self._last = time.monotonic()

    def due(self, i):
        if i % self.every:
            return False
        now = time.monotonic()
        if now - self._last < self.min_seconds:
            return False
        self._last = now
        return True


def set_cell(df, mask, col, value):
    """
    Assign ``value`` into ``df.loc[mask, col]``, widening the column when the
    value does not fit its dtype.

    The workbooks this package updates row-by-row are ragged by nature: a
    document with three Research Areas leaves ``Research Areas 4 Is_Subset``
    empty, a document with no Results section leaves the overview's ``Results``
    column empty — and a column nothing has filled yet is read back from Excel
    as all-NaN ``float64``. pandas 3 refuses to put a bool or a string into such
    a column ("Invalid value 'False' for dtype 'float64'") where pandas 1.x
    silently widened it, which aborts the write for that whole section or
    document. The column is therefore promoted to ``object`` on demand — Excel
    is untyped, so nothing is lost, and columns that stay homogeneous keep
    their dtype.

    Booleans get the same treatment up front rather than on failure. Only a
    *Python* ``False`` raises against a float column; a numpy ``np.False_`` —
    which is what ``DataFrame[col].values[0]`` yields for a bool column — is a
    numeric subtype and would be accepted silently, writing ``0.0``/``1.0``
    into the sheet where the reader expects TRUE/FALSE. The column is widened
    first and the value normalised to a plain ``bool``, so an ``Is_Subset``
    cell reads as a boolean no matter which document created the column.

    Use this instead of a bare ``df.loc[mask, col] = value`` anywhere a sheet is
    updated in place with values whose type varies by row.
    """
    import numpy as np
    import pandas as pd

    if col not in df.columns:
        df[col] = pd.Series([None] * len(df), dtype=object)
    if isinstance(value, (bool, np.bool_)):
        value = bool(value)
        if df[col].dtype != object:
            df[col] = df[col].astype(object)
    try:
        df.loc[mask, col] = value
    except (TypeError, ValueError):
        df[col] = df[col].astype(object)
        df.loc[mask, col] = value


def checkpoint():
    """Flush the deferred workbooks now (call between documents)."""
    with _CACHE_LOCK:
        return _flush_locked()


@contextmanager
def workbook_session(*paths):
    """
    Defer writes to ``paths`` for the duration of the block.

    Nesting is safe: sessions are reference-counted per path, and the writes
    are flushed when the outermost one closes -- including on an exception, so
    a failed pass still keeps everything it had done.
    """
    keys = [str(Path(p)) for p in paths if p]
    with _CACHE_LOCK:
        for k in keys:
            _DEFERRED[k] = _DEFERRED.get(k, 0) + 1
    try:
        yield checkpoint
    finally:
        with _CACHE_LOCK:
            for k in keys:
                if _DEFERRED.get(k, 0) <= 1:
                    _DEFERRED.pop(k, None)
                else:
                    _DEFERRED[k] -= 1
            # Only the paths this session actually closed: an inner session
            # exiting must not flush what the outer one is still batching.
            closed = {k for k in keys if k not in _DEFERRED}
            _flush_locked(closed if _DEFERRED else None)


def get_column_value(excel_file, column_name, idx):
    """
    Retrieve the value from a specific column at a given index in the Excel file.
    
    Parameters:
        excel_file (str or Path): Path to the Excel file.
        column_name (str): The name of the column from which to retrieve the value.
        idx (int): The index of the row from which to retrieve the value.
    
    Returns:
        value: The value at the specified index in the specified column.
    """
    # Load the Excel file into a DataFrame (cached while the file is unchanged)
    df = read_excel_cached(excel_file)

    # Ensure the column exists in the DataFrame
    if column_name not in df.columns:
        raise ValueError(f"Column '{column_name}' not found in the Excel file.")
    
    # Retrieve and return the value at the specified index
    return df.iloc[idx][column_name]

def extract_column(file_path: str, column_name: str) -> list:
    """
    Extracts all data from a specified column in a single-sheet Excel file into a list.
    It automatically reads the first sheet.

    Args:
        file_path (str): The full path to the Excel file (e.g., 'C:/data/input.xlsx').
        column_name (str): The name of the column to extract.

    Returns:
        List: A list containing all the values from the specified column.
    """
    try:
        # Read the entire Excel file (it defaults to the first sheet)
        # Setting header=0 (the default) ensures it uses the first row as column names
        df = read_excel_cached(file_path)

        # Check if the column exists
        if column_name in df.columns:
            # Extract the column data and convert it to a Python list
            data_list = df[column_name].astype(str).replace('nan', '').tolist()
            return data_list
        else:
            print(f"Error: '{column_name}' not found in the sheet.")
            return []

    except FileNotFoundError:
        print(f"Error: File not found at path: {file_path}")
        return []
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return []

def get_corresponding_value(excel_file_path, column_1, value_1, column_2):
    try:
        # Load the Excel file into a DataFrame (cached while unchanged: this is
        # the single hottest call in the per-document pipeline)
        df = read_excel_cached(excel_file_path)
        
        # Check if the columns exist in the DataFrame
        if column_1 not in df.columns or column_2 not in df.columns:
            # print(f"Columns '{column_1}' or '{column_2}' not found in the Excel file.")
            print(f"No existing information of '{column_1}: {value_1}' or '{column_2}'.")
            return None
        
        # Find the row where column_1 matches the given value_1
        matching_row = df[df[column_1] == value_1]
        
        if matching_row.empty:
            # print(f"No matching row found for value '{value_1}' in column '{column_1}'.")
            print(f" '{value_1}' - is being Processed for the 1st time")
            return None
        
        # Retrieve the corresponding value from column_2
        corresponding_value = matching_row[column_2].values[0]
        return corresponding_value
    
    except Exception as e:
        print(f"An error occurred: {e}")
        return None

def update_corresponding_value(excel_file_path, column_1, value_1, column_2, new_value):
    try:
        # Load the Excel file into a DataFrame (cached read; the copy below is
        # what gets mutated, so the cached parse is never altered in place)
        df = read_excel_cached(excel_file_path)
        
        # Check if the columns exist in the DataFrame
        if column_1 not in df.columns or column_2 not in df.columns:
            # print(f"Columns '{column_1}' or '{column_2}' not found in the Excel file.")
            print(f"No existing information of '{column_1}: {value_1}' or '{column_2}'.")
            return False
        
        # Find the row where column_1 matches the given value_1
        matching_row_index = df[df[column_1] == value_1].index
        
        if matching_row_index.empty:
            # print(f"No matching row found for value '{value_1}' in column '{column_1}'.")
            print(f" '{value_1}' - is being Processed for the 1st time")
            return False
        
        # Update the corresponding value in column_2. An all-empty column is
        # read back as float64 (all NaN) and pandas then refuses a string
        # assignment, so coerce it to object first.
        df = df.copy()
        if isinstance(new_value, str) and df[column_2].dtype != object:
            df[column_2] = df[column_2].astype("object")
        df.at[matching_row_index[0], column_2] = new_value

        # Save the updated DataFrame back to the Excel file
        write_excel_cached(df, excel_file_path)
        
        # print(f"Successfully updated the value in '{column_2}' to '{new_value}' for row where '{column_1}' is '{value_1}'.")
        
        print(f"updated info'{column_2}': '{new_value}'.")
        return True
    
    except Exception as e:
        print(f"An error occurred: {e}")
        return False

def get_values_from_sorted_numbers(excel_file_path, num_column, value_column, n):
    # Load the Excel file
    df = pd.read_excel(excel_file_path)

    # Sort the dataframe based on the 'num_column' in ascending order
    sorted_df = df.sort_values(by=num_column).reset_index(drop=True)

    # Get the first 'n' rows from the sorted dataframe
    first_n_rows = sorted_df.head(n)

    # Extract the corresponding values from the 'value_column'
    result_values = first_n_rows[value_column].tolist()

    return result_values

def get_values_from_sorted_numbers_and_save(excel_file_path, num_column, value_column, n, output_file_path):
    # Load the Excel file
    if not os.path.exists(excel_file_path):
        raise FileNotFoundError(f"Input file {excel_file_path} does not exist.")

    df = pd.read_excel(excel_file_path)

    # Print the column names to debug
    # print("Columns in the DataFrame:", df.columns)

    # Strip spaces in case there are any hidden characters
    df.columns = df.columns.str.strip()

    # Check if columns exist
    if num_column not in df.columns or value_column not in df.columns:
        raise KeyError(f"Columns '{num_column}' or '{value_column}' not found in the DataFrame.")

    # Sort the dataframe based on the 'num_column' in ascending order
    sorted_df = df.sort_values(by=num_column).reset_index(drop=True)

    # Get the first 'n' rows from the sorted dataframe
    first_n_rows = sorted_df.head(n)

    # Extract the corresponding values from the 'value_column'
    result_values = first_n_rows[value_column].tolist()

    # Check if the output file exists, if not, create and save
    if not os.path.exists(os.path.dirname(output_file_path)):
        os.makedirs(os.path.dirname(output_file_path))  # Create directories if needed

    # Save only the first 'n' rows to the given output file path
    first_n_rows.to_excel(output_file_path, index=False)

    print(f"search phrases saved in {output_file_path}")

    return result_values

def add_column_sum(excel_file_path, col1, col2, col3):
    """
    This function loads an Excel file, performs the sum of col1 and col2,
    and stores the result in col3. If column names are missing, assigns default names.
    Finally, the modified Excel file is saved with the same name.
    
    Args:
    excel_file_path (str): The file path of the Excel file.
    col1 (str): The name of the first column for summation.
    col2 (str): The name of the second column for summation.
    col3 (str): The name of the column where the result will be stored.
    """
    # Load the Excel file into a DataFrame)
    df = pd.read_excel(excel_file_path)  # header=None if columns are missing


        # Print the column names to debug
    # print("Columns in the DataFrame:", df.columns)

    # Assign default column names if not present
    if df.columns.isnull().any():
        df.columns = [f"Column {i+1}" for i in range(df.shape[1])]
        # print("Column names were missing. Default names assigned.")

    # Check if the column names exist
    if col1 not in df.columns or col2 not in df.columns:
        print(f"Error:'{col1}' and/or '{col2}' not found in {excel_file_path}.")
        return

    # Perform the sum of col1 and col2 and store it in col3
    df[col3] = df[col1] + df[col2]

    # Save the modified DataFrame back to Excel with the same file name
    df.to_excel(excel_file_path, index=False)

    # print(f"Column {col3} has been updated with the sum of {col1} and {col2}. File saved as {excel_file_path}")



def sum_columns_ending_with_to_target(
    excel_path: str | Path,
    suffix: str,
) -> str:
    """
    For all sheets in the given Excel file:
      - Find all columns whose names end with `suffix`
      - Sum them row-wise
      - Store the sums into a new column 'TOTAL{suffix}' (if it doesn't exist)
      - Save result back to the same Excel file (overwrites existing file)

    Notes:
      - Non-numeric values are treated as 0 (coerced to NaN then filled with 0).
    """
    excel_path = Path(excel_path)

    # Load the entire Excel file to get all sheet names
    excel_data = pd.ExcelFile(excel_path, engine="openpyxl")
    sheet_names = excel_data.sheet_names

    # Iterate over all sheets
    for sheet_name in sheet_names:
        # Load the current sheet
        df = excel_data.parse(sheet_name)

        # Find columns ending with the suffix
        suffix_cols = [c for c in df.columns if str(c).endswith(suffix)]
        if not suffix_cols:
            continue  # Skip sheet if no matching columns are found

        # Create a new column for the total sum with the name 'TOTAL{suffix}'
        target_col = f"TOTAL{suffix}"

        # Sum across all suffix columns
        cols_to_sum = suffix_cols
        numeric_block = df[cols_to_sum].apply(pd.to_numeric, errors="coerce").fillna(0)
        df[target_col] = numeric_block.sum(axis=1)

        # Save back to the same Excel file (overwrites the current sheet)
        with pd.ExcelWriter(excel_path, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            df.to_excel(writer, sheet_name=sheet_name, index=False)

    return f"Updated Excel file saved at {excel_path}"

def aggregate_query_excel_data(folder_path, column_name, output_file):
    all_data = []
    metadata_cols = ['Original_UUID', 'Filename']
    print(f'folder path identified : {folder_path}')
    
    for file in Path(folder_path).glob("*.xlsx"):
        # if "Overview" in file.name: continue # Don't aggregate the overview itself
        try:
            df = pd.read_excel(file)
            if column_name in df.columns:
                cols = [column_name] + [c for c in metadata_cols if c in df.columns]
                all_data.append(df[cols])
        except Exception as e:
            print(f"Error reading {file.name}: {e}")

    if all_data:
        combined = pd.concat(all_data, ignore_index=True)
        # Group and count
        counts = combined.groupby([column_name] + metadata_cols, as_index=False).size()
        counts.rename(columns={'size': 'Occurrences'}, inplace=True)
        # Sort descending
        counts.sort_values(by='Occurrences', ascending=False, inplace=True)
        counts.to_excel(output_file, index=False)
        print(f"Report saved at: {output_file}")   

# def aggregate_querry_excel_data(VDB, column_name, output_file):
#     all_data = []
    
#     folder_path=Path(VDB.query_storage)
#     # Define the additional columns we want to preserve
#     metadata_columns = ['Original_UUID', 'Filename']
#     folder_path_obj = Path(folder_path)

#     search_root='/remotedata/U/DLR+kata_du/ALR DATA'
#     destination_folder=Path(VDB.querry_storage_pdfs)

#     for filename in os.listdir(folder_path):
#             if filename.endswith(".xlsx") or filename.endswith(".xls"):
#                 file_path = folder_path_obj / filename
#                 try:
#                     df = pd.read_excel(file_path)
#                     if column_name in df.columns:
#                         # Filter for columns that exist in the file
#                         cols_to_extract = [column_name] + [c for c in metadata_columns if c in df.columns]
#                         all_data.append(df[cols_to_extract])
#                 except Exception as e:
#                     print(f"Could not read {filename}: {e}")

#     if all_data:
#         combined_df = pd.concat(all_data, ignore_index=True)
        
#         # Group and count occurrences
#         counts = combined_df.groupby([column_name, 'Original_UUID', 'Filename'], as_index=False).size()
#         counts.rename(columns={'size': 'Occurrences'}, inplace=True)
        
#         # 1. Sort from most occurred to least occurred
#         counts.sort_values(by='Occurrences', ascending=False, inplace=True)
        
#         # Save the sorted Excel report
#         counts.to_excel(output_file, index=False)
#         print(f"Success! Report saved and sorted at {output_file}")

#         # 2. Extract unique filenames and move corresponding PDFs
#         unique_filenames = counts['Filename'].unique().tolist()
#         # move_matching_pdfs(unique_filenames, search_root, destination_folder)
        
#     else:
#         print("No data found to process.")

import pandas as pd
import os
import shutil
from pathlib import Path

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
    # Load the Excel file into a DataFrame
    df = pd.read_excel(excel_file)

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
        df = pd.read_excel(file_path)

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
        # Load the Excel file into a DataFrame
        df = pd.read_excel(excel_file_path)
        
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
        # Load the Excel file into a DataFrame
        df = pd.read_excel(excel_file_path)
        
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
        if isinstance(new_value, str) and df[column_2].dtype != object:
            df[column_2] = df[column_2].astype("object")
        df.at[matching_row_index[0], column_2] = new_value
        
        # Save the updated DataFrame back to the Excel file
        df.to_excel(excel_file_path, index=False)
        
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

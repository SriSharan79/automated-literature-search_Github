import sys

from COMMON.General_Utils import is_similar
sys.path.extend([
    r'src',
    r'src/COLLECTION',
    r'Working_Code',
    r'src/DATA_ANALYSIS',
    r'src/COMMON',
    r'src/Command_Line_UI'
])
from datetime import datetime
import json
import os
from colorama import Fore, Style

def print_json_file(file_path):
    """
    Reads a JSON file from the given path and prints its content 
    formatted with nice indentation in the terminal.
    """
    # Check if the file actually exists first
    if not os.path.exists(file_path):
        print(f"Error: The file at '{file_path}' does not exist.")
        return

    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
            # indent=4 makes it pretty and readable in the console
            print(Fore.LIGHTYELLOW_EX + json.dumps(data, indent=4))
            
    except json.JSONDecodeError:
        print(f"Error: '{file_path}' is not a valid JSON file.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

def store_to_json(input_data, file_path,time_taken):
    try:
        
        # Convert the string input to a dictionary (assuming it's a valid JSON string)
        data = json.loads(input_data)
        
        # Add timestamp and time taken keys
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")# Get current time in readable format
        
        # Add the timestamp and time taken to the data dictionary
        data["Timestamp"] = timestamp
        data["Time Taken (seconds)"] = time_taken
        
        # Write the updated dictionary to a JSON file at the specified file path
        with open(file_path, 'w') as json_file:
            json.dump(data, json_file, indent=2)  # Use 'indent=2' for pretty printing
        
        # print(f"Data successfully written to {file_path}")
    
    except json.JSONDecodeError as e:
        print(f"Invalid JSON input: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")

def store_to_json_with_text(input_data, file_path,time_taken, text,type):
    try:
        
        # Convert the string input to a dictionary (assuming it's a valid JSON string)
        data = json.loads(input_data)
        
        # Add timestamp and time taken keys
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")# Get current time in readable format
        
        # Add the timestamp and time taken to the data dictionary
        data[f"{type} Text identified:"] = text
        data["Timestamp"] = timestamp
        data["Time Taken (seconds)"] = time_taken
        
        # Write the updated dictionary to a JSON file at the specified file path
        with open(file_path, 'w') as json_file:
            json.dump(data, json_file, indent=2)  # Use 'indent=2' for pretty printing
        
        # print(f"Data successfully written to {file_path}")
    
    except json.JSONDecodeError as e:
        print(f"Invalid JSON input: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")

def pretty_print_json_from_file(file_path):
    # Load JSON data from the file
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
        
        # Print each section with colors for clarity
        print(Fore.CYAN + "Research Areas:")
        for area in json_data["Research Areas"]:
            print(Fore.GREEN + f"  - {area}")
        
        print(Style.RESET_ALL)  # Reset style

        print(Fore.CYAN + "\nResearch Problem:")
        print(Fore.YELLOW + f"  {json_data['Research Problem']}")
        
        print(Style.RESET_ALL)  # Reset style

        print(Fore.CYAN + "\nKey Concepts:")
        for concept in json_data["Key Concepts"]:
            print(Fore.GREEN + f"  - {concept}")
        
        print(Style.RESET_ALL)  # Reset style

        print(Fore.CYAN + "\nObjective:")
        print(Fore.YELLOW + f"  {json_data['Objective']}")
        
        print(Style.RESET_ALL)  # Reset style

        print(Fore.CYAN + "\nMethodology:")
        print(Fore.YELLOW + f"  {json_data['Methodology']}")
        
        print(Style.RESET_ALL)  # Reset style

        print(Fore.CYAN + "\nResults:")
        for result in json_data["Results"]:
            print(Fore.GREEN + f"  - {result}")
        
        print(Style.RESET_ALL)  # Reset style

        print(Fore.CYAN + "\nConclusion:")
        print(Fore.YELLOW + f"  {json_data['Conclusion']}")
        
        print(Style.RESET_ALL)  # Reset style

    except FileNotFoundError:
        print(Fore.RED + f"Error: The file at '{file_path}' was not found.")
    except json.JSONDecodeError:
        print(Fore.RED + f"Error: Failed to decode JSON from the file at '{file_path}'.")
    except Exception as e:
        print(Fore.RED + f"An error occurred: {str(e)}")



def get_key_from_file(file_path, target_key):
    """
    Loads a JSON file and finds all occurrences of target_key.
    
    Returns a single consolidated list:
    - If the target value is a list, elements are flattened into the result.
    - Special handling for 'Chunks'-style lists:
        value like [[1, "text"], [2, "text2"], ...]
      will return ["1 text", "2 text2", ...]
    
    Ignores malformed items safely.
    """
    def normalise_value(v):
        """
        Convert a found value into a list of output items (flatten-ready),
        with special handling for Chunks-style entries.
        """
        out = []

        # If it's not a list, just return as a single item
        if not isinstance(v, list):
            return [v]

        # If it's a list, it might be:
        # - a normal list of values: ["a", "b"]
        # - a Chunks list: [[1, "txt"], [2, "txt2"]]
        # - mixed / nested
        for item in v:
            # Chunks row case: [number, "text"]
            if (
                isinstance(item, (list, tuple))
                and len(item) >= 2
                and isinstance(item[0], int)
                and isinstance(item[1], str)
            ):
                out.append(f"{item[0]} {item[1].strip()}")
                continue

            # If item is a plain string/number/etc.
            if isinstance(item, (str, int, float, bool)) or item is None:
                out.append(item)
                continue

            # If item is another list (nested), flatten one level safely
            if isinstance(item, list):
                out.extend(item)
                continue

            # If item is a dict or something else, keep it as-is
            out.append(item)

        return out

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        def search(obj, key):
            results = []

            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k == key:
                        results.extend(normalise_value(v))
                    results.extend(search(v, key))

            elif isinstance(obj, list):
                for item in obj:
                    results.extend(search(item, key))

            return results

        return search(data, target_key)

    except FileNotFoundError:
        print(f"Error: The file at {file_path} was not found.")
        return []
    except json.JSONDecodeError:
        print(f"Error: Failed to decode JSON from {file_path}.")
        return []


def get_value_by_pair(file_path, anchor_key, anchor_value, target_key):
    """
    Finds a dictionary where anchor_key matches anchor_value, 
    then returns the value of target_key from that same dictionary.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        def search(obj):
            # If we find a dictionary, check for the pair
            if isinstance(obj, dict):
                current_val = obj.get(anchor_key)
                
                # Verify the key exists and its value is a string before converting to lowercase
                if isinstance(current_val, str):
                    if is_similar(current_val.lower(), anchor_value.lower()) or(anchor_value.lower() in current_val.lower()):
                        if target_key in obj:
                            return obj[target_key]
                
                # Otherwise, keep diving into the values of this dict
                for v in obj.values():
                    result = search(v)
                    if result is not None: 
                        return result
            
            # If we find a list, search each item
            elif isinstance(obj, list):
                for item in obj:
                    result = search(item)
                    if result is not None: 
                        return result
            
            return None

        return search(data)

    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error: {e}")
        return None

def get_chunks_from_references(file_path):
    """
    This function reads the JSON file, searches for sections with various reference headings, 
    and returns the chunks (texts) from the found section.

    :param file_path: Path to the JSON file
    :return: List of texts from the "Chunks" of the reference section or None if not found
    """
    try:
        # print(f"Opening file: {file_path}")
        # Open the JSON file and load the data
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # print(f"Successfully loaded JSON. Total sections: {len(data)}")

        # List of possible reference headings
        reference_headings = ["references", "bibliography", "reference", "works cited", "cited works", "citations"]

        # Search for any of the reference sections
        for section in data:
            section_name = section.get("Section Name", "")
            # print(f"Checking section: {section_name}")

            # Check if the section matches any of the reference headings
            if any(is_similar(section_name, heading,0.5) for heading in reference_headings) and "Chunks" in section:
                # print(f"Found reference section: {section_name} with Chunks!")
                # Extract the texts from each chunk
                texts = [chunk[1] for chunk in section["Chunks"] if isinstance(chunk, list) and len(chunk) > 1]
                # print(f"Extracted {len(texts)} chunks")
                return texts

        # Return None if no reference section or "Chunks" is found
        # print("No reference section or 'Chunks' found.")
        return None

    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error: {e}")
        return None
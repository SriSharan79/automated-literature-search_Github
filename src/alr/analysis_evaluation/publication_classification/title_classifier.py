from alr.common.file_manager import DataAnalyzeManager, Vec_DB_Manager
from alr.common.llm_utils import llm_call

from colorama import Fore, Style, init
import pandas as pd
from datetime import datetime
import sys
from collections import deque
import textwrap
import requests
import json
import re # Import regex
from typing import List,Dict,Any
from colorama import Fore, init
init(autoreset=True)
import time
from typing import List,Dict,Any

# Initialize a deque to keep track of request timestamps outside the function
# maxlen=20 ensures we only ever keep track of the last 20 hits
REQUEST_TIMES = deque(maxlen=10)


system_prompt_sysE = """
You are a rigorous Academic Classifier specializing in Aerospace Systems and Safety Engineering. 
Your goal is to evaluate a publication content against a specific taxonomy with high precision.

### Evaluation Criteria:
- **Strict Inclusion:** Only mark a topic as "true" if the Text Content contains explicit keywords or clearly implied methodologies belonging to that field.
- **Contextual Awareness:** Distinguish between general terms and engineering-specific applications (e.g., "Risk" in a financial context is 'false', but "Risk" in an avionics context is 'true').
- **No Neutrality:** You must make a binary choice (true/false) for every single topic provided in the schema.

### Analysis Methodology:
Before generating the JSON, internally analyze the Text Content for:
1. Primary domain (e.g., Aerospace, Software, Systems).
2. Methodologies mentioned (e.g., MBSE, STPA, Formal Methods).
3. Technology stack (e.g., LLMs, Neural Networks).

### Output Format:
You must return ONLY a JSON object. Do not include conversational filler. 
The JSON must follow this exact structure, covering all 10 specific topics:

{
  "Systems Engineering": boolean,
  "Safety Engineering": boolean,
  "Model based system Engineering": boolean,
  "Model based safety assessments": boolean,
  "Requirements Engineering": boolean,
  "Risk Assessment": boolean,
  "Aircraft Certification": boolean,
  "Aircraft System Development": boolean,
  "Hazard analysis": boolean,
  "Artificial Intelligence": boolean,
  "Large Language Models": boolean
}
"""
system_prompt_lit= """
You are a rigorous Academic Classifier specializing in Research Methodologies and Meta-Research. 
Your goal is to evaluate a publication Text Content against a specific literature review taxonomy with high precision.

### Evaluation Criteria:
- **Strict Inclusion:** Only mark an aspect or concept as "true" if the Text Content contains explicit keywords or clearly implied methodologies belonging to that specific field.
- **Contextual Awareness:** Distinguish between general terms and review-specific methodologies (e.g., "Synthesis" in a chemical context is 'false', but "Synthesis" in a thematic data context is 'true').
- **No Neutrality:** You must make a binary choice (true/false) for every single aspect provided in the schema.

### Analysis Methodology:
Before generating the JSON, internally analyze the Text Content for:
1. Primary review type being evaluated or proposed.
2. Specific methodological mechanisms and tools highlighted.
3. Theoretical or practical objectives intended by the review process.

### Output Format:
You must return ONLY a JSON object. Do not include conversational filler, markdown formatting (like ```json), or explanations. 
The JSON must follow this exact structure, covering all specified dimensions:

{
  "Primary Review Type - Systematic Review": boolean,
  "Primary Review Type - Narrative/Critical Review": boolean,
  "Primary Review Type - Meta-Analysis": boolean,
  "Primary Review Type - Scoping Review": boolean,
  "Methodological Focus - PRISMA/Reporting Standards": boolean,
  "Methodological Focus - Snowballing Search": boolean,
  "Methodological Focus - Thematic Synthesis": boolean,
  "Methodological Focus - Quality Appraisal Frameworks": boolean,
  "Target Output - Gap Identification": boolean,
  "Target Output - Framework Development": boolean,
  "Tools/Tech - Artificial Intelligence Concepts": boolean,
  "Tools/Tech - Large Language Models": boolean
}
"""
# def blabla_ask_llm_test(
#     prompt: str,
#     sys_prompt: str,
#     temperature: float = 0.3,
#     max_tokens: int = 8192,
#     blablador_key: str = None
# ) -> str:
#     """Query Blablador LLM with dynamic model selection and a 20 req/min rate limit."""
    
#     time.sleep(1.5)
    
#     # --- RATE LIMITER LOGIC ---
#     current_time = time.time()
    
#     # If we have already hit our 20 request capacity, check the oldest request
#     if len(REQUEST_TIMES) == 10:
#         oldest_request_time = REQUEST_TIMES[0]
#         elapsed_since_oldest = current_time - oldest_request_time
        
#         # If the oldest request happened less than 60 seconds ago, we must wait
#         if elapsed_since_oldest < 60:
#             sleep_time = 60 - elapsed_since_oldest
#             print(Fore.YELLOW + f"⚠️ Rate limit approaching. Sleeping for {sleep_time:.2f} seconds..." + Style.RESET_ALL)
#             time.sleep(sleep_time)
            
#     # Record the current timestamp for this request execution
#     REQUEST_TIMES.append(time.time())
#     # --------------------------

#     start_time = time.time()
    
#     # Dynamically select best model
#     model = "15 - Apertus-8B-Instruct-2509 - A new swiss model from September 2025"
#     print(f"🤖 Using model: {model}")

#     print(Fore.GREEN + f" Prompt : \n {prompt}"+ Style.RESET_ALL)
    
#     messages = [
#         {'role': 'system', 'content': sys_prompt},
#         {'role': 'user', 'content': prompt}
#     ]
    
#     payload = {
#         "model": model,
#         "messages": messages,
#         "temperature": temperature,
#         "max_tokens": max_tokens,
#     }

#     headers = {"Content-Type": "application/json"}    
#     BlaBla_API_Key = check_api_key('BlaBla Door')
#     key = blablador_key or BlaBla_API_Key
#     if key:
#         headers["Authorization"] = f"Bearer {key}"

#     url = f"{BLABLADOR_BASE_URL}/chat/completions"
#     resp = requests.post(url, headers=headers, json=payload)
#     resp.raise_for_status()

#     result = resp.json()
#     try:
#         content = result["choices"][0]["message"]["content"]

#         if content is None:
#             raise ValueError("Empty content received from Blablador")

#         # Clean the content
#         content = content.replace("```json", "").replace("```", "").strip()
            
#         print(Fore.CYAN + "\n--- RAW LLM RESPONSE START ---" + Style.RESET_ALL)
#         print(Fore.CYAN + content + Style.RESET_ALL)
#         print(Fore.CYAN + "--- RAW LLM RESPONSE END ---\n" + Style.RESET_ALL)
#         return content 
        
#     except (KeyError, IndexError, ValueError) as exc:
#         print(f"❌ Blablador failed. Full response: {result}")
#         raise ValueError(f"Unexpected response format from Blablador: {exc}") from exc

def classify_title(title, service=None):
    """
    Classify a publication by its title against the taxonomy.

    ``service`` selects which configured LLM engine to use ('O' = DLR Ollama,
    'B' = Blablador -- same codes as the "LLM Processing Service Engine"
    picker in the main window), and which session-selected model
    (``set_selected_model``/``get_selected_model``) is used for the call. If
    omitted, ``llm_call`` falls back to its own default engine/model.
    """
    Prompt = f"""Title of publication to be analyzed:
                - {title}
            """
            # Add a delay here (e.g., 1.5 seconds) to stay under rate limits
    time.sleep(1.5)
    try:
        response = llm_call(Prompt, system_prompt_sysE, service) if service else llm_call(Prompt, system_prompt_sysE)
        # Parse the string response into a dictionary
        return json.loads(response)
    except Exception as e:
        print(f"Error processing title '{title}': {e}")
        return {topic: False for topic in [
            "Systems Engineering", "Safety Engineering", "Model based system Engineering",
            "Model based safety assessments", "Requirements Engineering", "Risk Assessment",
            "Aircraft Certification", "Aircraft System Development", "Hazard analysis","Artificial Intelligence",
            "Large Language Models"
        ]}


TAXONOMY_TOPICS = [
    "Systems Engineering", "Safety Engineering", "Model based system Engineering",
    "Model based safety assessments", "Requirements Engineering", "Risk Assessment",
    "Aircraft Certification", "Aircraft System Development", "Hazard analysis",
    "Artificial Intelligence", "Large Language Models",
]


def classify_abstract(abstract_text, service=None):
    """
    Classify a publication against the same taxonomy as :func:`classify_title`,
    but using the identified abstract text (from the abstract analyzer) instead of
    the title. Returns ``{topic: bool}`` for every taxonomy topic; on failure it
    falls back to an all-False result. Requires an API key for the chosen service.

    ``service`` selects which configured LLM engine to use ('O' = DLR Ollama,
    'B' = Blablador), and which session-selected model is used for the call.
    If omitted, ``llm_call`` falls back to its own default engine/model.
    """
    Prompt = f"""Abstract of the publication to be analyzed:
                - {abstract_text}
            """
    # Small delay to stay under the Blablador rate limit.
    time.sleep(1.5)
    try:
        response = llm_call(Prompt, system_prompt_sysE, service) if service else llm_call(Prompt, system_prompt_sysE)
        return json.loads(response)
    except Exception as e:
        print(f"Error processing abstract: {e}")
        return {topic: False for topic in TAXONOMY_TOPICS}


if __name__ == "__main__":
    source_file='/remotedata/U/DLR+kata_du/ALR DATA/Only_MBSA/Title_Assessment.xlsx'
    df = pd.read_excel(source_file)

    classified_rows = []

    for index, row in df.iterrows():
        print(f"Processing row {index + 1}/{len(df)}: {row['Publication Name'][:50]}...")
        
        # Get classification
        classification = classify_title(row['Publication Name'])
        
        # Merge original row data with new classification data
        combined_data = {**row.to_dict(), **classification}
        classified_rows.append(combined_data)
        
        # 3. Save every time
        # We create a temporary DataFrame and save it to the output file
        temp_df = pd.DataFrame(classified_rows)
        temp_df.to_excel(source_file, index=False)
        
        # Small sleep to be kind to the API rate limits
        time.sleep(0.1)

    print(f"✅ Finished! All data saved to {source_file}")
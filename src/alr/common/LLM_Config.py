import os
from alr.common.file_manager import ALR_main_folder
import json
# Model repo id 
# model_repo_id = "openai/gpt-oss-20b"
# model_repo_id = "meta-llama/Llama-3.2-3B-Instruct"
model_repo_id = "meta-llama/Llama-3.1-8B-Instruct"
# model_repo_id = "meta-llama/Llama-3.1-8B"
# model_repo_id = "meta-llama/Llama-3.2-1B-Instruct"

# base path where the models were stored
base_path = "/localdata/user/kata_du/LLM Models"
# Adjust the path to your required model directory
local_model_dir = os.path.join(base_path, "00_LLM_model", model_repo_id)

API_keys_config= os.path.join(ALR_main_folder, "API_keys_config.json")

sys_env_DLR_ollama=os.getenv("Ollama_DLR_API_Key")
sys_env_BlaBla=os.getenv("BlaBla_API_Key")
Ollama_DLR_API_Key= None
BlaBla_API_Key=None

def get_api_key(API_Key_type):
    # Check if the config file exists
    if os.path.exists(API_keys_config):
        with open(API_keys_config, 'r') as file:
            config_data = json.load(file)
        
        # Check if API key exists in the config file
        api_key = config_data.get(API_Key_type, None)
        if api_key:
            # print(f"{API_Key_type} API Key found in the config file.")
            return api_key
        else:
            print(f"{API_Key_type} API Key not found in the config file.")
    else:
        print("Config file not found.")
    
    # Prompt user for the API key if not found
    api_key = input(f"Please enter your {API_Key_type} API Key: ")
    
    # Save the entered API key to the config file
    with open(API_keys_config, 'w') as file:
        config_data = {API_Key_type: api_key}
        json.dump(config_data, file, indent=4)

    # Store API key as environment variable
    
    if API_Key_type=='DLR Ollama':
        os.environ["Ollama_DLR_API_Key"] = api_key 
        
    
    if API_Key_type=='BlaBla Door':         
        os.environ["BlaBla_API_Key"] = api_key 
    print("API Key stored as environment variable.")
    return api_key

def check_api_key(API_Key_type):
    if API_Key_type=='DLR Ollama':
        if sys_env_DLR_ollama:
            return sys_env_DLR_ollama
        else:
            return get_api_key(API_Key_type)
            
    
    if API_Key_type=='BlaBla Door':
        if sys_env_BlaBla:
            return sys_env_BlaBla
        else:
            return get_api_key(API_Key_type)    
   
# System Prompts

BLABLADOR_BASE_URL = "https://api.helmholtz-blablador.fz-juelich.de/v1"
PREFERRED_BLABLADOR_MODELS = [
    "GPT-OSS-120b", "Llama-3.1-70B-Instruct", "Llama-3.1-8B-Instruct",
    "Mistral-Large-Instruct-2407", "Mistral-7B-Instruct-v0.3"
]

if __name__ == "__main__":
    
    Ollama_DLR_API_Key=check_api_key('DLR Ollama')
    BlaBla_API_Key = check_api_key('BlaBla Door')
    print (BlaBla_API_Key)
    print (Ollama_DLR_API_Key)

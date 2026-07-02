import os
from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline
import torch
from colorama import Fore, Style    

model_repo_id = "meta-llama/Llama-3.2-1B-Instruct"
base_path = "/localdata/user/kata_du/LLM Models"
local_model_dir = os.path.join(base_path, "00_LLM_model", model_repo_id)

# --- STEP 2: Load the model and tokenizer from the local directory ---
try:
    print(Fore.CYAN + f"\nLoading tokenizer from local path: {local_model_dir}" + Style.RESET_ALL)
    tokenizer = AutoTokenizer.from_pretrained(local_model_dir, local_files_only=True)

    print(Fore.CYAN + f"Loading model from local path: {local_model_dir}" + Style.RESET_ALL)
    # Add device_map='auto' for large models to utilize GPU if available
    # Adjust torch_dtype based on your GPU capabilities (bfloat16 for newer NVIDIA, float16 for older)
    # If no GPU, change device_map='cpu' and consider removing torch_dtype.
    model = AutoModelForCausalLM.from_pretrained(
        local_model_dir,
        local_files_only=True,
        device_map='auto',
        torch_dtype=torch.bfloat16 if torch.cuda.is_available() else None # Use bfloat16 if CUDA is available, else default
    )
    model.eval() # Set the model to evaluation mode (important for inference)
    print(Fore.GREEN + "Model and tokenizer loaded successfully!" + Style.RESET_ALL)

except Exception as e:
    print(Fore.RED + f"Error loading local Hugging Face model: {e}" + Style.RESET_ALL)
    print(Fore.RED + "Please ensure the model is correctly downloaded at the specified path and all required libraries (transformers, torch, accelerate) are installed." + Style.RESET_ALL)
    exit()

# --- STEP 3: Create a Hugging Face pipeline ---
# Configure generation parameters.
# max_new_tokens: maximum number of tokens to generate in the response.
# pad_token_id: often set to eos_token_id to avoid warnings for models without an explicit pad token.
# do_sample=False ensures deterministic (greedy) decoding for consistent answers.
hf_pipeline = pipeline(
    "text-generation",
    model=model,
    tokenizer=tokenizer,
    max_new_tokens=256, # Generate up to 256 new tokens
    pad_token_id=tokenizer.eos_token_id, # End-of-sequence token as pad token
    do_sample=False, # Use greedy decoding (deterministic)
    # You can add other parameters like top_k, top_p, temperature if do_sample=True
)
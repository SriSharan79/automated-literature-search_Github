from transformers import AutoTokenizer, AutoModelForCausalLM,AutoModelForSequenceClassification,AutoModel
from huggingface_hub import snapshot_download
from langchain_huggingface.llms import HuggingFacePipeline
from transformers import AutoModelForCausalLM, AutoTokenizer
from langchain_core.prompts import PromptTemplate

# Specify the model repository ID
model_repo_id = "mistralai/Ministral-3-14B-Instruct-2512"
import os
HuggingFace_Token = os.environ["HuggingFace_Token"]

# Define a local directory to store the model
local_model_path = f"/localdata/user/kata_du/LLM Models/00_LLM_model/{model_repo_id}"


# Download the model files
snapshot_download(
    repo_id=model_repo_id,
    use_auth_token=HuggingFace_Token,  # True uses the stored token from huggingface-cli login
    local_dir=local_model_path,
    local_dir_use_symlinks=False
)

# Define paths for tokenizer and model
tokenizer_path = f"{local_model_path}/Tokenizer/{model_repo_id}"
model_path = f"{local_model_path}/Model/{model_repo_id}"

# Load and save the tokenizer locally
tokenizer = AutoTokenizer.from_pretrained(model_repo_id,token =HuggingFace_Token)
tokenizer.save_pretrained(tokenizer_path)

# Load and save the model locally
model = AutoModelForCausalLM.from_pretrained(model_repo_id,token =HuggingFace_Token)
model.save_pretrained(model_path)

print(f"Model and tokenizer saved locally in {local_model_path}")

# pipe = pipeline("text-generation"
#                 , model=model,
#                   tokenizer=tokenizer, 
#                   max_new_tokens=512)
# hf = HuggingFacePipeline(pipeline=pipe)


# template = """Question: {question}

# Answer: Let's think step by step."""
# prompt = PromptTemplate.from_template(template)
# chain = prompt | hf

# question = "What is electroencephalography?"

# print(chain.invoke({"question": question}))
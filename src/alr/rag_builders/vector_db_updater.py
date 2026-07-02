import sys
sys.path.extend([
    r'src',
    r'src/COLLECTION',
    r'Working_Code',
    r'src/DATA_ANALYSIS',
    r'src/COMMON',
    r'src/Command_Line_UI'
])

import os
from pathlib import Path
import numpy as np
import torch
import torch.nn.functional as F
from torch import Tensor
from transformers import AutoTokenizer, AutoModel
import faiss
from pathlib import Path
from colorama import Fore, Style, init
from COLLECTION.Search_Phrase_Generator_Utils import rank_search_phrases
from COMMON.File_Manager import Vec_DB_Manager
from COMMON.JSON_file_Utils import get_key_from_file
from COMMON.Excel_Utils import get_values_from_sorted_numbers_and_save, sum_columns_ending_with_to_target


model_repo_id = "Qwen/Qwen3-Embedding-8B"
# base path where the models were stored
base_path = "/localdata/user/kata_du/LLM Models"
# Adjust the path to your required model directory
local_model_dir = os.path.join(base_path, "00_LLM_model", model_repo_id)


# Function to pool the last hidden states
def last_token_pool(last_hidden_states: Tensor, attention_mask: Tensor) -> Tensor:
    left_padding = (attention_mask[:, -1].sum() == attention_mask.shape[0])
    if left_padding:
        return last_hidden_states[:, -1]
    else:
        sequence_lengths = attention_mask.sum(dim=1) - 1
        batch_size = last_hidden_states.shape[0]
        return last_hidden_states[torch.arange(batch_size, device=last_hidden_states.device), sequence_lengths]

# ---------- model loading (GPU) ----------
def load_model_and_tokenizer(local_model_dir: str):
    if not local_model_dir:
        raise ValueError("local_model_dir is empty/None")

    tokenizer = AutoTokenizer.from_pretrained(
        local_model_dir,
        padding_side="left",
        local_files_only=True,
        trust_remote_code=True,
    )

    # If CUDA is available -> place model on GPU, otherwise CPU fallback
    use_cuda = torch.cuda.is_available()
    dtype = torch.float16 if use_cuda else torch.float32
    device_map = "auto" if use_cuda else "cpu"

    model = AutoModel.from_pretrained(
        local_model_dir,
        local_files_only=True,
        trust_remote_code=True,
        torch_dtype=dtype,
        device_map=device_map,
    )
    model.eval()
    return tokenizer, model

tokenizer, model = load_model_and_tokenizer(local_model_dir)
@torch.inference_mode()
def vectorize_strings(input_strings: list[str], max_length: int = 512) -> np.ndarray:

    batch = tokenizer(
        input_strings,
        padding=True,
        truncation=True,
        max_length=max_length,
        return_tensors="pt",
    )
    batch = {k: v.to(model.device) for k, v in batch.items()}

    outputs = model(**batch)
    emb = last_token_pool(outputs.last_hidden_state, batch["attention_mask"])

    # Normalise => cosine similarity works with inner product / L2
    emb = F.normalize(emb, p=2, dim=1)

    return emb.detach().cpu().numpy().astype(np.float32)


def create_faiss_index_cosine(vectors: np.ndarray) -> faiss.Index:
    # With normalised vectors: inner product == cosine similarity
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)
    return index

def save_index_file(index, file_path):
    # Validate index
    if index is None or not hasattr(index, "ntotal"):
        raise TypeError(f"index is not a faiss Index. Got type={type(index)}")

    # Normalise file_path to a real string
    if file_path is None:
        raise ValueError("file_path is None")

    file_path = str(Path(file_path))  # handles str/Path safely
    Path(file_path).parent.mkdir(parents=True, exist_ok=True)

    faiss.write_index(index, file_path)
    print(f"FAISS index saved to: {file_path} (vectors stored: {index.ntotal})")

def load_index_file(file_path):
    if file_path is None:
        return None

    file_path = str(Path(file_path))  # force Path/other path-like -> str

    if not os.path.exists(file_path):
        return None

    return faiss.read_index(file_path)


def add_new_strings_to_index(index_file: str, new_strings: list[str], max_length: int = 512):
        # Normalise file_path to a real string
    if index_file is None:
        raise ValueError("index_file is None")

    index_file = str(Path(index_file))  # handles str/Path safely
    Path(index_file).parent.mkdir(parents=True, exist_ok=True)
    # Ensure the index file path is correct and exists
    try:
        # Make sure the index file path is a string
        if isinstance(index_file, str):
            index = faiss.read_index(index_file)
        else:
            raise ValueError("The index file path must be a string.")

    except RuntimeError:
        # If index file doesn't exist or is corrupted, create a new index
        print(f"Index file not found or invalid. Creating a new index at {index_file}.")
        d = max_length  # Dimensionality of vectors
        index = faiss.IndexFlatL2(d)  # Using L2 distance, adjust based on your use case
    
    # Vectorize the new strings
    new_vecs = vectorize_strings(new_strings, max_length=max_length)

    # Add the vectors to the index
    index.add(new_vecs)
    
    # Save the updated index
    faiss.write_index(index, index_file)
    print(f"Added {len(new_strings)} vectors and saved updated index: {index_file}")


def search_similar(index_file: str, query: str, top_k: int = 5, max_length: int = 512):
    index = load_index_file(index_file)
    qvec = vectorize_strings([query], max_length=max_length)
    scores, ids = index.search(qvec, top_k)  # scores are cosine (since IndexFlatIP + normalised)
    return scores[0].tolist(), ids[0].tolist()


# Example usage
if __name__ == "__main__":
    storage_path='/remotedata/U/DLR+kata_du/ALR DATA/SLR_Process_Main/SLR_Process_results'
    VDB=Vec_DB_Manager(storage_path)
    strings= get_key_from_file(VDB.Research_Areas_DB_json,"Content")
    # print(RAs)
    embeds=vectorize_strings(strings)
    index_in=create_faiss_index_cosine(embeds)
    save_index_file(index_in,VDB.Research_Areas_DB_bin)
    print(f'strings: {len(strings)}')
    index=load_index_file(VDB.Research_Areas_DB_bin)
    print("index type:", type(index))
    print("index ntotal:", getattr(index, "ntotal", None))
    print("bin path type:", type(VDB.Research_Areas_DB_bin), "value:", VDB.Research_Areas_DB_bin)
    scores, ids = search_similar(VDB.Research_Areas_DB_bin, "search phrase" ,top_k=3)
    print("Top matches:")
    for s, i in zip(scores, ids):
        print(f"idx={i}  cosine={s:.4f}  text={strings[i] if i < len(strings) else '(newly added item)'}")




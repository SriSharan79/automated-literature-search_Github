import os
from pathlib import Path
import numpy as np
from colorama import Fore, Style, init

from alr.collection.search_phrase_generator_utils import rank_search_phrases
from alr.common.file_manager import Vec_DB_Manager
from alr.common.json_utils import get_key_from_file
from alr.common.excel_utils import get_values_from_sorted_numbers_and_save, sum_columns_ending_with_to_target

# Embedding model loading/calling now lives in llm_utils.py:
#   - vectorize_strings_local(): local GPU/CPU HuggingFace model
#     (Qwen/Qwen3-Embedding-8B by default), loaded lazily the same way
#     hf_pipeline_with_Lamma() / Local_Model_call() load the local chat model.
#   - get_embedding() / get_default_embedding_model(): remote API call against
#     DLR Ollama or Blablador's /embeddings endpoint.
from alr.common.llm_utils import (
    vectorize_strings_local,
    get_embedding,
    get_default_embedding_model,
    DEFAULT_EMBEDDING_MODEL,
)

# Which embedding method to use by default:
#   'local' -> local GPU/CPU HuggingFace model (vectorize_strings_local in llm_utils.py)
#   'api'   -> remote DLR Ollama / Blablador /embeddings call (get_embedding in llm_utils.py)
EMBEDDING_METHOD = "local"
EMBEDDING_SERVICE = "DLR Ollama"  # only used when EMBEDDING_METHOD == "api"


def vectorize_strings(
    input_strings: list[str],
    method: str = EMBEDDING_METHOD,
    service: str = EMBEDDING_SERVICE,
    model: str = None,
    max_length: int = 512,
    batch_size: int = 32,
) -> np.ndarray:
    """
    Get embedding vectors for a list of strings.

    method='local' (default): uses the local GPU/CPU HuggingFace model via
        llm_utils.vectorize_strings_local() - same behaviour/weights as before.
    method='api': calls the remote embedding API via llm_utils.get_embedding()
        (DLR Ollama / Blablador), batched, then L2-normalises the result
        client-side so cosine similarity == inner product (matches the FAISS
        IndexFlatIP index built below).
    """
    if not input_strings:
        return np.zeros((0, 0), dtype=np.float32)

    if method == "local":
        return vectorize_strings_local(input_strings, max_length=max_length)

    if method == "api":
        resolved_model = model or get_default_embedding_model(service)
        all_embeddings = []
        for i in range(0, len(input_strings), batch_size):
            batch = input_strings[i:i + batch_size]
            result = get_embedding(batch, service=service, model=resolved_model)
            all_embeddings.extend(result["embeddings"])

        vectors = np.array(all_embeddings, dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        vectors = vectors / norms
        return vectors

    raise ValueError(f"Unknown embedding method '{method}'. Expected 'local' or 'api'.")


def create_faiss_index_cosine(vectors: np.ndarray) -> "faiss.Index":
    import faiss

    # With normalised vectors: inner product == cosine similarity
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)
    return index


def save_index_file(index, file_path):
    import faiss

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
    import faiss

    if file_path is None:
        return None

    file_path = str(Path(file_path))  # force Path/other path-like -> str

    if not os.path.exists(file_path):
        return None

    return faiss.read_index(file_path)


def add_new_strings_to_index(
    index_file: str,
    new_strings: list[str],
    method: str = EMBEDDING_METHOD,
    service: str = EMBEDDING_SERVICE,
    model: str = None,
    max_length: int = 512,
):
    import faiss

    # Normalise file_path to a real string
    if index_file is None:
        raise ValueError("index_file is None")

    index_file = str(Path(index_file))  # handles str/Path safely
    Path(index_file).parent.mkdir(parents=True, exist_ok=True)

    # Vectorize the new strings first, since a freshly-created index needs to
    # know the embedding dimensionality up front.
    new_vecs = vectorize_strings(new_strings, method=method, service=service, model=model, max_length=max_length)

    # Ensure the index file path is correct and exists
    try:
        if isinstance(index_file, str):
            index = faiss.read_index(index_file)
        else:
            raise ValueError("The index file path must be a string.")

    except RuntimeError:
        # If index file doesn't exist or is corrupted, create a new index
        print(f"Index file not found or invalid. Creating a new index at {index_file}.")
        d = new_vecs.shape[1]  # Dimensionality of the embedding vectors actually returned
        index = faiss.IndexFlatIP(d)  # Inner product on normalised vectors == cosine similarity

    # Add the vectors to the index
    index.add(new_vecs)

    # Save the updated index
    faiss.write_index(index, index_file)
    print(f"Added {len(new_strings)} vectors and saved updated index: {index_file}")


def search_similar(
    index_file: str,
    query: str,
    top_k: int = 5,
    method: str = EMBEDDING_METHOD,
    service: str = EMBEDDING_SERVICE,
    model: str = None,
    max_length: int = 512,
):
    index = load_index_file(index_file)
    qvec = vectorize_strings([query], method=method, service=service, model=model, max_length=max_length)
    scores, ids = index.search(qvec, top_k)  # scores are cosine (since IndexFlatIP + normalised)
    return scores[0].tolist(), ids[0].tolist()


# Example usage
if __name__ == "__main__":
    storage_path = '/remotedata/U/DLR+kata_du/ALR DATA/SLR_Process_Main/SLR_Process_results'
    VDB = Vec_DB_Manager(storage_path)
    strings = get_key_from_file(VDB.Research_Areas_DB_json, "Content")
    # print(RAs)
    embeds = vectorize_strings(strings)  # defaults to method='local' (GPU HF model)
    index_in = create_faiss_index_cosine(embeds)
    save_index_file(index_in, VDB.Research_Areas_DB_bin)
    print(f'strings: {len(strings)}')
    index = load_index_file(VDB.Research_Areas_DB_bin)
    print("index type:", type(index))
    print("index ntotal:", getattr(index, "ntotal", None))
    print("bin path type:", type(VDB.Research_Areas_DB_bin), "value:", VDB.Research_Areas_DB_bin)
    scores, ids = search_similar(VDB.Research_Areas_DB_bin, "search phrase", top_k=3)
    print("Top matches:")
    for s, i in zip(scores, ids):
        print(f"idx={i}  cosine={s:.4f}  text={strings[i] if i < len(strings) else '(newly added item)'}")
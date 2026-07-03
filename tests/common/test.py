"""
test_embeddings.py

Standalone test script for:
  1. Discovering which models on DLR Ollama and Blablador are embedding models
     (i.e. their id/name contains 'embed').
  2. Resolving the default embedding model for each service, preferring
     'qwen3-8b-embeddings' when it's available.
  3. Calling the embeddings endpoint for each service with sample text.
  4. Saving the returned embedding vectors (and raw API response) to a JSON
     file on disk via save_embedding_result().

Run:
    python test_embeddings.py

Notes:
  - Requires valid API keys already stored/registered the same way the rest
    of llm_utils.py expects (see check_api_key / get_stored_api_key).
  - If a service has no reachable /models endpoint or no embedding models,
    the script reports it and moves on to the next service rather than
    crashing.
"""

from alr.common.llm_utils import (
    list_embedding_models,
    get_default_embedding_model,
    set_selected_embedding_model,
    get_embedding,
    save_embedding_result,
    DEFAULT_EMBEDDING_MODEL,
)

SAMPLE_TEXTS = [
    "The quick brown fox jumps over the lazy dog.",
    "Embeddings turn text into numerical vectors for semantic search.",
]

SERVICES = ["DLR Ollama", "BlaBla"]


def test_service(service: str):
    print("\n" + "=" * 70)
    print(f"Testing embedding models for: {service}")
    print("=" * 70)

    # 1. Discover embedding models available right now.
    embedding_models = list_embedding_models(service)
    print(f"[1] Embedding models found for {service}: {embedding_models}")

    if not embedding_models:
        print(f"[!] No embedding models available for {service} - skipping call test.")
        return None

    # 2. Resolve which one to use (prefers DEFAULT_EMBEDDING_MODEL, currently
    #    set to '{}').
    default_model = get_default_embedding_model(service)
    print(f"[2] Resolved default embedding model for {service}: {default_model}")

    # Optional: force a specific model instead of the resolved default, e.g.:
    # set_selected_embedding_model(service, "qwen3-8b-embeddings")

    # 3. Call the embeddings endpoint with sample text (batch of 2 strings).
    try:
        result = get_embedding(SAMPLE_TEXTS, service=service, model=default_model)
    except Exception as e:
        print(f"[X] Embedding call failed for {service}: {e}")
        return None

    embeddings = result["embeddings"]
    print(f"[3] Received {len(embeddings)} embedding vector(s) using model '{result['model']}'")
    for i, vec in enumerate(embeddings):
        preview = vec[:5]
        print(f"    - text[{i}]: dim={len(vec)}, first 5 values={preview}")

    # 4. Persist the result (vectors + raw response) to disk.
    saved_path = save_embedding_result(result, prefix=f"test_{service.replace(' ', '_')}")
    print(f"[4] Saved embedding result to: {saved_path}")

    return result


def main():
    results = {}
    for service in SERVICES:
        results[service] = test_service(service)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for service, result in results.items():
        if result:
            dim = len(result["embeddings"][0]) if result["embeddings"] else 0
            print(
                f"{service}: OK - model='{result['model']}', "
                f"vectors={len(result['embeddings'])}, dim={dim}"
            )
        else:
            print(f"{service}: SKIPPED / FAILED")


if __name__ == "__main__":
    main()
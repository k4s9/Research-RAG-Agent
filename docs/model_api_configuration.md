# Embedding and reranker API configuration

The application uses HTTP clients and does not require model processes to run in the same WSL
instance. Local mode exists for workflow verification only; quality evaluation must use the real
model endpoints.

## OpenAI-compatible embedding endpoint

Configure these values in `.env`:

```dotenv
EMBEDDING_PROVIDER=remote
EMBEDDING_BASE_URL=https://embedding.example.com
EMBEDDING_ENDPOINT=/v1/embeddings
EMBEDDING_API_KEY=replace-me
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
EMBEDDING_DIMENSION=1024
EMBEDDING_TIMEOUT_SECONDS=30
```

The endpoint must accept an OpenAI-compatible request:

```json
{"model":"Qwen/Qwen3-Embedding-0.6B","input":["first text","second text"]}
```

It must return `data[].embedding` and should return `data[].index`. The client verifies the batch
size and configured dimension. If the provider uses another vector dimension, update both
`EMBEDDING_DIMENSION` and the Milvus `dense_vector` schema before ingesting any documents.

## Reranker endpoint

Configure:

```dotenv
RERANKER_PROVIDER=remote
RERANKER_MODEL=Qwen/Qwen3-Reranker-0.6B
RERANKER_BASE_URL=https://reranker.example.com
RERANKER_ENDPOINT=/rerank
RERANKER_API_KEY=replace-me
RERANKER_TIMEOUT_SECONDS=30
```

The endpoint must accept:

```json
{"query":"question","documents":["candidate one","candidate two"],"top_k":2}
```

It must return `results` containing valid original document indexes and scores, for example:

```json
{"results":[{"index":1,"score":0.93},{"index":0,"score":0.12}]}
```

Run the contract probe before live E2E:

```bash
RUN_LIVE_INTEGRATION=1 python -m pytest -m integration -v
```

Reranking is configured and contract-tested in the current phase, but it is not yet part of the
production retrieval path. BM25, RRF and reranker wiring belong to Phase 3 and must not be claimed
as completed until the stage trace and ablation tests pass.

## Docker-free local workflow

```bash
cp .env.local.example .env
pip install -e ".[dev]"
python scripts/init_db.py
uvicorn src.main:app --host 127.0.0.1 --port 8002
```

This mode uses SQLite, an in-process vector index, deterministic hash embeddings and an extractive
local response. Restarting the API clears the in-process vector index; use a fresh local database
path for a new session until a vector-index rebuild job is implemented. It validates API
orchestration and locators, not retrieval quality.

For live E2E, configure PostgreSQL, Milvus, embedding, reranker and LLM endpoints, then run:

```bash
RUN_LIVE_E2E=1 python -m pytest tests/e2e/test_live_stack.py -v
```

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
{"query":"question","documents":["candidate one","candidate two"],"top_n":2}
```

It must return `results` containing valid original document indexes and scores, for example:

```json
{"results":[{"index":1,"score":0.93},{"index":0,"score":0.12}]}
```

Run the contract probe before live E2E:

```bash
RUN_LIVE_INTEGRATION=1 python -m pytest -m integration -v
```

The current retrieval path includes Dense, BM25, RRF and reranking. Passing the protocol contract
does not establish ranking quality; the Qwen reranker still needs the correct scoring template.

## Locally deployed Qwen generation

Use the HTTP-compatible provider (`LLM_PROVIDER=openai`) for the real Qwen endpoint.
`LLM_PROVIDER=local` selects a deterministic test double, not a locally deployed inference server.

```dotenv
LLM_TIMEOUT_SECONDS=180
LLM_REASONING_EFFORT=low
AGENT_MAX_ACTIVE_SECONDS=600
AGENT_SEARCH_TOP_K=3
AGENT_MAX_CONTEXT_TOKENS=65536
AGENT_MAX_TOTAL_TOKENS=128000
AGENT_MAX_OUTPUT_TOKENS=2048
DOCUMENT_ENRICHMENT_TIMEOUT_SECONDS=180
```

The async generation deadline is the smaller of the configured request timeout and remaining
active run time. The native Agent path has no hidden generation retries. Search keeps the wider
Dense/BM25/rerank candidate pool, but B0 and Agent tools return at most three chunks to the model.
Tools report requested/effective Top-K, and the model-visible tool schema advertises the cap.

`reasoning_effort=low` is an explicit HTTP parameter. A compatible schema or HTTP 200 alone does
not prove that the model's chat template honors it. Confirm against the actual served model;
leave the setting empty for services that do not support it. Rejections are not silently retried
without the requested setting. Reasoning output, if counted in completion usage by the service,
shares the output allowance; 2,048 is still a bounded starting value for reports.

Three different limits must be distinguished:

- The model context window is for one request's input plus output. On 2026-09-22 the actual
  Qwen service's `/models` returned `max_model_len=262144` (256K). Its OpenAPI schema also lists
  `low` for `reasoning_effort`; its effect on generation has not been benchmarked in this check.
- `AGENT_MAX_CONTEXT_TOKENS` is our application guard. It currently uses serialized UTF-8 bytes
  plus framing reserve, which overestimates tokenizer counts. The old 24,000 guard caused the
  September 22 `context_tokens` failures before requests reached the model.
- `AGENT_MAX_TOTAL_TOKENS` accounts for the whole run. Repeated history and evidence are counted
  on each model call. Missing usage retains a conservative reservation. A 256K model window
  does not remove this cumulative budget, tool/result limits, or the execution deadline.

New tasks use these settings; persisted checkpoints retain their original execution budgets.
The smoke runner now freezes the configured RunBudget instead of hardcoding the former 180s /
24,000 / 1,024 profile. Runtime identity includes reasoning effort, request timeout and search
Top-K. Results from changed settings require a new frozen experiment; existing v1 results stay intact.

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

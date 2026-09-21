# 内网共享推理与数据库服务使用指南

本文档是新项目和新 session 的复用入口。凭据只从运行环境或本项目 `.env` 读取，
不要把 key、密码、完整 `DATABASE_URL` 写入代码、日志、issue 或 prompt。

## 服务清单

| 服务 | 默认地址 | 协议/路径 | 必要变量 |
|---|---|---|---|
| LLM | `http://133.133.135.63:8000` | OpenAI 兼容 `/v1/chat/completions` | `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` |
| Embedding | `http://133.133.135.63:8001/v1` | OpenAI 兼容 `/embeddings` | `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL`, `EMBEDDING_API_KEY` |
| Reranker | `http://133.133.135.62:8001/v1` | `/rerank`（`/v1/rerank` 也可用） | `RERANKER_BASE_URL`, `RERANKER_MODEL`, `RERANKER_API_KEY` |
| PostgreSQL | `postgresql://133.133.135.64:5432/<db>` | PostgreSQL wire protocol | `DATABASE_URL`，以及拆分变量 |

当前 `.env` 已有 LLM 的 base URL，以及三项服务的 key；建议补充以下非秘密变量，
使新项目不必猜测端口、路径和模型名：

```dotenv
EMBEDDING_BASE_URL=http://133.133.135.63:8001/v1
EMBEDDING_MODEL=Qwen3-Embedding-4B
RERANKER_BASE_URL=http://133.133.135.62:8001/v1
RERANKER_MODEL=Qwen/Qwen3-Reranker-4B
```

`DATABASE_URL` 是应用连接的唯一推荐入口；`POSTGRES_DB`、`POSTGRES_USER`、
`POSTGRES_PASSWORD` 用于初始化工具或无法使用 DSN 的客户端。不要在代码中拼接并打印 DSN。

## 快速验证

在项目目录使用已安装本项目依赖的 `research_rag` 环境执行（脚本依赖 `requests`、
`python-dotenv` 和 `asyncpg`；本机 `migration_memory_system` 环境未安装这些依赖）：

```bash
conda run --no-capture-output -n research_rag \
  python scripts/check_shared_services.py --env-file .env
```

脚本只输出状态码、耗时、响应字段名、模型名和 embedding 维度。它会对 `/models` 做
1/2/4/8 并发基线，并各发一个最小功能请求。新项目可直接复制该脚本；只需把
`load_env_file` 替换为项目自己的 dotenv loader 即可。

内网调用前建议清除代理变量，否则 Python `urllib` 可能把 `133.133.*` 发给外部代理：

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    -u http_proxy -u https_proxy -u all_proxy \
    conda run --no-capture-output -n research_rag \
    python scripts/check_shared_services.py
```

## 标准调用

```python
import os
from openai import OpenAI

llm = OpenAI(base_url=os.environ["LLM_BASE_URL"].rstrip("/") + "/v1",
             api_key=os.environ["LLM_API_KEY"])
answer = llm.chat.completions.create(
    model=os.environ["LLM_MODEL"],
    messages=[{"role": "user", "content": "..."}],
)

embedding = OpenAI(base_url=os.environ["EMBEDDING_BASE_URL"],
                   api_key=os.getenv("EMBEDDING_API_KEY", "unused"))
vectors = embedding.embeddings.create(
    model=os.environ["EMBEDDING_MODEL"], input=["..."])
```

Reranker 使用 JSON POST：`{"model": RERANKER_MODEL, "query": "...", "documents": [...]}`，
请求 `RERANKER_BASE_URL + "/rerank"`，认证头为 `Authorization: Bearer <RERANKER_API_KEY>`。
服务返回 `results[].index` 和 `results[].relevance_score`；保留原始排序和模型名以便审计。

## 已验证基线（2026-09-20）

- LLM `/v1/chat/completions`：带 key 成功，返回模型 `Qwen3.8-27B`；1/2/4/8 并发均成功，
  本次最小请求约 0.51–0.58 秒/请求。
- Embedding `/v1/models` 和 `/v1/embeddings`：成功，返回模型 `Qwen3-Embedding-4B`、
  2560 维向量；1/2/4/8 并发均成功，单请求约 0.18 秒，4 并发出现约 1.2–1.6 秒抖动，
  客户端应设置超时、重试和有界并发。
- Reranker `/v1/rerank` 与 `/rerank`：均返回 200，模型为 `Qwen/Qwen3-Reranker-4B`，
  `max_model_len` 为 1024。功能请求成功；并发基线必须在目标机器清除代理后重新执行，
  不把代理超时误判为服务容量结论。
- PostgreSQL：只读连接成功，PostgreSQL 16.14；1/2/4/8 个并发连接本次均成功，约
  0.01–0.03 秒。生产应用仍应使用连接池，不要每条请求新建连接。

这些数字是连通性基线，不是容量承诺。上线前应按真实 prompt 长度、batch 大小和持续时间
做压测，并记录服务端 GPU/队列指标。

## 使用约束

1. LLM 请求固定 `temperature=0`（若任务允许），记录模型、请求 hash、响应 hash 和重试次数。
2. Embedding 记录返回维度；建 pgvector 索引前先确认维度，长文本按服务上下文窗口分块。
3. Reranker 的 `max_model_len=1024` 是输入约束，documents 过长时先截断或分块。
4. 所有服务失败都应显式标记为 unavailable/unknown，不能解释成“没有相关记忆”。
5. 日志只记录 URL 的 host/path、状态码、耗时和 hash；Authorization、密码和完整 DSN 必须脱敏。

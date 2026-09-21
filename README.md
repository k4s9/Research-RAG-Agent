# 科研 RAG Agent 系统

## 项目介绍

科研 RAG (Retrieval-Augmented Generation) Agent 系统是一个专为科研人员设计的智能知识管理和问答系统。它能够帮助科研人员上传、管理和检索科研文档，提取关键信息，并通过大语言模型提供智能问答服务。

## 功能特点

- **文档上传与解析**：支持 PDF、Markdown 等格式的文档上传和解析
- **智能切片**：自动将文档切分为合适大小的 chunks，便于检索
- **向量嵌入**：使用 Qwen3-Embedding 模型将文本转换为向量表示
- **检索**：当前生产路径为 Dense 检索，时间衰减为可选后处理；BM25 + RRF + Reranker 仍在 Phase 3 实现中
- **RAG 问答**：基于检索结果和大语言模型，提供智能问答服务
- **真实多轮会话**：会话 / run / step 落库，历史从数据库加载，重启后仍可回放
- **项目管理**：支持按项目组织和管理文档
- **Web 界面**：提供直观的 Gradio 界面，方便用户操作

## 技术栈

- **后端框架**：FastAPI
- **前端框架**：Gradio
- **向量数据库**：Milvus Standalone
- **关系数据库**：PostgreSQL
- **嵌入模型**：Qwen3-Embedding-0.6B
- **重排序模型**：Qwen3-Reranker-0.6B
- **大语言模型**：DeepSeek Chat API
- **文档解析**：PyMuPDF (fitz)

## 安装步骤

> 当前项目处于 experimental 状态。本地离线 E2E 已可运行，真实 PostgreSQL/Milvus/模型
> E2E 仍需按环境配置执行；BM25 + RRF + Reranker 生产链路尚未完成。

### 1. 克隆项目

```bash
git clone <repo-url>
cd research-rag-agent
```

### 2. 启动基础设施

使用 Docker Compose 启动 Milvus、PostgreSQL 等服务：

```bash
cp .env.example .env  # 编辑填入实际配置
docker compose up -d
```

### 3. 创建 Python 环境

```bash
conda create -n rag python=3.10 -y
conda activate rag
pip install -e ".[dev]"
```

### 4. 初始化数据库

```bash
python scripts/init_db.py      # 安全执行 Alembic 迁移，不会删除旧表
python scripts/init_milvus.py  # 创建 Milvus Collection
```

### 无 Docker 的 WSL 离线流程

```bash
cp .env.local.example .env
pip install -e ".[dev]"
python scripts/init_db.py
python -m pytest -m e2e -v
uvicorn src.main:app --host 127.0.0.1 --port 8002
```

Embedding/Reranker 远程 API 协议、环境变量和验证命令见
[`docs/model_api_configuration.md`](docs/model_api_configuration.md)。

### 5. 启动服务

#### 启动 API 服务

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8002
```

#### 启动 Gradio 前端

```bash
python frontend/app.py
```

## 使用方法

1. **访问 Gradio 界面**：在浏览器中打开 `http://localhost:7860`
2. **上传文档**：在 "文件上传" 标签页上传 PDF 或 Markdown 文件
3. **进行对话**：在 "对话" 标签页与系统进行交互，提问关于文档的问题
4. **搜索知识**：在 "搜索" 标签页搜索系统中的知识

## 会话与轨迹 API（Phase A）

`POST /api/v1/chat/message` 现在是真实多轮：`session_id` 不存在时自动创建会话，从
数据库加载最近 `CONTEXT_RECENT_TURNS` 轮历史注入 orchestrator，并把 user / assistant
两条消息落库。响应新增 `run_id` / `run_status`，既有字段保持不变。

| 接口 | 说明 |
|---|---|
| `POST /api/v1/chat/sessions` | 显式创建会话（title / project_ids 可选） |
| `GET /api/v1/chat/sessions` | 会话列表（按最近活跃排序，`limit` / `offset`） |
| `GET /api/v1/chat/sessions/{id}` | 真实消息列表，`limit` + `cursor` 游标向前翻页 |
| `GET /api/v1/chat/runs/{run_id}` | run 的每一步：tool、参数、结果摘要、`result_ref`、耗时 |
| `GET /api/v1/chat/sessions/{id}/runs` | 会话下的 run 列表 |
| `GET /api/v1/versions/{entity_id}/history` | 真实 `VersionLog` 历史（原为 mock） |

每次对话写入一条 `agent_run`（`completed` / `failed`）与若干 `agent_step`
（本阶段记录 `tool_call` / `observation`；`thought` / `plan` / `reflection` 属 Phase C）。
`agent_step.result_summary` 上限 800 字；当前只用于轨迹查询，模型上下文摘要化和完整结果寻址在 Phase C 完成。

升级执行 `alembic upgrade head`，新增 `20260919_01` 会补建历史会话并回填消息序号。
纯旧会话从 0 编号；混有已编号消息时，旧消息使用更小的序号（可能为负数），保留已发出的游标。
该迁移只修复数据，downgrade 不删除恢复的历史。

## 文档注册表与文献工具（Phase B）

上传接口现在会先创建 `ingest_batch`（可带 `note`），再走既有解析 / 分块 / 双存储流程，
并从首页启发式抽取 `title` / `authors` / `year` / `doc_type` / `outline`（PDF 用字号与位置，
Markdown 用 heading path + 行号）。随后由后台任务调用 LLM 生成 `summary` / `tags`；
失败只记 warning，文档保持 `ready`（摘要与标签是派生元数据，不是证据）。
后台摘要使用 HTTPX 异步请求，复用已声明的 HTTPX 依赖（现列入运行依赖）；
总超时由 `DOCUMENT_ENRICHMENT_TIMEOUT_SECONDS` 控制，默认 60 秒，超时会取消任务。
模型等待期间仍可查询或订正文档；已人工设置的 tags 不会被后台结果覆盖。

| 接口 | 说明 |
|---|---|
| `GET /api/v1/documents` | 文档注册表：按 `project_id` / `doc_type` / `tag` / `year` / `status` 过滤 + 分页 |
| `GET /api/v1/documents/{id}` | 元数据 + outline + chunk 统计 + `ingest_batch` |
| `PATCH /api/v1/documents/{id}` | 人工订正 title / doc_type / authors / year / venue / tags（不在工具面内，Agent 不能调用） |

Agent 工具面新增 `list_documents`、`get_document_outline`、`read_document_range`，
并为 `search_knowledge` 增加 `doc_types` / `tags` 过滤：先从数据库读取当前元数据确定 chunk 范围，
再同时约束 Dense/BM25 召回，避免在 Top-K 之后过滤导致漏检；PATCH 后无须重建向量索引。
文档列表使用项目关联的 EXISTS 条件，兼容 PostgreSQL JSON 字段且不重复列出跨项目文档。
`read_document_range` 按
`chunk_index` 返回**文档顺序连续**的片段（页码区间或 `section_path`），而不是检索命中的零散片段；
`max_chars` 严格限制返回正文（含片段间分隔符，不含 JSON 元数据）；首个 chunk 超长时也会截断，
不修改数据库原文。`chunks` 中的 `char_start` / `char_end` 标出原 chunk 内的字符范围。
`truncated=true` 时，将 `next_cursor` 的 `start_chunk_index` / `start_char` 原样传入下一次工具调用，
并保留相同 document/page/section 条件，即可无遗漏地续读；读完时 `next_cursor=null`。

PostgreSQL 的独立回归：在测试库配置 `TEST_POSTGRES_URL`（`postgresql+asyncpg://…`）后执行
`python -m pytest tests/integration/test_postgres_document_registry.py -q`。
用例在随机 schema 中验证 JSON 列和跨项目去重，结束时回滚 schema 与数据；未配置时明确跳过。

## 项目结构

```
research-rag-agent/
├── src/                # 源代码
│   ├── api/            # API 接口
│   ├── core/           # 核心功能
│   │   ├── ingest/     # 文档摄入
│   │   ├── retrieval/  # 检索功能
│   │   └── agent/      # Agent 功能
│   ├── db/             # 数据库相关
│   ├── schemas/        # 数据模型
│   └── utils/          # 工具函数
├── frontend/           # 前端界面
├── scripts/            # 脚本文件
├── data/               # 数据存储
├── volumes/            # Docker 卷
├── docker-compose.yml  # Docker 配置
└── README.md           # 项目文档
```

## 配置说明

项目配置通过 `.env` 文件管理，主要配置项包括：

- **数据库配置**：PostgreSQL 和 Milvus 的连接信息
- **模型配置**：嵌入模型和重排序模型的路径
- **LLM 配置**：DeepSeek API 的密钥和模型名称
- **检索配置**：检索参数和时间衰减参数
- **会话配置**：`CONTEXT_RECENT_TURNS`（注入的历史轮数，默认 6）、`AGENT_MAX_STEPS`（单次 run 步数上限，默认 12）

## 注意事项

- 确保 Docker 服务正常运行
- 确保 `.env` 文件中的 API 密钥正确配置
- 首次使用时，系统需要下载模型，可能需要一些时间
- 对于大型文档，解析和向量化可能需要较长时间

## 许可证

MIT

# 科研 RAG Agent 系统设计文档

> **版本**: v1.0 | **日期**: 2026-03-30 | **状态**: 初稿

---

## 目录

1. 需求概述
2. 技术栈选型
3. 数据模型设计
4. 核心模块设计
5. API 接口设计
6. 代码结构与规范
7. 分阶段开发路线图
8. 部署与运维建议

---

## 1. 需求概述

### 1.1 功能需求（用例）

| 编号 | 用例名称 | 描述 |
|------|---------|------|
| UC-01 | 上传文档 | 用户上传 PDF/PPT/Markdown 文件，系统自动解析、清洗、切片、向量化并存入知识库 |
| UC-02 | 多模态解析 | 系统对扫描件启用 OCR，提取表格为结构化格式，保留公式 LaTeX，过滤页眉/页脚 |
| UC-03 | 对话输入 | 用户通过自然语言输入导师意见、后续安排等内容，系统存储原文并自动提取长期记忆 |
| UC-04 | 记忆提取 | 系统自动从对话中提取里程碑、待办事项、决策点，存入长期记忆库 |
| UC-05 | 项目管理 | 为文档/对话关联一个或多个项目标签，支持按项目过滤或跨项目检索 |
| UC-06 | 混合检索 | BM25 + Dense Embedding 融合检索，经 RRF 合并后由 Reranker 重排序，支持时间衰减 |
| UC-07 | 多轮对话 | Agent 结合检索结果与历史对话上下文，辅助方案设计与任务安排 |
| UC-08 | 知识版本管理 | 新内容与旧内容冲突时标记旧内容为"过时"，保留历史版本，支持版本追溯 |
| UC-09 | 主动提醒 | 用户提出新思路时，系统主动检索相关历史内容并提醒潜在冲突或参考 |
| UC-10 | 知识浏览 | 用户可查看某条知识的演变历史、关联项目、状态（活跃/过时） |

### 1.2 非功能需求

| 类别 | 要求 | 说明 |
|------|------|------|
| 性能 | 检索响应 < 3s | 从用户发出查询到返回重排序结果（不含 LLM 生成）≤ 3 秒 |
| 性能 | 文档摄入吞吐 | 单文档（< 50 页 PDF）解析 + 向量化 < 60s |
| 可扩展性 | 知识库容量 | 初期支持 10 万级 chunk，后续可平滑迁移至 Milvus Standalone/Distributed |
| 可维护性 | 模块解耦 | 解析、检索、Agent 各模块独立，可单独替换或升级 |
| 可维护性 | 配置外部化 | 所有模型路径、API Key、数据库连接等通过环境变量/配置文件管理 |
| 安全性 | API Key 安全 | LLM API Key 不硬编码，通过 `.env` 管理，不进入版本控制 |
| 可靠性 | 数据持久化 | SQLite/PostgreSQL + Milvus 数据均有定期备份策略 |

---

## 2. 技术栈选型

### 2.1 总览

| 组件 | 首选方案 | 备选方案 | 选型理由 |
|------|---------|---------|---------|
| **后端框架** | FastAPI | Flask | 异步支持好、自动 OpenAPI 文档、类型注解友好 |
| **前端框架** | Gradio | Streamlit | 原生支持文件上传 + 对话界面、Python 原生、部署简单 |
| **向量数据库** | Milvus Standalone (Docker) | Qdrant | 原生支持 dense + sparse 混合检索，与 BGE-M3 生态集成紧密 |
| **关系数据库** | PostgreSQL | SQLite | 支持 JSON 字段、全文检索、事务可靠，生产就绪 |
| **Embedding** | BAAI/bge-m3 (本地部署) | text-embedding-3-small (OpenAI) | 同时支持 dense/sparse/multi-vector 三种检索，中英文优秀 |
| **Reranker** | BAAI/bge-reranker-v2-m3 (本地) | bge-reranker-v2-minicpm-layerwise | 轻量高效，多语言支持好，636MB 即可部署 |
| **LLM API** | DeepSeek Chat API | OpenAI GPT-4o | 性价比高、中文能力强、推理质量优秀 |
| **OCR** | PaddleOCR + PP-StructureV3 | — | 表格提取准确率达 93%+，支持公式→LaTeX，本地部署隐私可控 |
| **PDF 解析** | PyMuPDF (fitz) + PaddleOCR | pdfplumber | 原生 PDF 文本提取快，扫描件降级到 OCR |
| **PPT 解析** | python-pptx | LibreOffice CLI | 直接提取文本/表格/图片，无需外部依赖 |
| **任务队列** | 内置 asyncio + 后台线程 | Celery + Redis | 单机部署无需重量级队列，asyncio 足够 |
| **配置管理** | pydantic-settings | python-dotenv | 类型安全、自动校验、支持 .env 文件 |
| **日志** | loguru | logging (stdlib) | 开箱即用、格式美观、支持结构化日志与文件轮转 |
| **测试** | pytest + pytest-asyncio | unittest | 异步测试友好，fixture 机制强大 |

### 2.2 关键选型详解

**Embedding 选择 BGE-M3 的理由：** BGE-M3 可以同时执行 dense retrieval、multi-vector retrieval 和 sparse retrieval 三种检索功能[[1]](https://huggingface.co/BAAI/bge-m3)，支持从短句到长达 8192 token 的长文档输入[[1]](https://huggingface.co/BAAI/bge-m3)。这意味着我们可以用一个模型同时获得 dense embedding 和类 BM25 的 sparse embedding，无需额外维护 BM25 索引。官方推荐的使用流水线为：hybrid retrieval + re-ranking[[5]](https://build.nvidia.com/baai/bge-m3/modelcard)。

**向量数据库选择 Milvus 的理由：** Milvus 是分布式向量数据库，支持向量检索和混合检索，能结合传统索引与向量检索[[6]](https://medium.com/@oliversmithth852/comparative-evaluation-of-milvus-and-qdrant-for-retrieval-augmented-generation-rag-a101a72f93d1)。Milvus Standalone 是单机服务器部署，所有组件打包在单个 Docker 镜像中，如果有生产负载但不需要 Kubernetes，在内存充足的单机上运行是很好的选择[[1]](https://milvus.io/docs/install-overview.md)。Milvus 通过 BGEM3EmbeddingFunction 类与 BGE-M3 模型集成，处理 embedding 计算并以兼容格式返回用于索引和搜索[[10]](https://milvus.io/docs/embed-with-bgm-m3.md)。

**Reranker 选择 bge-reranker-v2-m3 的理由：** 这是 BAAI 推出的轻量级 reranker 模型，拥有强大的多语言能力，易于部署，推理速度快[[1]](https://bge-model.com/bge/bge_reranker_v2.html)。对于中英文场景推荐使用 bge-reranker-v2-m3，且其在效率方面表现突出[[1]](https://bge-model.com/bge/bge_reranker_v2.html)。

**OCR 选择 PaddleOCR + PP-StructureV3 的理由：** PaddleOCR 3.0 提供三大解决方案：PP-OCRv5 用于多语言文本识别，PP-StructureV3 用于分层文档解析，PP-ChatOCRv4 用于关键信息提取[[10]](https://arxiv.org/html/2507.05595v1)。其公式识别模型能够识别从文档图像中裁剪的包含公式的图片，并生成对应的 LaTeX 代码[[10]](https://arxiv.org/html/2507.05595v1)。PaddleOCR 处理大多数表格时表现优异，识别准确率高达 93%[[2]](https://dl.acm.org/doi/10.1145/3727353.3727391)。

---

## 3. 数据模型设计

### 3.1 实体关系图 (Mermaid ER Diagram)

```mermaid
erDiagram
    PROJECT {
        uuid id PK
        string name
        string description
        datetime created_at
        datetime updated_at
    }

    DOCUMENT {
        uuid id PK
        string filename
        string file_type "pdf/pptx/md"
        string file_path
        string status "processing/ready/failed"
        json parse_metadata
        datetime created_at
        datetime updated_at
    }

    CHUNK {
        uuid id PK
        uuid document_id FK
        int chunk_index
        text content
        string content_type "text/table/formula/image_ref"
        json metadata
        string version_status "active/outdated"
        uuid superseded_by FK "nullable"
        datetime created_at
    }

    CONVERSATION {
        uuid id PK
        uuid session_id
        string role "user/assistant"
        text content
        datetime timestamp
    }

    MEMORY {
        uuid id PK
        string memory_type "milestone/todo/decision/insight"
        text summary
        text original_context
        uuid source_conversation_id FK "nullable"
        uuid source_chunk_id FK "nullable"
        string version_status "active/outdated"
        uuid superseded_by FK "nullable"
        datetime created_at
        datetime resolved_at "nullable, for todos"
    }

    VERSION_LOG {
        uuid id PK
        uuid entity_id "chunk_id or memory_id"
        string entity_type "chunk/memory"
        string action "create/update/outdated/restore"
        text reason
        uuid actor_conversation_id FK "nullable"
        datetime created_at
    }

    PROJECT_DOCUMENT {
        uuid project_id FK
        uuid document_id FK
    }

    PROJECT_CONVERSATION {
        uuid project_id FK
        uuid conversation_id FK
    }

    PROJECT_MEMORY {
        uuid project_id FK
        uuid memory_id FK
    }

    PROJECT ||--o{ PROJECT_DOCUMENT : "has"
    PROJECT ||--o{ PROJECT_CONVERSATION : "has"
    PROJECT ||--o{ PROJECT_MEMORY : "has"
    DOCUMENT ||--o{ CHUNK : "contains"
    DOCUMENT ||--o{ PROJECT_DOCUMENT : "belongs_to"
    CONVERSATION ||--o{ PROJECT_CONVERSATION : "belongs_to"
    CONVERSATION ||--o{ MEMORY : "extracts"
    MEMORY ||--o{ PROJECT_MEMORY : "belongs_to"
    CHUNK ||--o| CHUNK : "superseded_by"
    MEMORY ||--o| MEMORY : "superseded_by"
    CHUNK ||--o{ VERSION_LOG : "history"
    MEMORY ||--o{ VERSION_LOG : "history"
```

### 3.2 Milvus Collection Schema

系统在 Milvus 中维护一个统一的 `knowledge_chunks` Collection，用于存储 chunk 和 memory 的向量表示：

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `id` | VARCHAR(36), PK | 对应 PostgreSQL 中 chunk/memory 的 UUID |
| `entity_type` | VARCHAR(16) | `"chunk"` 或 `"memory"` |
| `dense_vector` | FLOAT_VECTOR(1024) | BGE-M3 dense embedding |
| `sparse_vector` | SPARSE_FLOAT_VECTOR | BGE-M3 sparse embedding (类 BM25) |
| `project_ids` | ARRAY\<VARCHAR\> | 关联项目 ID 列表，用于过滤 |
| `version_status` | VARCHAR(16) | `"active"` / `"outdated"` |
| `content_type` | VARCHAR(16) | `"text"` / `"table"` / `"formula"` / `"milestone"` / `"todo"` / `"decision"` |
| `created_at` | INT64 | Unix 时间戳，用于时间衰减计算 |

### 3.3 关键字段说明

**version_status 字段**：当导师否定先前方向或用户更新决策时，系统将旧 chunk/memory 的 `version_status` 设为 `"outdated"`，同时在 `superseded_by` 中指向新的实体。检索时默认过滤 `"outdated"` 条目，但用户可选择包含。

**content_type 字段**：区分文本、表格、公式等内容类型，便于检索时按内容类型加权或过滤。

**project_ids 数组字段**：Milvus 支持 ARRAY 类型的标量过滤，可高效实现按项目过滤检索。

---

## 4. 核心模块设计

### 4.1 系统总体架构

```mermaid
graph TB
    subgraph Frontend["前端层 (Gradio)"]
        UI_Upload[文件上传]
        UI_Chat[对话窗口]
        UI_Project[项目管理]
        UI_Memory[记忆浏览]
    end

    subgraph API["API 层 (FastAPI)"]
        EP_Doc[/doc endpoints/]
        EP_Chat[/chat endpoints/]
        EP_Search[/search endpoints/]
        EP_Memory[/memory endpoints/]
        EP_Project[/project endpoints/]
    end

    subgraph Core["核心业务层"]
        Ingest[文档摄入引擎]
        MemExtract[记忆提取器]
        Retriever[混合检索器]
        Agent[对话 Agent]
        VersionMgr[版本管理器]
    end

    subgraph Infra["基础设施层"]
        PG[(PostgreSQL)]
        Milvus[(Milvus)]
        LLM_API[LLM API<br/>DeepSeek/OpenAI]
        OCR[PaddleOCR]
        EmbModel[BGE-M3]
        Reranker[BGE-Reranker]
    end

    Frontend --> API
    API --> Core
    Ingest --> OCR
    Ingest --> EmbModel
    Ingest --> PG
    Ingest --> Milvus
    MemExtract --> LLM_API
    MemExtract --> PG
    MemExtract --> Milvus
    Retriever --> Milvus
    Retriever --> Reranker
    Agent --> Retriever
    Agent --> LLM_API
    Agent --> VersionMgr
    VersionMgr --> PG
    VersionMgr --> Milvus
```

### 4.2 文档摄入流水线

```mermaid
flowchart TD
    A[用户上传文件] --> B{文件类型?}
    B -->|PDF| C[PyMuPDF 提取文本]
    B -->|PPTX| D[python-pptx 提取]
    B -->|Markdown| E[直接读取文本]

    C --> F{是否为扫描件?<br/>文本密度检测}
    F -->|是: 文本密度低| G[PaddleOCR + PP-StructureV3]
    F -->|否: 原生文本| H[结构化解析]

    D --> H
    E --> H
    G --> H

    H --> I[页眉/页脚过滤<br/>前后 1/5 区域规则]
    I --> J[内容分类]

    J --> J1[纯文本块]
    J --> J2[表格 → Markdown/JSON]
    J --> J3[公式 → LaTeX]
    J --> J4[图片 → 保存本地路径]

    J1 --> K[智能切片<br/>按语义段落, 512-1024 tokens]
    J2 --> K
    J3 --> K
    J4 --> K

    K --> L[BGE-M3 向量化<br/>dense + sparse]
    L --> M[写入 Milvus]
    L --> N[写入 PostgreSQL<br/>元数据 + 原文]
    M --> O[标记文档状态: ready]
    N --> O
```

**核心处理逻辑说明：**

**扫描件检测**：对 PDF 页面提取文本后，计算文本密度（字符数 / 页面面积）。若密度低于阈值（如每页 < 50 字符），则判定为扫描件，切换到 OCR 流水线。

**页眉/页脚过滤**：对每个页面的文本块，根据其 y 坐标位置判断——位于页面上 1/5 区域的文本标记为潜在页眉，下 1/5 区域标记为潜在页脚。结合跨页一致性检测（若多页出现相同文本则确认为页眉/页脚）进行过滤。

**表格提取**：结构识别模型以 HTML 格式输出表格结构[[10]](https://arxiv.org/html/2507.05595v1)。系统将 HTML 表格转换为 Markdown 表格格式存储，同时保留 JSON 结构化表示作为元数据。

**公式提取**：公式识别模型是 PP-FormulaNet 的增强版本，能生成对应 LaTeX 代码，token 长度增至 2560 以处理复杂多行公式[[10]](https://arxiv.org/html/2507.05595v1)。

**切片策略**：采用语义感知切片，以段落/节为基本单位，目标 chunk 大小 512–1024 tokens，允许 128 tokens 的重叠窗口。表格和公式作为独立 chunk 保留完整性。

### 4.3 对话记忆提取

```mermaid
flowchart TD
    A[用户输入对话内容] --> B[存储原始消息<br/>+ 时间戳到 PostgreSQL]
    B --> C[发送到 LLM 进行<br/>结构化信息提取]
    C --> D{提取结果}

    D --> D1[里程碑<br/>Milestone]
    D --> D2[待办事项<br/>Todo]
    D --> D3[决策点<br/>Decision]
    D --> D4[关键洞察<br/>Insight]

    D1 --> E[冲突检测:<br/>与现有 Memory 语义对比]
    D2 --> E
    D3 --> E
    D4 --> E

    E --> F{是否与旧条目冲突?}
    F -->|是| G[标记旧条目为 outdated<br/>记录 VERSION_LOG]
    F -->|否| H[直接创建新 Memory]
    G --> H

    H --> I[BGE-M3 向量化<br/>写入 Milvus + PostgreSQL]
```

**LLM 提取 Prompt 设计（核心模板）：**

```text
你是一个科研项目助手。请从以下对话内容中提取结构化信息。

对话内容:
---
{user_input}
---

请以 JSON 格式输出，包含以下字段（如果有的话）：
{
  "milestones": [{"summary": "...", "date_mentioned": "..."}],
  "todos": [{"task": "...", "deadline": "...", "priority": "high/medium/low"}],
  "decisions": [{"decision": "...", "rationale": "...", "decided_by": "..."}],
  "insights": [{"content": "...", "context": "..."}]
}

如果某类信息不存在，返回空数组。
```

### 4.4 检索与重排序

```mermaid
flowchart TD
    A[用户查询 Query] --> B[Query 改写/扩展<br/>LLM 辅助]
    B --> C[BGE-M3 编码<br/>获取 dense + sparse 向量]

    C --> D[Milvus Dense 检索<br/>Top-K₁ = 50]
    C --> E[Milvus Sparse 检索<br/>Top-K₂ = 50]

    D --> F[RRF 融合排序<br/>k = 60]
    E --> F

    F --> G[应用过滤条件]
    G --> G1[项目过滤<br/>project_ids ∈ selected]
    G --> G2[版本过滤<br/>默认 active only]
    G --> G3[内容类型过滤<br/>可选]

    G1 --> H[取 Top-N = 20<br/>候选集]
    G2 --> H
    G3 --> H

    H --> I[时间衰减加权<br/>score × decay_factor]
    I --> J[BGE-Reranker-v2-m3<br/>精排序 Top-N → Top-K]
    J --> K[返回 Top-K = 5~10<br/>最终结果]
```

**RRF (Reciprocal Rank Fusion) 公式：**

$$
\text{RRF\_score}(d) = \sum_{r \in \{dense, sparse\}} \frac{1}{k + \text{rank}_r(d)}
$$

其中 \(k = 60\) 为平滑常数。

**时间衰减公式：**

$$
\text{decay}(t) = \lambda + (1 - \lambda) \cdot e^{-\alpha \cdot \Delta t}
$$

其中 \(\Delta t\) 为文档创建时间到当前的天数差，\(\alpha = 0.01\) 控制衰减速率，\(\lambda = 0.3\) 为最低保留权重（确保老文档不会完全被忽略）。

### 4.5 Agent 工作流

```mermaid
flowchart TD
    A[用户发送消息] --> B[意图识别<br/>LLM 分类]

    B --> B1[问答/检索型]
    B --> B2[对话输入型<br/>记录信息]
    B --> B3[方案设计型<br/>需要创作]
    B --> B4[任务安排型]

    B1 --> C[混合检索流水线]
    C --> D[构建 RAG Prompt<br/>检索结果 + 历史对话]

    B2 --> E[记忆提取流水线]
    E --> F[确认提取结果]

    B3 --> G[检索相关知识]
    G --> H[主动冲突检测:<br/>是否存在相关旧方案?]
    H --> H1{发现冲突?}
    H1 -->|是| I["提醒用户:<br/>您之前有类似方案,<br/>导师曾指出…是否调整?"]
    H1 -->|否| J[基于检索结果<br/>辅助设计方案]
    I --> J

    B4 --> K[检索现有待办<br/>+ 项目进展]
    K --> L[LLM 生成任务安排<br/>考虑优先级和时间]

    D --> M[LLM 生成回答]
    F --> M
    J --> M
    L --> M

    M --> N[更新对话历史]
    N --> O[返回响应给用户]
```

**主动提醒机制实现：**

当 Agent 识别到用户在讨论方案设计或提出新思路时（通过意图识别判定为 B3 类型），执行以下步骤：

第一步，从用户消息中提取关键概念和方法关键词。第二步，以这些关键词进行检索，筛选 `content_type` 为 `"decision"` 或 `"insight"` 的历史记录。第三步，检查检索结果中是否存在 `version_status = "outdated"` 的条目，如果存在，加载其被标记为 outdated 的原因（从 `VERSION_LOG` 获取）。第四步，在 LLM prompt 中注入冲突信息，引导 LLM 生成包含提醒的回复。

**多轮对话上下文管理：**

系统维护一个滑动窗口（最近 10 轮对话）作为短期上下文，同时在每轮对话中基于当前话题进行检索，注入相关的长期记忆（milestone、decision）作为补充上下文。LLM prompt 结构如下：

```text
[System] 你是一个科研助手，帮助用户管理项目知识和进行方案设计。

[检索到的相关知识]
{retrieved_chunks}

[长期记忆 - 相关里程碑和决策]
{relevant_memories}

[最近对话历史]
{recent_conversation}

[用户消息]
{user_message}
```

---

## 5. API 接口设计

### 5.1 端点总览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/projects` | 创建项目 |
| GET | `/api/v1/projects` | 获取项目列表 |
| POST | `/api/v1/documents/upload` | 上传文档 |
| GET | `/api/v1/documents/{doc_id}/status` | 查询文档解析状态 |
| POST | `/api/v1/chat/message` | 发送对话消息（含 Agent 响应） |
| GET | `/api/v1/chat/sessions/{session_id}` | 获取对话历史 |
| POST | `/api/v1/search` | 执行混合检索 |
| GET | `/api/v1/memories` | 获取记忆列表（支持过滤） |
| PATCH | `/api/v1/memories/{memory_id}/status` | 更新记忆状态 |
| GET | `/api/v1/versions/{entity_id}/history` | 获取知识版本历史 |

### 5.2 核心接口请求/响应示例

**POST `/api/v1/documents/upload`**

```
Content-Type: multipart/form-data
```

请求字段：

```json
{
  "file": "<binary>",
  "project_ids": ["proj-uuid-1", "proj-uuid-2"],
  "description": "2026年3月组会报告"
}
```

响应 (202 Accepted)：

```json
{
  "document_id": "doc-uuid-xxx",
  "status": "processing",
  "message": "文档已提交解析，请通过 /documents/{doc_id}/status 查询进度"
}
```

**POST `/api/v1/chat/message`**

请求：

```json
{
  "session_id": "session-uuid-xxx",
  "project_ids": ["proj-uuid-1"],
  "message": "导师今天说我们的注意力机制方案需要改为用 Flash Attention，之前的标准实现效率太低了",
  "include_outdated": false
}
```

响应 (200 OK)：

```json
{
  "session_id": "session-uuid-xxx",
  "response": "我已记录导师的意见。我注意到您之前在3月15日的讨论中提到过使用标准Multi-Head Attention的方案，该条记录已标记为过时。关于Flash Attention的集成，建议您可以参考...",
  "extracted_memories": [
    {
      "memory_id": "mem-uuid-xxx",
      "type": "decision",
      "summary": "导师决定将注意力机制从标准实现改为 Flash Attention",
      "outdated_references": ["mem-uuid-old"]
    }
  ],
  "retrieved_context": [
    {
      "chunk_id": "chunk-uuid-xxx",
      "content_preview": "Flash Attention 通过分块计算...",
      "source": "attention_survey.pdf",
      "relevance_score": 0.92
    }
  ]
}
```

**POST `/api/v1/search`**

请求：

```json
{
  "query": "之前导师对 Transformer 方案的意见",
  "project_ids": ["proj-uuid-1"],
  "cross_project": false,
  "include_outdated": false,
  "content_types": ["text", "decision", "milestone"],
  "top_k": 5,
  "time_decay_enabled": true
}
```

响应 (200 OK)：

```json
{
  "results": [
    {
      "id": "chunk-uuid-xxx",
      "entity_type": "memory",
      "content": "导师认为当前 Transformer 架构的计算开销过大...",
      "content_type": "decision",
      "version_status": "active",
      "source": "conversation @ 2026-03-25",
      "score": 0.89,
      "project_ids": ["proj-uuid-1"],
      "created_at": "2026-03-25T14:30:00Z"
    }
  ],
  "total": 5,
  "query_time_ms": 245
}
```

**GET `/api/v1/versions/{entity_id}/history`**

响应 (200 OK)：

```json
{
  "entity_id": "mem-uuid-xxx",
  "entity_type": "memory",
  "current_status": "active",
  "history": [
    {
      "version": 3,
      "action": "create",
      "summary": "改用 Flash Attention 方案",
      "reason": "导师指示更换",
      "created_at": "2026-03-28T10:00:00Z"
    },
    {
      "version": 2,
      "action": "outdated",
      "summary": "使用标准 Multi-Head Attention",
      "reason": "导师认为效率太低，被 v3 取代",
      "created_at": "2026-03-28T10:00:00Z"
    },
    {
      "version": 1,
      "action": "create",
      "summary": "使用标准 Multi-Head Attention",
      "reason": null,
      "created_at": "2026-03-15T09:00:00Z"
    }
  ]
}
```

---

## 6. 代码结构与规范

### 6.1 项目目录树

```
research-rag-agent/
├── src/
│   ├── __init__.py
│   ├── main.py                     # FastAPI 入口
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py             # pydantic-settings 配置
│   ├── api/
│   │   ├── __init__.py
│   │   ├── router.py               # 总路由注册
│   │   ├── documents.py            # 文档上传/状态接口
│   │   ├── chat.py                 # 对话接口
│   │   ├── search.py               # 检索接口
│   │   ├── memories.py             # 记忆管理接口
│   │   ├── projects.py             # 项目管理接口
│   │   └── versions.py             # 版本追溯接口
│   ├── core/
│   │   ├── __init__.py
│   │   ├── ingest/
│   │   │   ├── __init__.py
│   │   │   ├── pipeline.py         # 摄入流水线编排
│   │   │   ├── pdf_parser.py       # PDF 解析 (PyMuPDF + OCR fallback)
│   │   │   ├── pptx_parser.py      # PPTX 解析
│   │   │   ├── markdown_parser.py  # Markdown 解析
│   │   │   ├── ocr_engine.py       # PaddleOCR 封装
│   │   │   ├── table_extractor.py  # 表格结构化提取
│   │   │   ├── formula_extractor.py# 公式 LaTeX 提取
│   │   │   ├── cleaner.py          # 清洗 (页眉/页脚过滤等)
│   │   │   └── chunker.py          # 语义切片
│   │   ├── retrieval/
│   │   │   ├── __init__.py
│   │   │   ├── embedder.py         # BGE-M3 embedding 封装
│   │   │   ├── hybrid_search.py    # 混合检索 (dense + sparse)
│   │   │   ├── rrf_fusion.py       # RRF 融合
│   │   │   ├── reranker.py         # BGE-Reranker 封装
│   │   │   └── time_decay.py       # 时间衰减计算
│   │   ├── agent/
│   │   │   ├── __init__.py
│   │   │   ├── orchestrator.py     # Agent 主控 (意图识别+调度)
│   │   │   ├── memory_extractor.py # 对话→记忆 LLM 提取
│   │   │   ├── conflict_detector.py# 知识冲突检测
│   │   │   ├── proactive_reminder.py# 主动提醒逻辑
│   │   │   └── prompt_templates.py # Prompt 模板集中管理
│   │   └── version/
│   │       ├── __init__.py
│   │       └── version_manager.py  # 版本标记、追溯
│   ├── db/
│   │   ├── __init__.py
│   │   ├── postgres.py             # PostgreSQL 连接 & ORM (SQLAlchemy)
│   │   ├── milvus_client.py        # Milvus 连接封装
│   │   └── models.py               # SQLAlchemy ORM 模型
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── document.py             # Pydantic request/response schemas
│   │   ├── chat.py
│   │   ├── search.py
│   │   ├── memory.py
│   │   ├── project.py
│   │   └── version.py
│   └── utils/
│       ├── __init__.py
│       ├── file_utils.py           # 文件处理工具
│       ├── text_utils.py           # 文本处理工具
│       └── llm_client.py           # LLM API 统一封装
├── frontend/
│   └── app.py                      # Gradio 前端入口
├── tests/
│   ├── __init__.py
│   ├── conftest.py                 # 共享 fixture
│   ├── test_ingest/
│   │   ├── test_pdf_parser.py
│   │   ├── test_chunker.py
│   │   └── test_cleaner.py
│   ├── test_retrieval/
│   │   ├── test_hybrid_search.py
│   │   └── test_rrf_fusion.py
│   ├── test_agent/
│   │   ├── test_memory_extractor.py
│   │   └── test_conflict_detector.py
│   └── test_api/
│       ├── test_documents.py
│       └── test_chat.py
├── scripts/
│   ├── init_db.py                  # 数据库初始化
│   ├── init_milvus.py              # Milvus Collection 创建
│   └── backup.sh                   # 备份脚本
├── data/
│   ├── uploads/                    # 原始上传文件
│   └── images/                     # 提取的图片存储
├── docker-compose.yml              # Milvus + PostgreSQL
├── Dockerfile                      # 应用容器 (可选)
├── pyproject.toml                  # 项目配置 + 依赖
├── .env.example                    # 环境变量模板
├── .pre-commit-config.yaml         # pre-commit hooks
└── README.md
```

### 6.2 编码规范

**语言版本**：Python 3.10+，充分利用 `match/case`、`ParamSpec`、`TypeAlias` 等特性。

**类型注解**：所有函数签名必须包含完整的类型注解。使用 `from __future__ import annotations` 启用延迟求值。

**格式化与检查**：使用 `ruff` 统一 lint + format（替代 black + flake8 + isort），规则配置在 `pyproject.toml` 中。

**pyproject.toml 核心配置片段：**

```toml
[project]
name = "research-rag-agent"
version = "0.1.0"
requires-python = ">=3.10"

[tool.ruff]
target-version = "py310"
line-length = 100
select = ["E", "F", "I", "N", "W", "UP", "ANN", "B", "A", "COM"]

[tool.ruff.format]
quote-style = "double"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

**配置管理 (pydantic-settings)**：

```python
# src/config/settings.py
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Database
    postgres_url: str = "postgresql+asyncpg://user:pass@localhost:5432/rag_db"
    milvus_uri: str = "http://localhost:19530"

    # Models
    bge_m3_model_path: str = "BAAI/bge-m3"
    reranker_model_path: str = "BAAI/bge-reranker-v2-m3"

    # LLM
    llm_provider: str = "deepseek"  # or "openai"
    llm_api_key: str = ""
    llm_model_name: str = "deepseek-chat"
    llm_base_url: str = "https://api.deepseek.com"

    # Retrieval
    dense_top_k: int = 50
    sparse_top_k: int = 50
    rrf_k: int = 60
    rerank_top_n: int = 20
    final_top_k: int = 5
    time_decay_alpha: float = 0.01
    time_decay_lambda: float = 0.3

    # OCR
    use_ocr: bool = True
    ocr_lang: str = "ch"

    # Paths
    upload_dir: str = "./data/uploads"
    image_dir: str = "./data/images"
```

**错误处理与日志 (loguru)**：

```python
# src/main.py
from loguru import logger
import sys

logger.remove()  # 移除默认 handler
logger.add(sys.stderr, level="INFO", format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{line} | {message}")
logger.add("logs/app_{time:YYYY-MM-DD}.log", rotation="100 MB", retention="30 days", level="DEBUG")
```

所有模块使用 `from loguru import logger` 统一记录日志。API 层通过 FastAPI 的异常处理中间件统一捕获异常，返回结构化错误响应：

```json
{
  "error": {
    "code": "DOCUMENT_PARSE_FAILED",
    "message": "PDF 解析失败: 文件可能已损坏",
    "details": "..."
  }
}
```

---

## 7. 分阶段开发路线图

### 7.1 总览时间线

```mermaid
gantt
    title 研发路线图
    dateFormat  YYYY-MM-DD
    axisFormat  %m/%d

    section MVP (Phase 0)
    环境搭建 & 基础框架              :p0_1, 2026-04-01, 4d
    PDF/MD 解析 + 切片               :p0_2, after p0_1, 5d
    BGE-M3 embedding + Milvus 集成    :p0_3, after p0_1, 4d
    基础 dense 检索                   :p0_4, after p0_2, 3d
    单轮 RAG 问答                     :p0_5, after p0_4, 4d
    Gradio 基础 UI                    :p0_6, after p0_5, 3d
    MVP 集成测试                      :p0_7, after p0_6, 2d

    section 迭代一 (Phase 1)
    PPTX + OCR 解析                   :p1_1, after p0_7, 5d
    对话输入 + 记忆提取               :p1_2, after p0_7, 6d
    项目管理 (标签/过滤)              :p1_3, after p1_1, 4d
    BM25 sparse + RRF 融合            :p1_4, after p1_1, 4d
    Reranker + 时间衰减               :p1_5, after p1_4, 3d
    迭代一集成测试                     :p1_6, after p1_5, 3d

    section 迭代二 (Phase 2)
    知识版本管理                       :p2_1, after p1_6, 5d
    冲突检测 + 主动提醒               :p2_2, after p2_1, 5d
    多轮对话 Agent                     :p2_3, after p1_6, 7d
    记忆浏览 + 版本追溯 UI            :p2_4, after p2_2, 4d
    端到端测试 + 优化                  :p2_5, after p2_4, 4d
```

### 7.2 各阶段详情

**MVP (Phase 0) — 约 3.5 周**

| 任务 | 预估工时 | 关键交付物 |
|------|---------|-----------|
| 环境搭建：Docker Compose (Milvus + PG)、项目骨架 | 4d | docker-compose.yml、项目目录 |
| PDF/Markdown 解析 + 页眉页脚过滤 + 切片 | 5d | pdf_parser.py, cleaner.py, chunker.py |
| BGE-M3 本地加载 + Dense Embedding | 4d | embedder.py, milvus_client.py |
| 基础 Dense 检索接口 | 3d | hybrid_search.py (仅 dense) |
| 单轮 RAG 问答 (LLM API 集成) | 4d | llm_client.py, orchestrator.py (简化版) |
| Gradio UI (上传 + 问答) | 3d | frontend/app.py |
| 集成测试 | 2d | tests/ |

MVP 验收标准：用户可上传 PDF/Markdown → 系统解析入库 → 用户提问 → 返回基于检索的 LLM 回答。

**迭代一 (Phase 1) — 约 3.5 周**

| 任务 | 预估工时 | 关键交付物 |
|------|---------|-----------|
| PPTX 解析 + PaddleOCR 集成 | 5d | pptx_parser.py, ocr_engine.py |
| 对话输入存储 + LLM 记忆提取 | 6d | memory_extractor.py |
| 项目创建/关联 + 检索过滤 | 4d | projects.py, 检索 filter 逻辑 |
| BGE-M3 Sparse Embedding + RRF 融合 | 4d | rrf_fusion.py, 更新 hybrid_search.py |
| Reranker 集成 + 时间衰减 | 3d | reranker.py, time_decay.py |
| 集成测试 | 3d | 更新 tests/ |

迭代一验收标准：支持三种文件格式 + OCR → 对话输入可自动提取记忆 → 按项目过滤 → 混合检索带 rerank。

**迭代二 (Phase 2) — 约 3.5 周**

| 任务 | 预估工时 | 关键交付物 |
|------|---------|-----------|
| 知识版本管理 (标记过时、版本链) | 5d | version_manager.py |
| 冲突检测 + 主动提醒 | 5d | conflict_detector.py, proactive_reminder.py |
| 多轮对话 Agent (上下文管理、意图路由) | 7d | orchestrator.py (完整版) |
| 记忆浏览 + 版本追溯 UI | 4d | Gradio 新 Tab |
| 端到端测试 + 性能优化 | 4d | 全量测试 |

迭代二验收标准：版本追溯可视化 → Agent 能主动提醒冲突 → 多轮对话流畅。

---

## 8. 部署与运维建议

### 8.1 物理机部署架构

```
┌─────────────────────────────────────────────────┐
│                  物理服务器                        │
│                                                   │
│  ┌──────────────────────────────────────────┐    │
│  │  Docker Compose                           │    │
│  │  ┌──────────┐  ┌──────────┐              │    │
│  │  │ Milvus   │  │PostgreSQL│              │    │
│  │  │ Standalone│  │  15      │              │    │
│  │  │ :19530   │  │ :5432    │              │    │
│  │  └──────────┘  └──────────┘              │    │
│  └──────────────────────────────────────────┘    │
│                                                   │
│  ┌──────────────────────────────────────────┐    │
│  │  Python 应用 (venv / conda)               │    │
│  │  ┌──────────┐  ┌──────────┐              │    │
│  │  │ FastAPI  │  │ Gradio   │              │    │
│  │  │ :8000    │  │ :7860    │              │    │
│  │  └──────────┘  └──────────┘              │    │
│  │  ┌──────────┐  ┌──────────┐              │    │
│  │  │ BGE-M3   │  │ Reranker │              │    │
│  │  │ (GPU)    │  │ (GPU)    │              │    │
│  │  └──────────┘  └──────────┘              │    │
│  │  ┌──────────┐                             │    │
│  │  │PaddleOCR │                             │    │
│  │  │ (GPU/CPU)│                             │    │
│  │  └──────────┘                             │    │
│  └──────────────────────────────────────────┘    │
│                                                   │
│  Nginx (可选, 反向代理 :80 → :7860/:8000)         │
└─────────────────────────────────────────────────┘
```

### 8.2 Docker Compose 配置

```yaml
# docker-compose.yml
version: '3.8'

services:
  milvus:
    image: milvusdb/milvus:v2.4-latest
    container_name: milvus-standalone
    command: ["milvus", "run", "standalone"]
    environment:
      ETCD_USE_EMBED: "true"
      ETCD_DATA_DIR: "/var/lib/milvus/etcd"
    ports:
      - "19530:19530"
      - "9091:9091"
    volumes:
      - ./volumes/milvus:/var/lib/milvus
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9091/healthz"]
      interval: 30s
      timeout: 10s
      retries: 5

  postgres:
    image: postgres:16-alpine
    container_name: postgres
    environment:
      POSTGRES_USER: raguser
      POSTGRES_PASSWORD: ${PG_PASSWORD:-changeme}
      POSTGRES_DB: rag_db
    ports:
      - "5432:5432"
    volumes:
      - ./volumes/postgres:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U raguser -d rag_db"]
      interval: 10s
      timeout: 5s
      retries: 5
```

### 8.3 环境变量配置示例 (.env.example)

```bash
# === Database ===
POSTGRES_URL=postgresql+asyncpg://raguser:changeme@localhost:5432/rag_db
MILVUS_URI=http://localhost:19530

# === LLM API ===
LLM_PROVIDER=deepseek
LLM_API_KEY=sk-your-deepseek-api-key
LLM_MODEL_NAME=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com

# === Models (local paths or HuggingFace IDs) ===
BGE_M3_MODEL_PATH=BAAI/bge-m3
RERANKER_MODEL_PATH=BAAI/bge-reranker-v2-m3

# === Retrieval Parameters ===
DENSE_TOP_K=50
SPARSE_TOP_K=50
RRF_K=60
RERANK_TOP_N=20
FINAL_TOP_K=5
TIME_DECAY_ALPHA=0.01
TIME_DECAY_LAMBDA=0.3

# === OCR ===
USE_OCR=true
OCR_LANG=ch

# === Paths ===
UPLOAD_DIR=./data/uploads
IMAGE_DIR=./data/images

# === Server ===
API_HOST=0.0.0.0
API_PORT=8000
GRADIO_PORT=7860
```

### 8.4 启动步骤

```bash
# 1. 克隆项目
git clone <repo-url> && cd research-rag-agent

# 2. 启动基础设施
cp .env.example .env  # 编辑填入实际配置
docker compose up -d

# 3. 创建 Python 环境
conda create -n rag python=3.10 -y
conda activate rag
pip install -e ".[dev]"

# 4. 初始化数据库
python scripts/init_db.py      # 创建 PostgreSQL 表
python scripts/init_milvus.py  # 创建 Milvus Collection

# 5. 首次运行下载模型 (BGE-M3 ~1.2GB, Reranker ~636MB)
python -c "from FlagEmbedding import BGEM3FlagModel; BGEM3FlagModel('BAAI/bge-m3')"

# 6. 启动服务
# 终端 1: API 服务
uvicorn src.main:app --host 0.0.0.0 --port 8000

# 终端 2: Gradio 前端
python frontend/app.py
```

### 8.5 监控与备份策略

**监控方案：**

系统使用 loguru 的结构化日志 + 文件轮转（100MB/文件，保留 30 天）。关键指标通过 FastAPI middleware 记录到日志：请求响应时间、检索耗时、LLM 调用耗时。Milvus 自带 WebUI（端口 9091），可监控 Collection 大小、查询性能。PostgreSQL 通过 `pg_stat_statements` 监控慢查询。

**备份策略：**

```bash
#!/bin/bash
# scripts/backup.sh — 建议通过 crontab 每日执行
BACKUP_DIR="/backup/rag-$(date +%Y%m%d)"
mkdir -p "$BACKUP_DIR"

# PostgreSQL 备份
docker exec postgres pg_dump -U raguser rag_db > "$BACKUP_DIR/pg_dump.sql"

# Milvus 数据备份 (复制 volume)
cp -r ./volumes/milvus "$BACKUP_DIR/milvus_data"

# 上传文件备份
cp -r ./data "$BACKUP_DIR/data"

# 保留最近 30 天备份
find /backup -maxdepth 1 -type d -mtime +30 -exec rm -rf {} \;

echo "Backup completed: $BACKUP_DIR"
```

推荐 crontab 配置：`0 3 * * * /path/to/scripts/backup.sh >> /var/log/rag-backup.log 2>&1`

### 8.6 硬件建议

| 组件 | 最低配置 | 推荐配置 |
|------|---------|---------|
| CPU | 8 核 | 16 核 |
| RAM | 32 GB | 64 GB |
| GPU | NVIDIA RTX 3060 (12GB) | NVIDIA RTX 4090 (24GB) |
| 存储 | 256 GB SSD | 1 TB NVMe SSD |

GPU 主要用于 BGE-M3 embedding 推理和 Reranker 推理。如无 GPU，BGE-M3 和 Reranker 可在 CPU 上运行（速度较慢约 5–10x），或切换到云端 embedding API（如 OpenAI text-embedding-3-small）以卸载计算。PaddleOCR 支持 CPU 推理但 GPU 可大幅加速。

---

> **文档终**
>
> 本设计文档覆盖了从需求到部署的完整链路。开发团队可基于此文档直接启动 MVP 开发，并按路线图逐步迭代。如有技术细节需进一步细化（如具体 Prompt 工程、Gradio 页面布局等），可在各迭代启动前补充子设计文档。

---
Learn more:
1. [BAAI/bge-m3 · Hugging Face](https://huggingface.co/BAAI/bge-m3)
2. [Milvus vs Qdrant | Vector Database Comparison](https://zilliz.com/comparison/milvus-vs-qdrant)
3. [Having trouble doing complex Table Recognition and Extraction from image to excel · PaddlePaddle/PaddleOCR · Discussion #15090](https://github.com/PaddlePaddle/PaddleOCR/discussions/15090)
4. [BGE-Reranker-v2 — BGE documentation](https://bge-model.com/bge/bge_reranker_v2.html)
5. [Overview of Milvus Deployment Options | Milvus Documentation](https://milvus.io/docs/install-overview.md)
6. [BGE-M3 — BGE documentation](https://bge-model.com/bge/bge_m3.html)
7. [Qdrant vs Milvus: Which Vector Database Should You Choose?](https://www.f22labs.com/blogs/qdrant-vs-milvus-which-vector-database-should-you-choose/)
8. [Automatic Extraction of PDF Table Data Based on Deep Learning | Proceedings of the 2025 4th International Conference on Big Data, Information and Computer Network](https://dl.acm.org/doi/10.1145/3727353.3727391)
9. [BAAI/bge-reranker-v2-m3 · Hugging Face](https://huggingface.co/BAAI/bge-reranker-v2-m3)
10. [Run Milvus Lite Locally | Milvus Documentation](https://milvus.io/docs/milvus_lite.md)
11. [BGE m3 | Products](https://docs.ionos.com/cloud/ai/ai-model-hub/models/embedding-models/bge-m3)
12. [Qdrant vs Milvus:A Brutal Dissection of Two Vector Databases in the AI Arena | by Dr. Elise Tanaka | Medium](https://medium.com/@aefselinates/qdrant-vs-milvus-a-brutal-dissection-of-two-vector-databases-in-the-ai-arena-e6ccd708e3c1)
13. [DeepSeek-OCR vs GPT-4-Vision vs PaddleOCR: 2025 Accuracy Guide](https://skywork.ai/blog/ai-agent/deepseek-ocr-vs-gpt-4-vision-vs-paddleocr-2025-comparison/)
14. [baai-bge-reranker-v2-m3](https://ai.azure.com/catalog/models/baai-bge-reranker-v2-m3)
15. [Milvus Lite vs. Standalone vs. Distributed: Which Mode is Right for You? - Zilliz blog](https://zilliz.com/blog/choose-the-right-milvus-deployment-mode-ai-applications)
16. [GitHub - FlagOpen/FlagEmbedding: Retrieval and Retrieval-augmented LLMs · GitHub](https://github.com/FlagOpen/FlagEmbedding)
17. [Vector Database Comparison: Pinecone vs Weaviate vs Qdrant vs FAISS vs Milvus vs Chroma (2025) | LiquidMetal AI](https://liquidmetal.ai/casesAndBlogs/vector-comparison/)
18. [General Table Recognition V2 - PaddleX Documentation](https://paddlepaddle.github.io/PaddleX/3.3/en/pipeline_usage/tutorials/ocr_pipelines/table_recognition_v2.html)
19. [bge-reranker-v2-m3 - Pinecone Docs](https://docs.pinecone.io/models/bge-reranker-v2-m3)
20. [GitHub - milvus-io/milvus: Milvus is a high-performance, cloud-native vector database built for scalable vector ANN search · GitHub](https://github.com/milvus-io/milvus)
21. [bge-m3 Model by BAAI | NVIDIA NIM](https://build.nvidia.com/baai/bge-m3/modelcard)
22. [Vector Database Comparison 2025: Pinecone vs Weaviate vs Qdrant vs Milvus vs FAISS | Complete Guide](https://tensorblue.com/blog/vector-database-comparison-pinecone-weaviate-qdrant-milvus-2025)
23. [Extract Tables from Image Documents with PaddleOCR](https://www.toolify.ai/ai-news/extract-tables-from-image-documents-with-paddleocr-413348)
24. [qllama/bge-reranker-v2-m3](https://ollama.com/qllama/bge-reranker-v2-m3)
25. [Run Milvus in Docker (Linux) | Milvus Documentation](https://milvus.io/docs/install_standalone-docker.md)
26. [BGE-M3 Embedding Model](https://www.emergentmind.com/topics/bge-m3-embedding-model-3ae9be85-46f0-4bec-be49-80d85554d4a6)
27. [Comparative Evaluation of Milvus and Qdrant for Retrieval-Augmented Generation (RAG) | by Marcus Feldman | Medium](https://medium.com/@oliversmithth852/comparative-evaluation-of-milvus-and-qdrant-for-retrieval-augmented-generation-rag-a101a72f93d1)
28. [PaddleOCR/ppstructure/table/README.md at main · PaddlePaddle/PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR/blob/main/ppstructure/table/README.md)
29. [bge-reranker-v2-m3 | AI Model Details](https://www.aimodels.fyi/models/replicate/bge-reranker-v2-m3-yxzwayne)
30. [How to Get Started with Milvus - Milvus Blog](https://milvus.io/blog/how-to-get-started-with-milvus.md)
31. [bge-m3](https://ollama.com/library/bge-m3)
32. [Vector DBs, Decoded: Qdrant vs Milvus vs Weaviate | by Nikulsinh Rajput | Medium](https://medium.com/@hadiyolworld007/vector-dbs-decoded-qdrant-vs-milvus-vs-weaviate-57455146b9f6)
33. [PaddleOCR/ppstructure/table/README.md at release/2.7 · PaddlePaddle/PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR/blob/release/2.7/ppstructure/table/README.md)
34. [BAAI/bge-reranker-base · Hugging Face](https://huggingface.co/BAAI/bge-reranker-base)
35. [Install Milvus Cluster with Milvus Operator | Milvus Documentation](https://milvus.io/docs/install_cluster-milvusoperator.md)
36. [The guide to bge-m3 | BAAI](https://zilliz.com/ai-models/bge-m3)
37. [Choosing a vector database for ANN search at Reddit - Milvus Blog](https://milvus.io/blog/choosing-a-vector-database-for-ann-search-at-reddit.md)
38. [PaddleOCR VL + RAG: Revolutionize Complex Data Extraction (Open-Source) | by Gao Dalie (高達烈) | Data Science Collective | Medium](https://medium.com/data-science-collective/paddleocr-vl-rag-revolutionize-complex-data-extraction-open-source-ee3d9e937ba9)
39. [bge-reranker-v2-m3-onnx-o3-cpu](https://www.promptlayer.com/models/bge-reranker-v2-m3-onnx-o3-cpu)
40. [Install Milvus Standalone with Milvus Operator Milvus v2.3.x documentation](https://milvus.io/docs/v2.3.x/install_standalone-operator.md)
41. [\[2402.03216\] M3-Embedding: Multi-Linguality, Multi-Functionality, Multi-Granularity Text Embeddings Through Self-Knowledge Distillation](https://arxiv.org/abs/2402.03216)
42. [Milvus vs Qdrant: Vector Database Performance Comparison](https://www.myscale.com/blog/milvus-vs-qdrant-vector-database-performance/)
43. [(PDF) PaddleOCR 3.0 Technical Report](https://www.researchgate.net/publication/393511573_PaddleOCR_30_Technical_Report)
44. [GitHub - LazaUK/HuggingFace-BAAI-BGERerankerv2m3: BGE Reranker v2 m3 demo with Hugging Face transformers for local and Azure cloud use. · GitHub](https://github.com/LazaUK/HuggingFace-BAAI-BGERerankerv2m3)
45. [Introducing Milvus Lite: the Lightweight Version of Milvus - Milvus Blog](https://milvus.io/blog/introducing-milvus-lite-lightweight-version-of-milvus.md)
46. [BGE M3 | Milvus Documentation](https://milvus.io/docs/embed-with-bgm-m3.md)
47. [What vector databases are best for semantic search applications?](https://milvus.io/ai-quick-reference/what-vector-databases-are-best-for-semantic-search-applications)
48. [PaddleOCR 3.0 Technical Report](https://arxiv.org/html/2507.05595v1)
49. [BGE Reranker — BGE documentation](https://bge-model.com/tutorial/5_Reranking/5.2.html)
50. [Milvus Lite Milvus v2.2.x documentation](https://milvus.io/docs/v2.2.x/milvus_lite.md)
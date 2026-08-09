# Research RAG Agent：简历核心项目补全计划

> 审计日期：2026-08-04  
> 审计范围：当前仓库的 PDF/Markdown 摄入、存储、检索、回答、引用、评测和 Agent 实现  
> 目标：先把项目做成可运行、可验证、可复现实验的 RAG 系统，再将它描述为简历核心项目。

## 0. 2026-08-09 复核快照

**简历准入结论：不通过，暂不能提升为简历核心项目。** 当前已完成摄入和结构化引用的主要代码路径与离线单测，但没有真实服务 E2E；BM25 四阶段检索、50-100 条评测集、质量/性能报告和原生 Tool Calling 循环均未完成。

| 简历标准 | 状态 | 当前证据 | 主要缺口 |
|---|---|---|---|
| PDF/Markdown 摄入、入库、检索、回答 | 部分完成（约 60%） | parser/chunker、批量 embedding、状态和失败补偿有单测 | 2 PDF + 2 Markdown 的 PostgreSQL/Milvus/模型完整 E2E 与双存储一致性报告 |
| 可验证页码或章节引用 | 部分完成（约 60%） | locator、PostgreSQL hydration、`[S1]` 解析和结构化 citation 有单测 | 真实文件回查、claim 支持性、无效引用拒绝/重试 |
| BM25 + Dense + RRF + Reranker | 未完成（约 15%） | 运行路径仅有 Dense；RRF/Reranker 是未接线代码 | BM25、统一 candidate、四阶段接线、trace、降级与集成测试 |
| 50-100 条带证据问答集 | 未开始 | 仓库无 `eval/` 数据集 | 建设约 80 条 verified 样本及 schema、manifest、validator |
| Recall@K、引用准确率、忠实性、P95 | 未开始 | 无 runner、metrics 或 report | 可复现评测命令、逐样本结果、消融和汇总报告 |
| 真实 Tool Calling 循环（若称 Agent） | 未开始 | 当前是固定 search -> generate | 原生 tools/tool_calls、多步回传、校验、终止保护和 trace |

### 本次验证记录

- `python -m pytest --collect-only -q`：成功收集 21 项；`python -m pytest -q`：21 passed，1 warning。
- `python -m compileall -q src tests`：通过。
- `make test-unit`：失败。Makefile 使用裸 `pytest`，当前环境收集时出现 `ModuleNotFoundError: src`；应改为 `python -m pytest`。
- `make test-integration`、`make test-e2e`：失败；仓库没有 integration/e2e 测试或固定 PDF/Markdown fixtures。
- `ruff check src tests`：失败，共 556 项；全量 lint 尚未形成可用质量门禁。
- Milvus、Embedding、Reranker 健康端点当前不可连接；Docker socket 无权限读取，故本次不能产生真实服务 E2E 证据。
- 当前首个 Alembic revision 只向既有 `document` 表增加字段，不能从空库建立完整 schema；`scripts/init_db.py` 仍执行破坏性的 `drop_all/create_all`。

### 下一步执行顺序

1. **恢复可靠测试入口**：Makefile 全部改用 `python -m pytest`，补 `tests/conftest.py`/安装约定；建立固定 2 PDF + 2 Markdown fixtures 和真实服务探活 fixture。
2. **完成 Phase 1/2 E2E**：从空库迁移开始，验证四份文档上传 -> ready -> 双存储一致 -> search -> answer -> locator 回查；覆盖并发幂等、部分写入和模型失败。
3. **完成 Phase 3**：先定义 `RetrievalCandidate` 与 stage trace，再实现 PostgreSQL BM25、Dense wrapper、RRF、Reranker 映射和显式降级；移除默认时间衰减。
4. **冻结语料和分块配置后建设 Phase 4**：先完成 schema/validator，再人工复核约 80 条 evidence，固定 dev/test 和 document hash manifest。
5. **完成 Phase 5 并达标后再改简历**：生成 Recall@1/3/5/10、citation precision/completeness/locator accuracy、faithfulness、拒答准确率、P50/P95/P99 与四组消融报告。
6. **最后决定 Agent 命名**：实现 Phase 6 的原生 Tool Calling 循环；否则将项目对外名称收敛为 Research RAG System/Assistant。

## 1. 审计结论

当前项目具备 FastAPI、PostgreSQL、Milvus、解析器、Dense Embedding、RRF、Reranker 和问答编排器等代码骨架，但**尚未满足简历核心项目的完成标准**。README 和 `docs/progress_report.md` 中的部分“已完成”描述高于实际可运行程度，应以端到端测试和评测报告为最终依据。

| 能力 | 当前状态 | 审计结论 |
|---|---|---|
| PDF 摄入、入库、检索、回答 | 阻断 | PDF 能解析出页码，但主流水线使用返回字典的 `DocumentChunker`，随后按对象属性 `c.content` 读取，运行时会失败 |
| Markdown 摄入、入库、检索、回答 | 阻断 | `_get_chunker()` 收到的是 `.md`/`.markdown`，却只在等于 `markdown` 时选择 MarkdownChunker；因此 Markdown 被送入 PDF 风格分块器并产生空结果，后续同样存在返回类型不一致 |
| PostgreSQL + Milvus 一致入库 | 部分实现 | 两套存储逐 chunk 提交且没有事务/补偿；Milvus 插入失败会被吞掉，文档仍可能被标为 `ready` |
| 可验证页码/章节引用 | 未实现 | 解析/分块元数据局部存在，但搜索和聊天响应把 `content`、`source` 留空，回答也没有结构化 citation |
| BM25 + Dense + RRF + Reranker | 未实现 | 运行路径只有 Dense + 时间衰减；RRF、Reranker 文件未接线；BM25 无实现；Milvus 运行时 schema 与初始化脚本不一致 |
| 50-100 条带证据问答集 | 未实现 | 仓库中没有可复用评测数据集、标注规范或数据校验器 |
| Recall@K、引用准确率、忠实性、P95 延迟 | 未实现 | 无评测 runner、指标实现、基线结果或实验报告 |
| 真实 Tool Calling 循环 | 未实现 | `AgentOrchestrator` 是固定的一次检索、一次生成，不含工具定义、模型工具选择、执行结果回传和循环终止 |
| 自动化测试 | 阻断 | `pytest --collect-only -q` 在导入 `tests/test_api/test_upload.py` 时因测试数据不存在直接 `exit(1)`，结果为 0 tests collected |

### 1.1 关键代码证据

- `src/core/ingest/pipeline.py:28-31,46-55`：分块器选择使用错误的文件类型值，且假定所有分块结果都是 `ChunkResult` 对象。
- `src/core/ingest/chunker.py:10-72`：通用分块器实际返回 `dict`，与主流水线契约不一致。
- `src/core/ingest/pdf_parser.py:41-49`：PDF 页码已从 1 开始保留，是后续可验证引用的可用基础。
- `src/core/ingest/markdown_parser.py:65-73,252-260`：Markdown 已产生 heading、start_line、end_line，但当前没有完整传递到回答引用。
- `src/core/ingest/pipeline.py:87-124`：PostgreSQL 每个 chunk 单独提交，Milvus 写入结果未校验，却仍将文档置为 `ready`。
- `src/db/milvus_client.py:30-77`：实际自动创建的 collection 只有 dense vector，没有 sparse/BM25 字段。
- `scripts/init_milvus.py:53-135`：脚本声明 sparse 字段，但调用了当前 wrapper 不存在的 `create_collection`、`add_field`、`create_index` 方法，不能作为有效初始化路径。
- `src/core/retrieval/hybrid_search.py:36-49`：只执行 Dense 检索和时间衰减，没有 BM25、RRF、Reranker。
- `src/core/retrieval/rrf_fusion.py:5-38`、`reranker.py:5-30`：存在独立骨架，但没有进入搜索链路；reranker 接口还需与实际 vLLM 服务协议核对。
- `src/api/search.py:27-40`：搜索响应主动清空 content、source、created_at。
- `src/core/agent/orchestrator.py:20-51`：回答路径只有 search -> prompt -> generate，返回 context 的 preview/source 为空。
- `src/core/agent/prompt_templates.py:8-15,38-43`：提示词可放来源文本，但没有强制引用标识、引用格式和无证据拒答协议。
- `src/api/documents.py:50-59`：文档状态端点固定返回 `ready`，不能证明真实入库状态。
- `tests/test_api/test_upload.py:1-14`：测试模块在导入阶段访问本地文件并退出，无法被 pytest 正常收集。

## 2. 完成定义与简历准入门槛

只有同时满足以下条件，项目才进入“可作为简历核心项目”的状态：

1. 用至少 2 个 PDF 和 2 个 Markdown 样本文档跑通上传 -> 解析 -> 分块 -> 双存储入库 -> 混合检索 -> 回答，并由自动化端到端测试验证。
2. 每个事实性回答返回至少一个结构化引用；PDF 引用可定位到文件名和页码，Markdown 引用可定位到文件名和章节路径，必要时附行号范围。
3. 生产检索路径真实执行 `BM25 -> Dense -> RRF -> Reranker -> Top-K`，并能记录每阶段候选、分数、耗时和降级状态。
4. 仓库内有 50-100 条经过人工复核、带 gold evidence 的问答集，包含 PDF/Markdown、中英文、可回答/不可回答和困难负例。
5. 一条命令能产出 Recall@K、引用准确率、忠实性、端到端 P50/P95 延迟以及分阶段消融结果；结果带配置、模型、数据集版本和时间戳。
6. CI 或本地标准测试命令可稳定通过；测试不得依赖开发者机器上的偶然文件，也不得用静默降级伪造成功。
7. 若项目继续宣称“Agent”，还必须通过真实 Tool Calling 多步循环测试；否则对外命名应收敛为 Research RAG System/Assistant。

建议首版质量门槛如下，最终阈值在首轮基线后只允许提高，不应为得到“通过”而降低：

| 指标 | 首版门槛 | 说明 |
|---|---:|---|
| Recall@5 | >= 0.80 | gold evidence 任一相关 chunk 出现在 Top-5 |
| Recall@10 | >= 0.90 | 用于观察候选召回上限 |
| Citation precision | >= 0.90 | 返回引用中确实支持相邻回答 claim 的比例 |
| Citation completeness | >= 0.85 | 需要证据的事实 claim 中已被有效引用覆盖的比例 |
| Citation locator accuracy | >= 0.95 | 文件、页码/章节定位与原文一致的比例 |
| Faithfulness | >= 0.90 | 回答中的事实 claim 被所引证据支持的比例 |
| Answerable abstention accuracy | >= 0.85 | 无证据问题能明确拒答或声明不足 |
| 检索 P95 | 先基线，目标 <= 1.5 s | 固定硬件、热缓存、同一并发条件 |
| 端到端回答 P95 | 先基线，目标 <= 8 s | 非流式，从收到请求到完整回答；需同时报告模型与硬件 |

## 3. 实施原则与关键决策

### 3.1 先修正确性，再调效果

阶段顺序必须是：数据契约与双存储一致性 -> 引用链 -> 混合检索 -> 数据集 -> 评测与调优 -> 可选 Agent。基础链路未通过时，不做模型参数调优，也不输出简历数字。

### 3.2 BM25 的建议实现

首版建议使用 **PostgreSQL 全文检索或独立 BM25 索引（如 `rank_bm25`）作为明确、可解释的 BM25 通道**，Dense 继续使用 Milvus。原因是当前 Qwen3 Embedding 服务只返回 Dense vector，仓库没有可用 sparse encoder；把 Milvus sparse vector 或 BGE-M3 sparse 称为 BM25 会混淆技术口径。

为简化本项目部署，推荐先使用 PostgreSQL FTS：

- 为 chunk 保存标准化 `search_text` 和 `tsvector`/对应索引；中英文语料应验证 tokenizer，中文可选 jieba 预分词后按空格存储。
- 返回统一的 `RetrievalCandidate`，包含 `chunk_id`、rank、raw_score、retriever=`bm25`。
- Dense 通道返回同一结构，由 RRF 只使用排名融合，避免直接混合不可比的分数。

若实测 PostgreSQL 中文召回不足，再切换到 Elasticsearch/OpenSearch；不要在首版同时维护两种 sparse 后端。

### 3.3 引用是数据契约，不是提示词装饰

建立统一 `SourceLocator`：

```text
document_id, filename, file_type, content_hash, chunk_id
PDF: page_start, page_end, optional bbox
Markdown: section_path[], heading, line_start, line_end
```

引用 ID 在送入模型前生成，例如 `[S1]`。模型只能引用已提供的 ID；服务端解析回答引用并将 `[S1]` 映射为结构化 locator，同时拒绝/标记不存在的 ID。前端展示的页码必须与用户看到的 PDF 页序一致（当前解析器为 1-based），Markdown 至少展示完整章节路径。

### 3.4 静默失败必须改为显式状态

Embedding、Milvus 插入、Reranker 或 LLM 失败不得伪装为成功：

- 摄入失败：文档 `failed`，记录 stage、error_code、message，可重试。
- 某检索通道失败：响应记录 `degraded=true` 和失败通道；评测默认将降级样本视为失败。
- Reranker 失败：可以回退到 RRF 排名，但必须有指标和日志，不能生成伪 rerank score。
- 双存储不一致：不能标记 `ready`，需要补偿删除或可重入修复任务。

## 4. 分阶段实施计划

## Phase 0：建立可测试基线（0.5-1 天）

**状态：部分完成（2026-08-09 复核）**

**验证记录：** 原 `tests/test_api/test_upload.py` 在导入阶段访问本地文件并调用 `exit(1)`，现已移除并替换为标准 pytest 单元测试。`python -m pytest --collect-only -q` 已成功收集 10 项测试，`python -m pytest -q` 已通过 10 项测试；新增 `unit`、`integration`、`e2e` markers 与 `Makefile` 测试入口。完整仓库 `ruff check` 仍有大量既有历史问题，本阶段只完成了改动模块的编译和功能测试，未将全量 lint 清理虚报为完成。

**2026-08-09 复核修正：** 当前已增长为 21 项单测且 `python -m pytest -q` 全过，但 Makefile 标准入口会因导入路径失败，integration/e2e marker 没有对应测试，固定 fixtures 也不存在。因此 Phase 0 从“已完成”回退为“部分完成”，直到标准命令和分层测试真实可用。

### 任务

- 将 `tests/test_api/test_upload.py` 改为标准 pytest 测试，测试数据通过 fixture 创建或放入 `tests/fixtures/`，禁止模块导入阶段执行请求/退出。
- 增加 `tests/unit`、`tests/integration`、`tests/e2e` 分层和 marker；外部服务用 fixture 探活，不可用时仅跳过明确标记的 integration/e2e，unit 必须始终执行。
- 固化本地标准命令：lint、unit、integration、e2e、evaluation。
- 新增最小可观测性字段：request_id、document_id、stage、duration_ms、candidate_count、degraded。

### 产物

- `tests/conftest.py`
- `tests/fixtures/sample.pdf`、`sample.md`（可使用程序生成、内容固定且许可清晰）
- `Makefile` 或 `scripts/verify.sh`
- 更新 `pyproject.toml` 测试 marker 和必要依赖

### 验收

- `pytest --collect-only` 返回成功且能发现真实测试。
- 无 Docker/模型时 unit tests 全过；服务齐备时 integration/e2e 可明确执行。

## Phase 1：跑通 PDF/Markdown 摄入和双存储（2-3 天）

**状态：进行中（2026-08-09 复核，代码与单测已完成主要路径，真实 E2E 未完成）**

**本次完成：** 统一 `.pdf`、`.md`、`.markdown` 到 `pdf|markdown` 类型契约；主摄入流程改用 `PDFChunker`/`MarkdownChunker`，消除原来的 dict/对象返回类型冲突；PDF chunk 保留 1-based `page_start/page_end`；Markdown 保留嵌套 `section_path` 与行号；embedding 改为批量请求；Document 与 Project 关系真实写入；只有 Milvus 确认全量写入后才标记 `ready`，写入失败时标记 `failed` 并补偿删除向量和已持久化的 chunk；文档状态 API 改为读取真实数据库状态、阶段、chunk 数和错误摘要；上传路径按 UUID 隔离，并验证格式和 `project_ids`。2026-08-06 继续补充 SHA-256 `content_hash` 幂等复用、失败文档重试复用原 ID、上传大小/MIME/文件签名/UTF-8 校验、Alembic 异步迁移入口和首个 content hash revision；Milvus 初始化脚本已收敛到应用 schema，已有 collection 会校验必需字段，客户端改为惰性连接以消除导入阶段网络副作用。

**已验证：** 真实 PDF/Markdown parser、页码/章节 locator、扩展名分流、成功状态和向量写入失败补偿均由 10 项离线单元测试覆盖。

**待完成/待验证：** PostgreSQL + Milvus + Embedding 服务的真实端到端测试、并发重复提交时数据库唯一冲突的集成验证、现有数据库 content hash 回填，以及在真实 PostgreSQL 上执行 Alembic revision。当前环境没有可用的完整外部服务，故尚未声称端到端链路已跑通，Phase 1 仍保持进行中。

### 任务

1. 统一摄入数据契约。解析器显式输出 `file_type`；所有 chunker 都只返回 `ChunkResult`，主流水线依据标准化类型 `pdf|markdown` 选取 `PDFChunker|MarkdownChunker`。删除或收敛重复的 `DocumentChunker` 路径。
2. 保留证据元数据。PDF chunk 带 page_start/page_end；Markdown parser 构造完整 heading path，并保证所有大章节拆分后的 chunk 仍保留 section_path 和 line range。
3. 修正文档归属。创建 Document 时真正写入 `project_document` 关系，不只在 Milvus entity 中存 project_ids。
4. 批量嵌入和批量写入，避免每 chunk 一次 HTTP + 一次事务。校验向量数量和维度，空向量直接失败。
5. 实现一致性：PostgreSQL 事务先写 document/chunks，Milvus 成功后再将状态置为 ready；失败时回滚或按 document_id 执行补偿清理；摄入使用 content hash/idempotency key 支持安全重试。
6. 统一 Milvus schema 创建路径，移除不可运行的初始化脚本 API；schema 至少保存 chunk_id、document_id、dense_vector 和检索所需过滤字段，权威正文及 locator 建议以 PostgreSQL 为准。
7. 状态 API 查询真实 Document 状态，并返回 chunk_count、current_stage、error（若有）。
8. 上传安全：校验扩展名/MIME、文件大小、清理文件名并使用 document_id 隔离路径，避免覆盖和路径穿越。

### 主要修改位置

- `src/core/ingest/base.py`、`pipeline.py`、`pdf_parser.py`、`markdown_parser.py`、`pdf_chunker.py`、`markdown_chunker.py`
- `src/db/models.py`、`postgres.py`、`milvus_client.py`
- `src/api/documents.py`、`src/schemas/document.py`
- 数据库 migration（建议引入 Alembic，而不是仅靠 `create_all`）

### 验收测试

- 单测：PDF 每个 chunk 的页码有效；Markdown 每个 chunk 的章节路径有效；长章节拆分不丢 locator。
- 集成：相同文档重复提交不会产生重复 chunk；Embedding/Milvus 故障时状态为 failed，修复后可重试。
- E2E：2 PDF + 2 Markdown 均达到 `ready`，PostgreSQL chunk 数与 Milvus entity 数一致，抽样内容/hash 一致。

## Phase 2：建立可验证引用的检索与回答（2 天）

**状态：进行中（2026-08-09 复核，结构化引用已有单测，真实引用准确性未验证）**

**本次完成：** 新增统一 source locator 与 citation ID 解析/校验；搜索和聊天命中均按 chunk ID 从 PostgreSQL 回填权威正文、文件名和 PDF 页码或 Markdown 章节/行号，并通过 `project_document` 再次校验项目归属；RAG prompt 改用 `[S1]...[Sn]` 且要求证据不足时拒答；聊天响应返回结构化 `citations[]`、真实 `retrieved_context`，并将模型产生的未知来源 ID 放入 `invalid_citation_ids`。相关路径由离线单测覆盖。

**待完成/待验证：** 在真实 PDF/Markdown E2E 中核对 locator 与原文；将无效引用从“标记”提升为可配置的拒绝/重试策略；补充 claim 级引用支持性校验与不可回答问题的真实模型测试；检索的分阶段分数将在 Phase 3 完整链路接入后补齐。

### 任务

- 定义统一的 `SearchResult`/`Citation` schema，搜索 API 返回正文预览、filename、locator 和分阶段分数，不再填空字符串。
- 检索命中后按 chunk_id 从 PostgreSQL 批量 hydrate 权威正文与文档元数据，禁止把缺字段的 Milvus hit 直接交给回答层。
- RAG prompt 采用编号来源 `[S1]...[Sn]`，明确要求：仅根据来源回答、事实 claim 后紧跟引用、证据不足时拒答、不允许创造来源 ID。
- 回答后解析引用 ID，校验其属于本次检索上下文，返回结构化 `citations[]`，字段包含 source_id、chunk_id、filename、page/section、quote/snippet。
- `retrieved_context` 返回真实 preview 和 source locator；前端可点开原文片段。
- 增加 citation validation 和无证据拒答测试，包括模型输出无效 `[S99]` 的情况。

### 主要修改位置

- `src/schemas/search.py`、`src/schemas/chat.py`
- `src/api/search.py`、`src/api/chat.py`
- `src/core/agent/prompt_templates.py`、`orchestrator.py`（若暂不做 Agent，建议重命名为 RAGService）
- 新建 `src/core/citations.py`

### 验收

- PDF 问题回答能由 API JSON 定位到正确文件和页码，并可在原 PDF 找到 supporting text。
- Markdown 问题回答能定位到正确文件与完整章节路径。
- 不可回答问题不生成虚假引用；任意返回引用都必须能映射到本次检索 chunk。

## Phase 3：接入 BM25 + Dense + RRF + Reranker（2-3 天）

**状态：未开始（2026-08-09 复核）**

**当前证据：** `HybridSearch.search()` 仍只执行 query embedding、Milvus Dense 检索和时间衰减。`rrf_fusion.py`、`reranker.py` 虽存在，但没有进入生产调用链；BM25、`RetrievalCandidate`、分阶段 trace 和对应测试均不存在。

### 目标流水线

```text
query
  -> BM25 Top-50 -------\
                         -> RRF(k=60) Top-20 -> Reranker -> final Top-5
  -> Dense Top-50 ------/
```

### 任务

1. 实现 `BM25Retriever`，索引与删除跟随 document 生命周期，并支持 project/content_type/version 过滤。
2. 将 Dense 封装为 `DenseRetriever`，明确 Milvus metric 的 distance/similarity 转换；不要把未标准化的 `distance` 当作越大越好的 score。
3. 定义 `RetrievalCandidate` 统一协议，并修正 RRF 去重和 rank 口径；记录来自哪些通道及各自 rank。
4. RRF 后截取 Top-N 进入 Reranker，再使用返回 index 将分数准确映射回 chunk；验证实际部署服务的请求协议、端点和健康检查。
5. 时间衰减默认移出科研文档主召回排序，或作为显式可配置的后处理并单独消融，避免新文档无依据压过高相关旧文档。
6. 所有 top-k、RRF k、rerank top-n、超时、批量大小配置化，并在评测报告记录实际值。
7. 对 BM25/Dense/Reranker 的失败分别实现可观测降级；正常评测必须确认四阶段均真实执行。

### 主要修改位置

- 新建 `src/core/retrieval/types.py`、`bm25.py`、`dense.py`
- 修改 `hybrid_search.py`、`rrf_fusion.py`、`reranker.py`、`time_decay.py`
- 修改 `src/db/models.py`/migration 与 `milvus_client.py`
- 补充 `tests/unit/retrieval` 和 `tests/integration/retrieval`

### 验收

- 测试可证明两路召回都被调用，RRF 的手算案例一致，rerank index 映射无错位。
- 记录每个结果的 `bm25_rank`、`dense_rank`、`rrf_score`、`rerank_score`。
- 消融报告至少包含 Dense-only、BM25-only、BM25+Dense+RRF、完整四阶段四组。

## Phase 4：建设 50-100 条带证据评测集（2-4 天，含人工复核）

**状态：未开始（2026-08-09 复核）**

仓库中不存在 `eval/datasets/rag_qa_v1.jsonl`、schema、manifest、validator 或人工复核记录。

### 数据范围

建议先建设 80 条，便于分层：

| 类别 | 建议数量 |
|---|---:|
| PDF 可回答事实题 | 20 |
| Markdown 可回答事实题 | 20 |
| 多跳/跨 chunk 或跨章节题 | 12 |
| 术语、缩写、精确关键词题（检验 BM25） | 8 |
| 语义改写题（检验 Dense） | 8 |
| 困难负例/相似干扰项 | 6 |
| 证据不足、应拒答题 | 6 |

中英文比例按实际文档分布确定，PDF/Markdown 均不得低于 25%。不要让 LLM 生成后未经人工检查就成为 gold。

### 建议 JSONL schema

```json
{
  "id": "qa-001",
  "question": "...",
  "answerable": true,
  "reference_answer": "...",
  "evidence": [
    {
      "document_id": "...",
      "filename": "...",
      "chunk_id": "...",
      "page_start": 3,
      "page_end": 3,
      "section_path": [],
      "quote": "支持答案的最小原文片段"
    }
  ],
  "tags": ["pdf", "factoid", "zh"],
  "review_status": "verified",
  "reviewer": "human"
}
```

### 任务与产物

- `eval/datasets/rag_qa_v1.jsonl`：只纳入 verified 样本。
- `eval/datasets/schema.json` 与 `eval/README.md`：字段、标注规范、冲突处理、版本规则。
- `scripts/validate_eval_dataset.py`：检查 ID 唯一、locator 存在、quote 能在源 chunk 找到、类别和格式分布。
- 固定 train/dev/test 或 tune/test 划分；不得在 test 上反复调参。建议 20 条 dev、60 条 test。
- 保存 dataset manifest：文档 hash、ingestion config、chunking config；重新分块造成 chunk_id 变化时必须升版本。

### 验收

- 80 条全部通过 schema 与 evidence existence 校验。
- 每条至少一位人工复核；歧义题修订或移除。
- 数据集分布报告和版本号进入仓库。

## Phase 5：实现评测、消融和性能报告（2-3 天）

**状态：未开始（2026-08-09 复核）**

仓库中不存在 evaluation runner、指标实现、benchmark 脚本、逐样本结果或可复现实验报告。

### 指标口径

1. **Recall@K**：对 answerable 问题，Top-K 中命中任一 gold chunk 记 1；同时补充 page/section-level recall，降低 chunk 边界变化带来的假失败。报告 K=1/3/5/10，并按格式、语言、题型分桶。
2. **Citation precision**：所有返回 citation 中，locator 正确且证据支持对应 claim 的比例。
3. **Citation completeness**：回答中需要外部证据的事实 claim，被有效 citation 覆盖的比例。
4. **Faithfulness**：将回答拆为原子事实 claim，计算被引用上下文蕴含的 claim 比例；自动 judge 只作辅助，至少抽样 20 条双人/人工复核并报告一致率。
5. **P95 latency**：使用 `perf_counter` 记录 embedding、BM25、Dense、RRF、Reranker、LLM、total；先预热，固定并发（建议 1 和 5 各一组），至少 100 次查询，报告 P50/P95/P99、错误率、硬件、模型和缓存状态。
6. **可用性指标**：摄入成功率、双存储一致率、无效引用率、降级率和拒答准确率。

### 实现建议

- 新建 `eval/runner.py`、`eval/metrics.py`、`eval/judges.py`、`eval/report.py`。
- runner 输出逐样本 JSONL，report 输出 Markdown/JSON 汇总；失败样本必须保存 query、gold、retrieved IDs、citations 和阶段耗时。
- 自动 judge 使用固定 prompt、模型、temperature=0，并缓存结果；报告中明确区分 deterministic metrics、LLM judge 和 human audit。
- 增加 `scripts/benchmark.py`，避免把一次 API 返回的 `query_time_ms` 当成 P95。
- 用同一数据、硬件和运行次数做消融：Dense-only、BM25-only、RRF、RRF+Reranker；同时记录质量与延迟变化。

### 验收和报告

- 一条命令从固定数据集生成 `eval/reports/<timestamp>/summary.md`、`metrics.json`、`per_sample.jsonl`、`config.json`。
- 达到第 2 节门槛；未达标则报告失败分桶并回到 chunking、retrieval 或 prompt 定向修复。
- README 只引用实际报告中的数字，并链接到可复现配置。

## Phase 6：可选的真实 Tool Calling Agent（2-3 天）

**状态：未开始（2026-08-09 复核）**

当前 `AgentOrchestrator` 是代码固定执行一次 search 和一次 generate；`LLMClient` 没有发送或解析原生 `tools`、`tool_choice`、`tool_calls` 字段。

此阶段不是 RAG 核心能力的前置条件。若暂不完成，应从项目标题和简历中去掉“Agent”。

### 最小真实循环

```text
用户消息 -> LLM（携带 tool schemas）
  -> 若返回 tool_calls：校验参数 -> 执行工具 -> 追加 tool result -> 再调用 LLM
  -> 若返回 final answer：校验 citations -> 返回
  -> 超过 max_steps / 重复调用 / 超时：显式终止
```

### 首版工具

- `search_knowledge(query, project_ids, filters, top_k)`：调用完整混合检索。
- `get_source(chunk_id)`：获取权威正文和 locator，用于引用核验。
- 可选 `list_documents(project_ids)`：帮助模型限定检索范围。

不要把上传、删除、写记忆等有副作用工具混入首版，直到有权限、幂等和确认机制。

### 工程要求

- 使用模型 API 的原生 `tools/tool_choice/tool_calls` 字段，不能靠解析“请调用某工具”的自然语言或 JSON 文本冒充。
- 工具 registry 使用 Pydantic/JSON Schema 校验参数；设置 allowlist、timeout、max_steps（建议 4-6）、max repeated calls 和总 token/时间预算。
- 保存完整 trace：step、model response、tool name、validated args、duration、result/error、final state；日志中脱敏。
- 工具异常作为 tool result 回传，让模型可修正一次；不可无限循环。
- 最终回答仍经过 citation validator。

### 测试与验收

- 单工具：模型选择 search，执行后基于结果回答。
- 多步：先 search 再 get_source，至少发生两次真实模型调用。
- 错参修复：第一次参数校验失败，模型收到错误后生成合法调用。
- 无工具问题：模型直接回答，不强制调用。
- 循环保护：重复工具调用达到阈值后终止。
- 轨迹断言能证明工具是由模型选择并真实执行，而非代码硬编码固定检索。

## 5. 测试矩阵

| 层级 | 覆盖重点 | 外部依赖 |
|---|---|---|
| Unit | parser/chunker locator、RRF 手算、schema、citation parser、metrics | 无，全部 mock/fake |
| Integration | PostgreSQL/Milvus/BM25 生命周期、实际 embedding/reranker 协议 | Docker 服务 |
| E2E | 上传 PDF/MD -> ready -> search -> answer -> citation resolve | 完整服务 + LLM |
| Evaluation | 80 条数据的质量、消融、失败分析 | 固定模型/配置 |
| Performance | 分阶段和端到端 P50/P95/P99、错误率 | 固定硬件和并发 |
| Agent | tool selection、执行、回传、终止和 trace | 支持 tool calling 的模型 |

必须增加的回归案例：

- `.md` 与 `.markdown` 都选择 MarkdownChunker；`.pdf` 选择 PDFChunker。
- 1-based PDF 页码不在清洗和 overlap 时丢失或串页。
- Markdown H1 内容、标题前导内容、嵌套标题路径和大章节拆分正确。
- 同一 chunk 同时被 BM25/Dense 命中时 RRF 去重且分数正确。
- Reranker 返回索引乱序时仍映射到正确 chunk。
- 空 embedding、Milvus 部分插入、reranker 超时、LLM 无效引用都显式失败/降级。
- project filter 不越权返回其他项目文档。

## 6. 里程碑、依赖和建议排期

| 里程碑 | 阶段 | 预计投入 | 退出条件 |
|---|---|---:|---|
| M1 可运行摄入 | Phase 0-1 | 3-4 天 | PDF/MD 双存储一致、状态真实、测试可运行 |
| M2 可验证 RAG | Phase 2 | 2 天 | 回答与搜索返回可解析页码/章节引用 |
| M3 完整检索 | Phase 3 | 2-3 天 | BM25 + Dense + RRF + Reranker 真实接线并有 trace |
| M4 可复现实验 | Phase 4-5 | 4-7 天 | 80 条 verified QA 和完整质量/性能报告 |
| M5 真实 Agent（可选） | Phase 6 | 2-3 天 | 原生 tool calling 多步循环测试通过 |

总计：核心 RAG 约 11-16 个工程日；包含 Agent 约 13-19 个工程日。人工问答集标注可以和 Phase 2-3 并行，但 gold evidence 必须基于稳定的文档版本和 locator，最终 chunk_id 标注需在分块方案冻结后完成。

## 7. 风险与控制

- **模型服务协议不匹配**：先用 integration contract test 固化 embedding/reranker 请求响应，再接业务链路。
- **Milvus schema 漂移**：只保留一个 migration/初始化入口，并在启动时校验 schema version，不自动吞异常继续运行。
- **中文 BM25 分词弱**：以中文子集 Recall@K 决定 tokenizer/后端，而不是凭主观选择；保留可替换 Retriever 接口。
- **引用看似正确但不支持 claim**：同时评测 locator accuracy、citation precision 和 completeness，不能只检查“回答里有没有 `[S1]`”。
- **LLM judge 偏差**：固定 judge 配置、缓存结果、抽样人工复核并报告一致率。
- **性能数字不可比较**：报告硬件、模型版本、并发、缓存、样本数和统计口径；区分检索与 LLM 延迟。
- **测试靠 mock 获得虚假绿灯**：unit 使用 mock，但简历准入必须有真实服务 E2E 和评测报告。
- **文档宣称超前**：在 M4 前将 README 功能状态标为 experimental；只有验收证据生成后更新为 completed。

## 8. 最终交付清单

- [ ] PDF/Markdown 真实 E2E 测试及固定 fixtures
- [ ] 统一 parser/chunker/source locator 数据契约
- [ ] PostgreSQL/Milvus 可重入且一致的摄入流程
- [ ] 真实状态 API 和显式失败/降级
- [ ] 结构化页码/章节引用与 citation validator
- [ ] BM25、Dense、RRF、Reranker 完整链路与分阶段 trace
- [ ] 80 条 verified、带 evidence 的 `rag_qa_v1`
- [ ] Recall@K、引用、忠实性、拒答和延迟指标实现
- [ ] 四组消融实验及可复现报告
- [ ] README 架构、运行方式、测试命令和实测数字更新
- [ ] 可选：原生 Tool Calling 多步循环、保护机制和轨迹测试

## 9. 简历表述规则（完成后再写）

最终简历内容应只使用 `eval/reports` 中可复现的实测数字，推荐结构为：

1. **系统范围**：支持多少文档/格式、采用何种双存储和部署方式。
2. **核心设计**：可验证 locator、BM25 + Dense + RRF + Reranker、结构化 citation。
3. **量化结果**：数据集规模、Recall@5/10、citation precision/faithfulness、P95、相对 Dense-only 的提升。
4. **Agent（仅完成 Phase 6 后）**：工具数量、平均/最大步数、工具调用成功率和循环保护。

禁止在实现和报告出现前写“完整混合检索”“高准确率”“生产级 Agent”等无法核验的描述。

# A/B 审查修补交付（2026-09-19）

本次完成 A/B 审查中的五项修补及回归验证。继续使用 main，未 commit/push，未修改 .env。
Phase C 尚未开始；稳定 source_id、完整 result_ref 存储、checkpoint/resume、Agent 闸门仍属 C。

## Phase A 修补完成

- 新增迁移 `20260919_01`（接在 `20260918_02` 后），适用于尚未升级和已经升级到 B 的数据库。
- 根据旧 conversation 补建 chat_session，保留项目归属和历史时间；按 timestamp/id 稳定排序补齐消息序号。
- 纯旧会话从 0 编号；混合历史将旧消息放在现有最小序号之前，保留已分配的游标（旧消息可能使用负数）。
- 新增回归实际调用 Alembic，随后通过会话 API 和历史加载函数验证完整分页；同时验证已有会话标题、现有消息序号及空库建表。
- 迁移 downgrade 不撤销数据修复，不删除恢复的历史。

阶段验证：`python -m pytest -m unit -q` → **63 passed, 5 deselected**；
`python -m pytest -m e2e -q -rs` → **3 passed, 1 skipped, 64 deselected**。
通过后才继续 B 的功能修改。

## Phase B 修补完成

| 审查问题 | 修复 | 验证 |
|---|---|---|
| list_documents 对 JSON 整行 DISTINCT，在 PostgreSQL 不兼容 | 改用项目关联 EXISTS，无需整行 DISTINCT；保持跨项目文档只出现一次 | SQLite 实际查询与跨项目回归通过；PostgreSQL 专用集成测试已加入，等待测试库 |
| 后台摘要同步阻塞事件循环 | 新增异步 HTTPX 客户端；请求等待/重试均可让出事件循环，总超时可取消 | HTTP MockTransport + Event 验证等待期间其他任务可执行；上传 E2E 验证此时仍可查询与 PATCH |
| 摘要失败/超时影响流程 | 生成、读写库被总超时包围，失败仅记录 warning；不更改 ready 状态 | 超时取消 HTTP、模型失败、非法 JSON、已有摘要跳过均通过 |
| 人工 tags 可能被后台生成结果覆盖 | 写入时读最新记录，仅填尚未设置的 tags；PostgreSQL 写入事务使用行锁 | 模型等待期间 PATCH 标签，后台完成后保留人工标签的 E2E 通过 |
| 首个 chunk 绕过 max_chars | 返回不修改 ORM 正文的片段视图；严格限制正文和分隔符，保留原 chunk 的字符区间 | 9000 字首块、恰好 200 字、跨片段分隔符、完整续读回归通过 |
| Top-K 后过滤导致范围内文档漏检 | 先由权威数据库确定匹配 chunk IDs，再对 Dense/BM25 同时施加 ID 过滤；保留 hydration 后校验 | 80 条干扰候选在前，目标论文仍经两个通道召回；标签订正后立即可查，越权/无匹配范围为空 |

`read_document_range` 新增 `start_chunk_index` / `start_char` 两个可选参数。
`max_chars` 限制正文（包括片段间的两个换行符），不包括 JSON 元数据；顶层 content 与 chunks 的正文兼容保留。
截断时返回 `next_cursor`，将其中的参数用于下一次调用并保留原文档、页码或章节条件即可续读。
每片段带 `char_start` / `char_end`，结束时 `next_cursor=null`。

HTTPX 已在项目 dev extra 声明，本次移入运行依赖，没有安装新库。
新增配置仅写入 `.env.example` 和 README：`DOCUMENT_ENRICHMENT_TIMEOUT_SECONDS=60`。
既有同步聊天模型路径未在本次扩展；其异步执行/预算边界交由 C 处理。

## 最终验证

环境：Linux，conda `research_rag`。

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate research_rag
VECTOR_STORE_BACKEND=memory LLM_PROVIDER=local python -m pytest -m unit -q
VECTOR_STORE_BACKEND=memory LLM_PROVIDER=local python -m pytest -m e2e -q -rs
VECTOR_STORE_BACKEND=memory LLM_PROVIDER=local python -m pytest -m integration -q -rs
```

| 命令 | 结果 |
|---|---|
| unit | **75 passed, 7 deselected**（12.06s） |
| e2e | **4 passed, 1 skipped, 77 deselected**（2.86s） |
| integration | **2 skipped, 80 deselected**（未配置真实模型及 PostgreSQL 测试库） |
| 对当前 A/B 全部 35 个改动 Python 文件执行 ruff check | **All checks passed** |
| 对相同文件执行 ruff format --check | **35 files already formatted** |
| git diff --check | 通过 |

相较修补前增加 **16 个单测、1 个离线 E2E、1 个可选 PostgreSQL 集成测试**。
原 A/B 的三轮会话、重启后历史、轨迹查询、版本记录、材料盘点和 PATCH 订正测试继续通过。

全仓 `ruff check src tests migrations` 仍有 **353 项历史问题**，本次未宣称全仓 lint 通过。
本次也补齐了已改动 A/B 文件的类型注解、FastAPI Annotated 依赖声明和格式，不通过削弱测试断言修复问题。

关键新增/增强测试：

- `tests/unit/db/test_chat_history_migration.py`：4 项，真实迁移与 API 可读性。
- `tests/unit/agent/test_document_range_limits.py`：5 项，字数边界和完整续读。
- `tests/unit/agent/test_document_tools.py`：增强原过滤/读取断言，新增跨项目去重与真实内存混合检索范围回归。
- `tests/unit/ingest/test_document_enrichment.py`：新增异步等待、超时取消、瞬时错误重试与人工标签保留。
- `tests/e2e/test_offline_workflow.py`：增加上传期间的并发查询/PATCH，以及订正后按标签搜索。
- `tests/integration/test_postgres_document_registry.py`：使用真正 AsyncSession，验证 PostgreSQL JSON 字段查询和跨项目去重；随机 schema 在事务结束时回滚。

## 文件列表

本轮新增：

- `migrations/versions/20260919_01_backfill_chat_history.py`
- `src/utils/async_llm_client.py`
- `tests/unit/db/test_chat_history_migration.py`
- `tests/unit/agent/test_document_range_limits.py`
- `tests/integration/test_postgres_document_registry.py`
- `docs/ab_repair_delivery_20260919.md`

本轮主要修改：

- `src/core/agent/tools.py`、`src/core/document_view.py`
- `src/core/ingest/enrichment.py`、`src/core/ingest/pipeline.py`
- `src/core/retrieval/hybrid_search.py`、`src/core/retrieval/hydration.py`、`src/db/vector_store.py`
- `src/api/chat.py`、`src/api/documents.py`、`src/api/versions.py`、`src/db/models.py`
- `src/config/settings.py`、`.env.example`、`pyproject.toml`、`README.md`
- `tests/unit/agent/test_document_tools.py`、`tests/unit/ingest/test_document_enrichment.py`、`tests/e2e/test_offline_workflow.py`
- `migrations/versions/20260918_01_add_chat_session_agent_run_step.py` 等既有 A/B 改动文件的格式修正。

工作区还保留原任务未提交的 A/B 文件；git diff 相对 HEAD 包括原任务改动，不应把整个 diff 当成本次新增实现。

## 未解决项与验证边界

1. **真实 PostgreSQL 未执行。** Docker 容器/镜像列表的只读检查被自动审批审核拒绝，原因是审核服务连接中断；没有绕过该拒绝。当前没有配置 `TEST_POSTGRES_URL`，因此专用回归明确跳过，尚不能宣称真实数据库端到端验证完成。
2. **真实模型与 live E2E 未执行。** 现有真实服务用例按配置跳过。异步请求行为用可控 HTTP transport 验证，未调用收费模型接口。
3. 迁移只在临时 SQLite 数据库实跑，没有更改现有业务数据库。部署前在目标环境执行 `alembic upgrade head`。
4. 标签筛选与 chunk ID 范围仍会载入内存，适合当前科研材料规模；大规模语料需另行优化。没有通过候选数量上限静默丢弃符合条件的材料。
5. 完整结果寻址、稳定引用编号、长程上下文摘要和并发 run 控制仍在 C；README 已明确这些尚未完成。

具备可访问的 PostgreSQL 测试库后，配置 `TEST_POSTGRES_URL=postgresql+asyncpg://…` 并运行：

```bash
python -m pytest tests/integration/test_postgres_document_registry.py -q
```

该测试仅操作自己创建的随机 schema，并在结束时回滚，不改业务表。

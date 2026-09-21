# Research RAG Agent → 长程 RAG Agent：设计方案与实施计划

> 状态：设计稿（待评审）｜ 日期：2026-09-18 ｜ 目标读者：实施 coding agent
> 关联文档：`.codex/plans/resume_core_project_implementation_plan.md`（Phase 0-6，RAG 核心补全）
> 本文档只讲方案，不含实现代码。实施时按第 10 节 Phase A-F 逐个交付。

---

## 0. 设计假设（若与预期不符，先改这一节）

1. **Agent 形态**：单 Agent + 工具循环（Plan-and-Execute 混合 bounded ReAct），不做多 Agent 协作。
   理由：轨迹可解释、成本可控、评测口径清晰；面试时更容易讲透"为什么选这个模式"，而不是堆概念。
2. **长程主场景**：论文精读与跨材料对比 —— 精读新上传的论文/汇报 → 与已有材料、历史决策对比 → 产出提纲或结论。
   理由：这是唯一能同时压测"多轮 + 长程 + 记忆 + 引用"的场景。
3. **记忆层**：只保留三层 —— L0 原始对话、L1 事实记忆（`Memory` 表）、L2 会话滚动摘要。不做情景事件层与实体关系层。
   理由：三层已经覆盖"多轮 + 跨会话事实 + 上下文压缩"，再加层的收益递减、评测成本陡增。
4. **数据源**：只考虑三种 —— PDF（论文/汇报）、Markdown（笔记/方案）、对话本身。不做 PPTX/DOCX，不做 URL 抓取、图片与音频。
5. **交付顺序**：批次一先把"单 Agent 多轮 + 长程闭环"做通（Phase A→B→C），再补解析深度与记忆（Phase F→D），最后评测（Phase E）。
6. **向后兼容**：不破坏现有 API 契约，`POST /api/v1/chat/message` 的响应字段只增不减；`frontend/app.py` 在 Phase A 末仍能跑。
7. **运行环境**：代码在 WSL / conda `rag` 环境执行。`VECTOR_STORE_BACKEND=memory` + `LLM_PROVIDER=local` 必须能离线跑通全部新增测试。

---

## 1. 现状盘点

### 1.1 可直接复用的资产

| 能力 | 位置 | 状态 |
|---|---|---|
| PDF 解析（页码 1-based、字级 bbox） | `src/core/ingest/pdf_parser.py` | 可用，但只有 `get_text("text", sort=True)` |
| Markdown 解析（heading path + 行号） | `src/core/ingest/markdown_parser.py` | 可用 |
| 清洗 / 分块 / 去重 / 双存储 / 失败补偿 | `src/core/ingest/pipeline.py` | 完整，含 `content_hash` 去重与状态机 |
| Dense + BM25 + RRF + Reranker + 时间衰减 | `src/core/retrieval/hybrid_search.py` | 已真实接线，带 `retrieval_degraded` |
| 权威正文回填与项目鉴权 | `src/core/retrieval/hydration.py` | 可用 |
| 引用 locator + `[S\d]` 解析校验 | `src/core/citations.py` | 可用 |
| 原生 tool calling 雏形 | `src/core/agent/orchestrator.py`、`tools.py` | 有真实 tools/tool_calls、重复调用拒绝、scope 收窄 |
| 数据模型 | `src/db/models.py` | Project/Document/Chunk/Conversation/Memory/VersionLog |
| Prompt 轨迹日志 | `src/utils/llm_client.py`、`scripts/view_prompt_logs.py` | 仅文件级 jsonl，未入库 |

### 1.2 关键缺口（按"是不是 Agent"排序）

| # | 缺口 | 证据 |
|---|---|---|
| 1 | 多轮是假的 | `src/api/chat.py:20` 传 `history=None  # 暂时不处理历史对话`；`Conversation` 表从未被写入；`GET /chat/sessions/{id}` 返回硬编码 mock |
| 2 | 循环是固定 4 步 for | `orchestrator.py:_handle_with_tools` 的 `for step in range(4)`，无 plan、无重规划、无预算、无 checkpoint |
| 3 | 无 Run/Step 概念 | `tool_trace` 只存在响应体里，不落库、不可回放、不可断点续跑 |
| 4 | 无上下文装配层 | 没有 token 预算、没有滚动摘要；tool 结果全量回灌上下文，长程必爆 |
| 5 | 记忆是死的 | `extracted_memories` 恒为 `[]`；无自动抽取、无冲突检测；`VersionLog` 只在 `PATCH /memories/{id}/status` 时写 |
| 6 | 无文档注册表 | `Document` 只有 `filename/file_type/status`，无 title/作者/年份/类型/标签/摘要；**没有 `GET /documents` 列表接口**，只有 upload 和 status |
| 7 | 工具面太窄 | 只有 `search_knowledge` / `get_document_section` / `save_memory`，无法"列出我上传的材料""按章节连续精读" |
| 8 | 解析偏调库 | `pdf_parser_enhanced.py` 的多栏重建/表格/图注判断是死代码（`_is_table_block` 恒 `return False`）且未接入 pipeline；`is_scanned` 算出来了但 OCR 从没被调用（`use_ocr` 是摆设）；仅支持 pdf/md |
| 9 | 调度器无状态 | `src/api/dependencies.py` 每次请求新建 `AgentOrchestrator`，无单例、无连接复用、无并发预算 |

### 1.3 已确认可用的测试基线

- `tests/unit/`：citations、hydration、orchestrator citations、tool calling、ingest contract、retrieval pipeline。
- `tests/e2e/test_offline_workflow.py`：离线四文档上传 → 检索 → 回答 → 引用，是新增长程能力的主要回归载体。
- `tests/e2e/test_live_stack.py`、`tests/integration/test_live_model_contracts.py`：需要真实服务，标记为 live。

**实施第一步不是写新功能，而是确认 `pytest -m unit` 与 `pytest -m e2e` 在当前环境全绿**，否则新老失败会互相掩盖。

---

## 2. 目标：什么算 RAG Agent

### 2.1 完成定义（DoD）

同时满足以下 6 条才允许在简历/README 里继续叫 Agent：

1. **多轮**：同一 session 的第 N 轮能引用第 1 轮的结论；服务重启后历史仍在。
2. **长程**：单个 run 平均 ≥ 3 步、最大 ≥ 8 步，且每步都有落库轨迹；中断后可从最后一步 resume。
3. **自主选工具**：工具由模型通过原生 `tool_calls` 选择，代码不得硬编码"先检索再生成"。
4. **有界**：max_steps / 重复调用 / 无进展 / token / wall-clock 五道闸门，超限显式终止并报告原因。
5. **有记忆**：run 结束后能自动产生结构化记忆；新决策与旧记忆冲突时会标旧为 outdated 并记录理由。
6. **可评测**：有 ≥ 20 条 agent 评测集，能断言"工具选择 + 轨迹 + 引用闭合 + 步数 + 延迟"。

### 2.2 与既有 Phase 0-6 计划的关系

既有计划的 Phase 1-3（摄入、引用、混合检索）在当前代码里已基本落地，剩余缺口是 Phase 4-5（评测集）与 Phase 6（真实 tool calling）。
**本文档的 Phase A-D 是 Phase 6 的展开与升级**；Phase E 复用 Phase 4-5 的评测框架；Phase F 回应"PDF 解析只会调库"的面试追问。

---

## 3. 目标架构

### 3.1 三层状态机

```
ChatSession（长期，跨多轮，可能跨天）
   └── AgentRun（一次长程任务：goal / plan / state / 预算 / checkpoint）
          └── AgentStep（thought → tool_call → observation → reflect）
```

| 层 | 生命周期 | 持久化 | 职责 |
|---|---|---|---|
| ChatSession | 周～月 | `chat_session` + `conversation` | 承载多轮、滚动摘要、关联 project |
| AgentRun | 分钟～小时 | `agent_run` | 承载 goal、plan、scratchpad state、预算、中断/恢复 |
| AgentStep | 秒 | `agent_step` | 可回放的最小单位：模型输出、工具、参数、结果摘要、耗时、token |

### 3.2 一次长程 Run 的生命周期

```
用户消息
  → Router：chat / qa / record / task（便宜模型或规则，可写死兜底）
  → 若 task：Planner 产出 JSON plan（落 agent_run.plan），否则单步执行
  → Executor 循环：
       Think → ToolCall → Observe → 写入 state / result_ref
       → 若偏离 plan 或无进展 → Replan（有界，最多 1 次）
  → Reflection：evidence coverage 检查，缺证据则有界补检索
  → Finalize：生成带 [S\d] 的答案 → citation validator
  → 收尾（后台）：抽取 memory、更新滚动摘要、写 trace 汇总
```

### 3.3 设计模式清单（面试可直接讲）

| 模式 | 本项目落点 | 一句话解释 |
|---|---|---|
| Router（意图路由） | `AgentRouter` | 分流闲聊/问答/记录/长任务，避免所有请求都走重循环 |
| Plan-and-Execute | `Planner` + `agent_run.plan` | 任务型先出步骤清单并落库，防止长程跑偏 |
| bounded ReAct | `Executor` 每步 think→act→observe | 单步内自适应性，但用 max_steps 封顶 |
| Reflexion / Critic | `Reflector` | 收尾前检查证据覆盖，不足则有界补检索 |
| Working memory vs 长期记忆 | `agent_run.state` / `Memory` + 滚动摘要 | scratchpad 是易失工作台，Memory 是可检索资产 |
| Context assembly + token budget | `ContextAssembler` | 按优先级装配上下文，超预算按优先级裁剪 |
| Tool result 摘要化 + `result_ref` | `agent_step.result_summary/result_ref` | 大结果落库只回传摘要，长程不爆上下文 |
| Checkpoint & resume | `agent_run`/`agent_step` 落库 | 长程的工程本质：可中断、可恢复、可回放 |
| Loop guard | 五道闸门 | max_steps / 重复签名 / 无进展 / token / wall-clock |
| Human-in-the-loop interrupt | `ask_user` 工具 + `waiting_user` 状态 | 需要澄清或有副作用时暂停，等用户输入再续跑 |
| Registry + schema 校验 + scope 收窄 | 已有 `ResearchToolRegistry` | 工具是权限边界，模型不能放大调用方 scope |
| Observability / trace | `agent_step` + `GET /chat/runs/{id}` | 每步可回放，评测与排障共用同一份数据 |

明确**不做**的：多 Agent 协作、self-consistency 投票、无界递归子任务。这些在单机科研场景收益低、评测成本高。

---

## 4. 数据模型改造

### 4.1 新增表

**`chat_session`**

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | UUID |
| title | String(255) | 首轮消息截断生成，可改 |
| project_ids | JSON | 会话默认项目范围 |
| status | String(20) | active / archived |
| rolling_summary | Text | 早期对话的滚动摘要 |
| summary_upto_turn | Integer | 摘要已覆盖到第几轮，支持增量压缩 |
| last_active_at / created_at / updated_at | DateTime | 列表排序用 |

**`agent_run`**

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | UUID |
| session_id | String(36) FK chat_session.id | 所属会话 |
| run_type | String(20) | chat / qa / research / report / ingest |
| goal | Text | 本 run 的目标（Planner 改写后的版本） |
| status | String(20) | pending / running / waiting_user / completed / failed / cancelled |
| plan | JSON | `[{step_id, intent, tool_hint, status, result_ref}]` |
| state | JSON | scratchpad：已读文档、notes、未决问题、证据清单 |
| step_count / max_steps | Integer | 步数与上限 |
| prompt_tokens / completion_tokens | Integer | 成本核算 |
| error | Text | 失败原因 |
| started_at / finished_at / created_at / updated_at | DateTime | 时长统计 |

**`agent_step`**

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | UUID |
| run_id | String(36) FK agent_run.id, index | 所属 run |
| step_index | Integer | 从 0 开始，与 run 内顺序一致 |
| step_type | String(20) | plan / thought / tool_call / observation / reflection / final / error |
| tool_name | String(64) | 非 tool_call 为空 |
| arguments | JSON | 校验后的参数 |
| result_summary | Text | **回灌模型的部分**，≤ 800 字 |
| result_ref | String(512) | 完整结果的寻址：chunk ids / document ids / 相对路径 |
| error | Text | 工具错误原文 |
| duration_ms | Integer | 单步耗时 |
| prompt_tokens / completion_tokens / model | - | 单步成本 |
| created_at | DateTime | |

**`ingest_batch`**

| 字段 | 类型 | 说明 |
|---|---|---|
| id | String(36) PK | UUID |
| session_id | String(36) nullable | 哪次会话里插入的 |
| project_ids | JSON | 归属项目 |
| note | Text | 用户备注（如"导师给的 3 篇 baseline"） |
| file_count | Integer | 文件数 |
| created_at | DateTime | |

用途：支持"我上周插入的那批材料是什么"这类跨文档问题。

### 4.2 扩展既有表

**`document`** 新增：`title`、`doc_type`(paper/report/note/other；数据源只有 PDF/Markdown/对话，不设 slides)、`authors`(JSON)、`year`、`venue`、`tags`(JSON)、`summary`(Text)、`page_count`、`ingest_batch_id`、`extra_metadata`(JSON)。
索引：`doc_type`、`year`、`status`、`ingest_batch_id`。

**`conversation`**（语义就是"单条消息"，表名保持不变以降低迁移风险）新增：`turn_index`、`run_id`(FK agent_run.id, nullable)、`token_count`、`message_metadata`(JSON)。
`session_id` 语义升级为指向 `chat_session.id`；迁移时若存在历史裸 session_id，允许 nullable 或 backfill 成新 session。

**`memory`** 新增：`scope`(project/session/global)、`confidence`(Float)、`source_turn_index`、`tags`(JSON)。
`superseded_by`、`version_status`、`source_chunk_id` 已有，直接复用。

### 4.3 迁移与兼容

- 新增一个 Alembic 版本，命名风格对齐 `migrations/versions/20260806_01_add_document_content_hash.py`。
- 所有新列必须 nullable 或有 server_default，保证旧数据可迁移。
- 不做表重命名、不删列、不改已有列的语义（除 `conversation.session_id` 的 nullable 化），避免破坏 `tests/e2e/test_offline_workflow.py`。

---

## 5. 工具面 v2

### 5.1 工具清单

**知识 / 文献（Phase A-B）**

| 工具 | 参数要点 | 说明 |
|---|---|---|
| `search_knowledge` | query, project_ids?, top_k?, include_outdated?, content_types?, doc_types?, tags? | 扩展现有实现，支持 doc_type / tag 过滤 |
| `get_source` | chunk_id / document_id + page_start / section_path | 现有 `get_document_section` 的收敛版，保留旧名做别名 |
| `list_documents` 🆕 | project_ids?, doc_type?, tag?, year?, limit? | 回答"我上传了哪些论文/汇报" |
| `get_document_outline` 🆕 | document_id | 返回章节树 / 页码骨架，供精读规划 |
| `read_document_range` 🆕 | document_id, page_start/page_end 或 section_path, max_chars | **连续精读**的关键；按文档顺序返回相邻 chunk，而不是零散检索 |

**记忆（Phase D）**

| 工具 | 说明 |
|---|---|
| `save_memory` | 扩展 scope / confidence / tags |
| `search_memories` 🆕 | 语义 + 类型过滤检索长期记忆 |
| `list_memories` 🆕 | 按类型/状态列举（如"未完成的 todo"） |
| `update_memory` 🆕 | 改状态并写 VersionLog；是唯一会写记忆状态的工具 |

**控制 / 元（Phase C）**

| 工具 | 说明 |
|---|---|
| `ask_user` 🆕 | 澄清问题，触发 interrupt，run 进入 waiting_user |
| `create_plan` / `update_plan_step` | **内部状态，不暴露给模型**（更稳，避免模型把 plan 当玩具） |

**明确不做（首版）**：`ingest_document`（上传走 HTTP API）、`delete_document`、`export_report`（先由 LLM 直接输出 Markdown）。
有副作用的能力必须等权限、幂等与确认机制齐备后再进工具面。

### 5.2 权限、预算、幂等

- 沿用 `ResearchToolRegistry._scope`：模型只能收窄 project 范围，不能放大。
- 每个工具声明 `timeout_seconds` 与 `max_result_chars`；超限截断并写 `result_ref`。
- 读工具必须幂等；写工具（`save_memory` / `update_memory`）必须带幂等键（`run_id + step_index`），防止重试重复写。
- 工具异常统一转成 `{"error": ...}` 回传模型，让其自行修正；同一签名重复调用直接拒绝。

### 5.3 返回值契约（长程的关键）

工具返回**两层**：

```json
{
  "summary": "命中 5 段，主要涉及 Flash Attention 与显存占用",   // 回灌模型
  "result_ref": "chunks:8f2a...,c19b...;run:...;step:4",     // 落库寻址
  "items": [ { "source_id": "S1", "chunk_id": "...", "locator": {...}, "preview": "..." } ]
}
```

规则：`summary` ≤ 800 字回灌上下文；`items` 只带可引用的元信息与短预览；正文一律通过 `source_id` 在最终答案里被引用，需要全文时再调 `get_source` / `read_document_range`。
这样上下文占用与 run 步数解耦，是"长程不炸"的工程前提。

---

## 6. Agent 循环

### 6.1 控制流（伪代码）

```python
async def run(goal, session, project_ids, budget):
    run = create_run(session, goal, budget)          # status=running，立即落库
    plan = await route_and_plan(goal)                # 简单问答 → 单步 plan
    persist_plan(run, plan)

    while True:
        guard.check(run)                             # 五道闸门，超限抛显式异常
        context = assembler.build(run, session)      # 分层装配 + token 预算
        message = llm.generate_with_tools(context, TOOL_DEFINITIONS)

        if message.tool_calls:
            for call in message.tool_calls:
                args = validate(call, run.project_ids)   # JSON Schema + scope 收窄
                if args.tool == "ask_user":
                    pause(run, call)                     # status=waiting_user，等 POST /resume
                    return
                result = await registry.execute(args)    # 超时 / 异常统一回传
                persist_step(run, call, summarize(result), result_ref(result))
            continue

        if should_reflect(run):                      # 证据覆盖不足且预算允许（最多 1 次）
            persist_step(run, reflection_prompt())
            continue

        answer = message.content
        citations = validate_citations(answer, run.evidence)   # 闭合校验
        finalize(run, answer, citations)             # status=completed
        enqueue_post_turn(run)                       # 抽取记忆 + 更新滚动摘要
        return
```

### 6.2 终止条件与闸门

`src/config/settings.py` 新增（全部有默认值、可经 `.env` 覆盖）：

```text
agent_max_steps = 12
agent_max_tool_calls = 8
agent_max_repeated_signature = 1      # 同签名第 2 次直接拒
agent_max_wall_seconds = 120
agent_max_replans = 1
agent_step_context_tokens = 8000
agent_run_token_budget = 60000
context_recent_turns = 6
context_summary_trigger_turns = 12
```

要求：

- 任何一类闸门触发都必须写 `agent_run.status` + `error`，并返回可解释的终止原因，**不允许静默返回半成品**。
- 无进展检测：连续 2 步没有新增 `evidence`、`notes` 或 plan 状态变化 → 视为停滞，终止或强制 replan 一次。
- 所有闸门参数在 trace 里可见（评测时能区分"模型不行"和"预算太小"）。

### 6.3 反思与引用闭合

- **Reflection 只做一件事**：检查 plan 中每个子问题是否至少有 1 条 evidence 覆盖；缺失则生成一轮补充检索，最多 1 次，避免死循环。
- **引用编号当前有串号缺陷**：`orchestrator.py` 现在把所有 step 的检索结果拼成一个列表再重新编号 `S1..Sn`，早期步骤答案里的 `S1` 与最终 sources 对不上。Phase C 必须改成：**每个 tool 返回时就分配稳定的 `source_id` 并写入 `run.evidence`，最终答案只用这些 id**。
- 无证据时按既有 prompt 约定明确拒答，拒答准确率进 Phase E 的评测指标。

---

## 7. 上下文装配（ContextAssembler）

按优先级从高到低装配，总预算默认 8000 token（`agent_step_context_tokens`）：

| 优先级 | 内容 | 裁剪策略 |
|---|---|---|
| 1 | system prompt + 工具使用规范 | 固定，不裁 |
| 2 | run goal + plan + 当前步意图 | 固定，不裁 |
| 3 | pinned 事实：未完成 todo、当前 run 已确认的结论 | 固定，最多 10 条 |
| 4 | 相关长期记忆（按 query 检索 top-k） | 按分数截断 |
| 5 | 本轮证据（`run.evidence` 的 summary + source_id） | 按相关度截断，正文不回灌 |
| 6 | 最近 `context_recent_turns` 轮原文 | 从最旧开始丢 |
| 7 | 更早对话的滚动摘要 | 最后才丢 |

配套规则：

- 超过 `context_summary_trigger_turns`（默认 12 轮）时，后台把第 1..(N-6) 轮压进 `chat_session.rolling_summary`，并推进 `summary_upto_turn`，实现增量压缩。
- **工具大结果永不进上下文**，只进 `agent_step.result_summary` + `result_ref`（见 5.3）。
- 装配器必须是纯函数式输入输出（给定 run/session/memories 得到 messages），便于单测断言"预算内 + 优先级正确"。

---

## 8. 记忆系统

### 8.1 三层结构（L0 / L1 / L2）

| 层 | 载体 | 内容 | 检索方式 |
|---|---|---|---|
| L0 原始 | `conversation` | 逐轮原文 | 最近 N 轮直接注入；更早靠摘要 |
| L1 事实记忆 | `memory`（milestone/todo/decision/insight） | 结构化、可过期、可被取代 | 语义检索 + 类型/状态过滤 |
| L2 会话摘要 | `chat_session.rolling_summary` | 会话内已压缩的历史 | 每次装配都带上 |

边界要守住：

- L0 是**唯一事实来源**，L1 与 L2 都是派生物，任何时候都能从 L0 重建。配套一个 `scripts/rebuild_memory.py`，改完抽取 prompt 后可整库重跑。
- 一次 run 内的临时 notes 存在 `agent_run.state` 里，属于**运行态数据而非记忆层**；run 结束后有价值的部分经 L1 抽取沉淀下来。
- 明确不做：情景事件层（L3）、实体关系/知识图谱层（L4）。理由不是"做不了"，而是这两层在单机科研场景的收益需要额外评测集才能证明，先把三层做扎实。

### 8.2 写入：抽取 → 比对 → 取代

1. run 结束后异步触发抽取（结构化 JSON 输出，schema 对齐 `Memory`），失败只记日志不影响回答。
2. 对每条候选记忆，在同 project 下做语义检索（top 3）+ 类型匹配，判断是否冲突：
   - 无冲突 → 新建 `Memory`，写 Milvus。
   - 冲突 → 旧记忆置 `version_status='outdated'`、`superseded_by=新id`，写 `VersionLog(action='outdated', reason=LLM 给出的理由)`，再建新记忆。
3. 幂等：抽取结果按 `run_id + 记忆指纹` 去重，重复触发不产生重复记忆。

### 8.3 读取：注入与主动提醒

- `ContextAssembler` 每步按当前 query 检索 L1，注入 top-k；未完成 todo 作为 pinned 常驻。
- **主动冲突提醒**（对应设计文档 UC-09）：当本轮 query 与某条 `outdated` 记忆高度相关时，在装配结果里附上"该结论已于 X 时间被 Y 取代"，让模型主动提醒用户。
- 记忆检索同样受 project 鉴权约束，不得跨项目泄漏。

---

## 9. 材料管理（"我插入的论文 / 汇报"）

### 9.1 文档注册表

`Document` 扩展为可检索的论文/材料台账，字段见 4.2。核心是让 Agent 能回答：

- "我上传过哪些论文？" → `list_documents`
- "这篇论文讲了什么？" → `get_document_outline` + `read_document_range`
- "我上周插入的那批材料是什么？" → `ingest_batch` + `list_documents(ingest_batch_id=...)`
- "和三个月前那篇对比一下" → `search_knowledge` + `read_document_range` 组合

### 9.2 上传流程增强

```
POST /documents/upload
  → 创建 ingest_batch（可选 note）
  → 落盘 + 校验（沿用现有实现）
  → 解析 / 清洗 / 分块 / 双存储（沿用 DocumentIngestPipeline）
  → 元数据补全：title/authors/year（首页启发式）
  → 异步：LLM 生成 summary + tags（失败不阻塞，status 仍为 ready）
  → 返回 document_id + status
```

同时新增：

- `GET /documents`：按 project / doc_type / tag / year / status 过滤 + 分页。
- `GET /documents/{id}`：元数据 + outline + chunk 统计 + ingest_batch。
- `PATCH /documents/{id}`：人工订正 title / tags / doc_type / year（Agent 不自动改，避免污染）。
- `DELETE /documents/{id}`：软删（status=archived）+ 向量侧删除，建议后置到 Phase F 之后。

### 9.3 outline 生成

- Markdown：直接用已有 heading path + 行号。
- PDF：字号/加粗启发式识别一级标题，结合页码生成骨架，落 `chunk_metadata.outline`（Phase F 可用更好的 layout 方案替换，但接口不变）。

---

## 10. 分阶段实施计划

总览（估计为纯工程日，不含评测集人工标注）。**批次一先把单 Agent 多轮闭环做通，再进批次二**：

| 批次 | Phase | 内容 | 预估 | 出口条件 |
|---|---|---|---|---|
| 一 | A | 会话与轨迹地基 | 1.5-2d | 多轮真实、历史落库、trace 可查 |
| 一 | B | 文档注册表 + 文献工具 | 1.5-2d | 能回答"我上传了哪些材料" |
| 一 | C | Agent 循环 v2 | 2-3d | 计划/执行/反思/闸门/resume 通过轨迹断言 |
| 二 | F | PDF 解析深挖（原理与选型见 §15） | 2-3d | locator accuracy ≥ 0.95，扫描件不再静默失败 |
| 二 | D | 记忆闭环（L0/L1/L2） | 1.5-2d | 冲突检测 + supersede + 滚动摘要生效 |
| 三 | E | Agent 评测与可解释 | 1.5-2d | 20+ 条评测集 + 指标报告 |

批次一结束时，系统已经是"能多轮、能长程、记得住你插过什么"的 Agent；批次二补的是**解析质量**与**跨会话记忆**这两块深度；批次三把效果变成数字。

**每完成一个 Phase 必须：`pytest` 全绿 → 更新 README/docs 状态 → 单独 commit。禁止跨 Phase 堆积未验证改动。**

### Phase A：会话与轨迹地基

**目标**：把"多轮"从假的变成真的，并让每一步可回放。

**任务**

1. 迁移：新增 `chat_session`、`agent_run`、`agent_step`；扩展 `conversation`（见 §4）。
2. API：`POST /chat/sessions`、`GET /chat/sessions`、`GET /chat/sessions/{id}`（真实消息列表 + 游标分页）、`GET /chat/runs/{run_id}`、`GET /chat/sessions/{id}/runs`。
3. `POST /chat/message` 改为：session 不存在则自动创建（兼容旧调用方）；从 DB 加载最近 N 轮注入 orchestrator；把 user/assistant 两条消息落库；`history` 请求参数保留但忽略。
4. 把现有 `tool_trace` 同步写入 `agent_step`（Phase A 可先只记 tool_call/observation，Phase C 再补齐 thought/plan/reflection）。
5. `GET /chat/sessions/{id}` 删除 mock，`src/api/versions.py` 的 mock 一并替换为真实 `VersionLog` 查询（顺手，约 0.2d）。

**验收**

- 同一 session 连问 3 轮，第 3 轮能引用第 1 轮的结论（用固定 fake LLM 断言注入的 messages 顺序与内容）。
- 重启服务后 `GET /chat/sessions/{id}` 仍返回完整历史。
- `GET /chat/runs/{run_id}` 返回每步的 tool、参数、结果摘要、耗时。

**测试**：`tests/unit/api/test_chat_sessions.py`（fake session factory）、`tests/unit/agent/test_history_assembly.py`；e2e 追加"三轮对话"用例。

### Phase B：文档注册表 + 文献工具

**目标**：让系统真正"记得"用户插入了哪些论文与汇报。

**任务**

1. `Document` 扩展字段 + 迁移 + 索引（见 §4.2）。
2. 上传流程接入 `ingest_batch`；首页启发式抽取 title/authors/year；异步 LLM 生成 `summary` + `tags`（失败不影响 `ready`）。
3. API：`GET /documents`、`GET /documents/{id}`、`PATCH /documents/{id}`（见 §9.2）。
4. outline 生成（见 §9.3）。
5. 新工具：`list_documents`、`get_document_outline`、`read_document_range`，并扩展 `search_knowledge` 的 `doc_types` / `tags` 过滤。

**验收**

- 上传 2 篇 PDF + 1 个 Markdown 后，问"我上传了哪些材料、分别讲什么"能正确列举并带引用。
- `list_documents(doc_type='paper')` 只返回论文；`read_document_range` 返回的是**文档顺序连续**的片段，而不是检索命中的零散片段。
- `PATCH` 订正 title/tags 后，再问同一问题能反映订正结果。

**测试**：`tests/unit/api/test_documents_registry.py`、`tests/unit/agent/test_document_tools.py`、e2e 追加材料盘点用例。

### Phase C：Agent 循环 v2

**目标**：把 `for step in range(4)` 换成有计划、有反思、有闸门、可恢复的状态机。

**任务**

1. 拆出 `src/core/agent/`：`router.py`、`planner.py`、`executor.py`、`reflector.py`、`context_assembler.py`、`guard.py`、`loop.py`（保留 `orchestrator.py` 作为对外门面，签名不变）。
2. `AgentRun` 落库 + 每步 checkpoint；新增 `POST /chat/runs/{id}/resume` 支持 `waiting_user` 续跑。
3. `ask_user` 工具 + interrupt 语义（`status=waiting_user`，返回 `pending_question`）。
4. 五道闸门（见 §6.2）与显式终止原因。
5. `ContextAssembler`（见 §7）+ tool result 摘要化（见 §5.3）。
6. 引用编号改为 run 内稳定 `source_id`（修 §6.3 的串号缺陷）。

**验收（轨迹断言，全部在测试里可验证）**

- 多步：至少发生 2 次真实模型调用，且第二步的工具参数依赖第一步的观察结果。
- 工具由模型选择：换一个不同的 fake 模型轨迹，工具序列随之改变（证明不是硬编码）。
- 闸门：重复签名、超步数、超时、超 token 四种情况各自显式终止并写明原因。
- 中断恢复：`ask_user` 后 run 进入 `waiting_user`，`POST /resume` 能带着答案继续跑完。
- 引用闭合：答案中的每个 `[S\d]` 都能映射到 run 内真实产生过的 chunk；越界引用进 `invalid_citation_ids`。

**测试**：`tests/unit/agent/test_loop_guards.py`、`test_planner_replan.py`、`test_interrupt_resume.py`、`test_citation_closure.py`。

### Phase D：记忆闭环（L0 / L1 / L2）

**目标**：让 `extracted_memories` 不再是空数组；只做 L0 原文、L1 事实记忆、L2 会话摘要三层（见 §8.1），不做情景事件与实体关系层。

**任务**

1. run 结束后异步抽取（结构化 JSON）+ 幂等去重（见 §8.2）。
2. 冲突检测 → 旧记忆 `outdated` + `superseded_by` + `VersionLog(reason)`。
3. `chat_session.rolling_summary` 增量压缩（`summary_upto_turn`）。
4. 新工具：`search_memories`、`list_memories`、`update_memory`。
5. `ContextAssembler` 注入相关记忆 + pinned todo + outdated 提醒（见 §8.3）。

**验收**

- 说"导师说要改用 Flash Attention"，下一轮问"之前的方案是什么"能答出旧方案已过时及原因。
- 重复触发抽取不会产生重复记忆（幂等断言）。
- 20 轮对话后，注入的上下文仍在上限内，且早期结论能通过摘要被答出。

**测试**：`tests/unit/agent/test_memory_extraction.py`、`test_conflict_supersede.py`、`test_context_budget.py`。

### Phase E：Agent 评测与可解释

**目标**：让"Agent 有效"变成可复现的数字。

**任务**

1. `eval/datasets/agent_v1.jsonl`，20-40 条，覆盖：多轮指代、跨文档对比、工具选择、拒答、记忆冲突、长程任务。
2. `scripts/evaluate_agent.py`：轨迹断言 + 工具选择准确率 + 工具成功率 + 平均/最大步数 + 端到端 P50/P95 + citation 指标。
3. 与单步 RAG 基线对照（同数据集，关掉 plan/reflect），产出一页结论。
4. 更新 README：架构图、模式说明、实测数字、已知限制。

**验收**：一条命令产出报告；报告含配置、模型、数据集版本、时间戳；结果可被重跑复现。

### Phase F：PDF 解析深挖

**目标**：不停留在"调库"这一层——把解析做成可量化、可解释、可替换的独立模块，并能说清每个库做了什么、为什么这么选。库原理、候选方案优劣势与选型标准见 §15。

**任务（F1 → F3，F1/F2 必做，F3 按需）**

**F1 阅读顺序与版面结构（约 1d）**

1. 清掉 `pdf_parser_enhanced.py` 的死代码（`_is_table_block` 恒 `return False`、`_detect_columns` 从未被调用），改成真正的版面分析：
   - 用 `page.get_text("dict")` 的 block/line/span + bbox 做 **XY-cut 递归投影切分**，得到多栏阅读顺序（替代 `sort=True` 在双栏文档上的失效）；
   - 用字号 / 字重 / 位置识别标题层级，产出 PDF 版 outline（§9.3 直接复用）；
   - 页眉 / 页脚 / 页码 / 脚注按"跨页重复 + 边缘位置 + 字号偏小"三条件识别，从正文剔除（现有 `pdf_cleaner.py` 的纯规则版升级为版面版）。
2. 解析产物落 `page.layout`（columns、reading_order、headers、footers、captions），供分块与 locator 复用——**分块顺序改为消费 reading_order，而不是消费内容流顺序**。

**F2 扫描件 OCR 兜底（约 0.5-1d）**

3. `is_scanned` 真正接上 OCR（`settings.use_ocr` 不再是摆设）：低文本密度页走 OCR，结果 + 置信度 + bbox 写回 page metadata，locator 仍能定位到该页。
4. 整页无文本或解析异常时必须显式失败（`status=failed` + 原因），不允许静默产出空 chunk 再标 `ready`。

**F3 表格 / 公式 / 图注（约 0.5-1d，按需）**

5. 先用 PyMuPDF 自带的 `page.find_tables()` 抽表格结构，落 `content_type='table'`；效果不够再考虑 §15.3 里的重型方案。
6. 公式与图注只做**识别与标记**（`formula` / `image_ref`），不追求转 LaTeX——除非后续确有需求。
7. 跨页表格 / 跨页段落合并，保持 page_start/page_end 的连续引用。

**fixture 集（必须人工标注，是这一 Phase 的验收基础）**

双栏论文页、扫描页、跨页表格、页眉页脚干扰页、公式页、图注页。每页人工标注正确的段落阅读顺序与页码，作为 ground truth 入库。

**验收**

- fixture 集上 reading-order 正确率与 locator accuracy ≥ 0.95。
- 扫描页不再产出空文本，且 OCR 页能被正常引用到页码。
- 解析失败显式化率 100%（不允许静默 `ready`）。
- 产出一页对比报告：自研版面层 vs PyMuPDF 原生 `sort=True` vs（可选）重型方案，用同一 fixture 集的数字说话，而不是主观论断。

---

## 11. 测试与评测矩阵

| 层级 | 覆盖重点 | 外部依赖 |
|---|---|---|
| Unit | 装配器预算与优先级、闸门、planner/replan、记忆抽取与取代、引用闭合、工具 schema 校验 | 无，全部 fake |
| Integration | 真实 PostgreSQL 迁移、Milvus 写入与过滤、BM25 通道、embedding/reranker 协议 | Docker 服务 |
| E2E（离线） | 上传 → 检索 → 多轮 → 长程 run → 引用 → trace 查询 | `VECTOR_STORE_BACKEND=memory` + `LLM_PROVIDER=local` |
| E2E（live） | 真实模型下的 tool calling 多步与失败恢复 | 真实 LLM / 模型服务 |
| Evaluation | agent 评测集指标、与单步 RAG 基线消融 | 固定模型与配置 |

离线 e2e 是硬要求：新增的长程能力必须在 `LLM_PROVIDER=local` 下有确定性的轨迹分支，否则 CI 只能靠真模型，回归会退化成人工抽查。

---

## 12. 风险与工程约束

| 风险 | 控制手段 |
|---|---|
| 长程改完把原有离线 e2e 打挂 | 每 Phase 先跑 `pytest -m unit` 与 `pytest -m e2e` 再动手；Alembic 只加不删 |
| 上下文/成本失控 | 五道闸门 + `agent_run` token 统计 + tool result 摘要化 |
| 模型不按预期调工具 | 保留固定 RAG 兜底路径（现有 `handle_message` 已有回退），并在 trace 里标注 `fallback_used` |
| 摘要丢失关键结论 | pinned 事实（未完成 todo、本次 run 已确认结论）不参与压缩 |
| 记忆越写越多、信噪比下降 | 抽取幂等 + 同 project 语义去重 + `confidence` 阈值 + 过期状态 |
| Milvus BM25 是本地全量打分，规模上不去 | README 写明为已知限制；留 `keyword_search` 接口，后续换原生 sparse 倒排 |
| 多进程/并发下 run 状态竞争 | 乐观锁（`agent_run.version`）或行级 `SELECT ... FOR UPDATE`；同一 session 串行化 run |
| 让模型改元数据污染知识库 | `PATCH /documents` 只走人工 API，不进工具面 |
| 解析方案选型靠主观拍脑袋 | Phase F 的 fixture 集 + 对比报告；重型库只作对照，用数字决定是否替换主链路 |
| 记忆层越做越复杂 | 硬性收敛到 L0/L1/L2（§8.1），新增层必须先用评测集证明收益 |

## 13. 交接说明（给实施 agent）

**先做这三件事**

1. 在 WSL / conda `rag` 环境跑 `pytest -m unit -q` 与 `pytest -m e2e -q`，记录基线结果（含失败项）。
2. 读 `.codex/plans/resume_core_project_implementation_plan.md` 与本文档，确认 §0 假设。
3. 按 Phase A 开一个分支，一次只交付一个 Phase。

**代码规范（沿袭现状，不引入新范式）**

- Python 3.10+，类型注解完整；异步用 `AsyncSession`；日志用 `loguru`。
- Ruff `line-length = 100`，规则集见 `pyproject.toml`；提交前跑 `ruff format` + `ruff check`。
- 迁移命名对齐 `migrations/versions/20260806_01_add_document_content_hash.py`。
- 不新增未在 `pyproject.toml` 声明的重依赖；引入任何新库先说明理由。

**边界**

- 不改 `Doudizhu-Arena/`、`Mandol-分层agent记忆系统/`、`简历/`、`投递记录/` 下的任何文件。
- 不动 `.env`（如需新增配置项，只改 `.env.example` 并在 README 说明）。
- 不提交任何 API Key、不 `git commit` 到主干（按 Phase 单独提交到分支）。
- 不为了让测试通过而降低断言强度；失败就如实暴露。
- 数据源范围锁定 PDF / Markdown / 对话本身；不要顺手加 PPTX、DOCX、URL 抓取。

**每个 Phase 的交付格式**

```
Phase X 完成
- 改动文件列表
- 新增/修改的测试与执行结果
- 验收条目的逐条证据（命令 + 输出摘要）
- 未解决项与遗留风险
```

## 14. 面试可讲点映射

| 面试可能问 | 对应实现 | 可讲的点 |
|---|---|---|
| "PDF 解析怎么做的？" | `src/core/ingest/` + Phase F + §15 | MuPDF 的 block/line/span 模型与 `sort=True` 的真实语义、XY-cut 阅读顺序、页眉页脚与脚注识别、跨页表格、扫描件 OCR 兜底、解析失败显式化 |
| "为什么用这个库？别的库呢？" | §15.1-15.3 对比表 | PyMuPDF / pdfplumber / Unstructured / Marker / MinerU / Docling / Nougat / Grobid / Camelot / PP-StructureV3 各自原理与代价，以及"用 fixture 数字决定替换"的选型标准 |
| "Agent 流程用了什么设计模式？" | §3.3、§6 | Router / Plan-and-Execute / bounded ReAct / Reflexion / checkpoint-resume / loop guard / tool registry 权限收窄 |
| "长程任务上下文怎么不爆？" | §5.3、§7 | tool result 摘要化 + `result_ref` 寻址 + 分层装配 + token 预算 + 滚动摘要 |
| "多轮和记忆怎么设计的？" | §8 | L0-L4 分层、结构化抽取、冲突检测与 supersede、VersionLog 审计、pinned 事实 |
| "怎么保证引用可信？" | §6.3、`src/core/citations.py` | locator 精确到页/章节行，引用编号在 run 内稳定，越界引用显式暴露 |
| "怎么证明 Agent 有效？" | §10 Phase E | 评测集 + 轨迹断言 + 步数/成功率/延迟 + 与单步 RAG 基线消融 |

---

## 15. 附录：PDF 解析的库原理、选型与验收

> 本节专门回答"你的 PDF 解析到底怎么做的""用了什么库""库的优劣势是什么"。面试可直接按 15.1 → 15.2 → 15.3 的顺序讲。

### 15.1 我们用的库，内部到底做了什么

现状：`src/core/ingest/pdf_parser.py` 调 `page.get_text("text", sort=True)` 取整页文本，再用 `get_text("dict")` / `get_text("words")` 取 bbox 与字号。PyMuPDF（`fitz`）是 **MuPDF**（Artifex 的 C 引擎）的 Python 绑定，链路如下：

1. **解析 PDF 对象层**：读 xref / trailer，解出页面树、内容流（content stream）、字体资源与 ToUnicode CMap。PDF 本身**只有"在某个坐标上画某个字形"的指令**——没有段落、没有阅读顺序、没有表格结构这些概念。
2. **解释绘图指令**：把内容流里的 `BT/ET`、`Tj/TJ`、`Tm/Td/T*` 等操作码还原成 glyph 序列，每个 glyph 带字体、字号、位置信息。
3. **结构化文本抽取**：MuPDF 的 structured text device 把 glyph 归并成 `block > line > span` 三级树（`get_text("dict")` 返回的就是这棵树；字符级要 `get_text("rawdict")`），并在过程中处理断词、连字符与连字（ligature）合并。
4. **坐标系**：PDF 用户空间原点在左下，fitz 转成左上原点（y 向下），因此 bbox 可直接与页面像素对齐——这是我们能做精确 locator 的基础。

**关键结论**：`get_text(..., sort=True)` 里的 `sort` **不是阅读顺序理解**，它只是把 block 按 (y, x) 重排。单栏顺序文档看起来是对的，双栏论文会被按 y 交错串行——这就是当前解析质量的真实上限。此外，表格线、公式、图注语义、扫描件 OCR，MuPDF 一概不建模，只能靠我们自己从 `bbox / font_size / 重复出现` 这些信号里推断。

这也解释了为什么本方案把版面层做成**自研模块**（Phase F1）而不是继续调库参数：原始信号（字符 + bbox + 字体 + 字号）已经足够，缺的是阅读顺序模型，而这一层恰恰是最能讲清楚"不是只会调库"的地方。

### 15.2 候选方案对比

| 方案 | 核心原理 | 优势 | 代价 | 本项目结论 |
|---|---|---|---|---|
| **PyMuPDF (fitz)** | MuPDF C 引擎；glyph → block/line/span 结构化文本 | 快、单依赖、体积小；字符级 bbox/字号/字体齐全；1.23+ 自带 `find_tables()` | 无版面与阅读顺序模型，`sort=True` 仅按 (y,x) 重排；表格/公式需自推 | ✅ 作为底座保留，其上自研版面层 |
| pdfplumber | 纯 Python，基于 pdfminer.six，暴露 char/line/rect/curve 对象 | 版面调试友好，能拿到表格框线，`extract_table` 开箱可用 | 比 MuPDF 慢一个量级、内存高；复杂字体/CMap 支持弱 | 备选：表格抽取对照与兜底 |
| pypdf | 纯 Python 高层 API | 极轻，适合元数据、合并、拆页 | 正文抽取与版面能力最弱 | ❌ 不用于正文抽取 |
| Unstructured (hi_res) | 版面检测模型 + OCR + 元素分类 | 开箱得到 title/narrative/table/list 等元素类型 | 依赖重（detectron2 等），安装与推理成本高、版本敏感 | 参考其元素分类思路 |
| Marker | 深度学习流水线（Surya OCR+版面+表格，texify 公式）→ Markdown | 论文转 Markdown 效果好，公式表格都能出 | 需 GPU；黑盒，难做 locator 级控制 | 质量上限对照实验 |
| MinerU (PDF-Extract-Kit) | 版面/公式/表格/OCR 多模型组合 | 中文论文支持好，输出 Markdown | 重依赖 + GPU，模型体积大，需适配层 | 对照实验候选 |
| Docling (IBM) | 版面分析 + TableFormer + OCR | 表格结构强，导出 Markdown/JSON，文档模型清晰 | 依赖较重，速度一般 | 对照实验候选 |
| Nougat | 端到端 VLM 直接生成 Markdown+LaTeX | 学术 PDF 公式与结构效果好 | 幻觉不可控，**无 bbox 坐标 → 拿不到可验证 locator** | ❌ 与"引用必须可定位"冲突 |
| Grobid | CRF/DL 抽取学术结构，输出 TEI XML | 标题/作者/章节/参考文献抽取专业 | 需常驻 Java 服务，不做通用正文与表格 | 可选用：只抽元数据 |
| Camelot | 基于框线(lattice)或空白(stream)的表格抽取 | 表格精度高，直接产出 DataFrame | 依赖 Ghostscript/OpenCV，配置挑剔 | 按需，滞后到 F3 |
| PP-StructureV3 (PaddleOCR) | 版面+表格+公式+阅读顺序一体化 | 中文场景强，直接输出阅读顺序 | Paddle 依赖重 | 备选：阅读顺序对照 |
| RapidOCR (ONNX) | 轻量 OCR 推理 | 无 GPU 可跑、依赖轻、易部署 | 仅 OCR，无版面能力 | ✅ F2 OCR 首选 |

### 15.3 我们的路线与选型标准

路线：

1. **底座保持 PyMuPDF**——轻、快，且我们需要的原始信号它全都给得到。
2. **版面层自研**（XY-cut 递归投影切分 + 页眉页脚/脚注规则 + 标题层级启发式）：算法经典、逻辑可解释、locator 完全可控，实现量级只有几十行核心逻辑。
3. **OCR 只做兜底**，只在 `is_scanned` 页触发，选轻量档（RapidOCR / PaddleOCR 轻量模型），不强绑 GPU。
4. **表格先用 `find_tables()`**，不够再上 Camelot / PP-Structure。
5. **重型方案（Marker / MinerU / Docling）只作对照实验**，用同一 fixture 集的数字决定要不要替换主链路，而不是凭感觉引入。

选型标准（评审任何新解析方案时按这五条打分，写进 README）：

1. **能否保留页码/bbox 级 locator** —— 拿不到坐标的方案直接淘汰（这是 Nougat 出局的原因）。
2. **依赖与部署成本** —— 是否需要 GPU、常驻服务、非 Python 运行时。
3. **中文支持** —— 我们的材料以中英文混合为主。
4. **速度** —— 单篇 50 页论文的解析耗时要进性能报告。
5. **可解释、可调试** —— 出问题时要能定位到具体页面与规则，而不是面对一个黑盒输出。

### 15.4 这一阶段要产出的材料（同时是面试素材）

1. `docs/pdf_parsing.md`：一页讲清用了什么库、库内部做了什么、`sort=True` 为什么不够、为什么自研版面层、怎么用数字选型。
2. `eval/fixtures/pdf/` + 人工标注的 ground truth：可复现的解析质量评测集（双栏、扫描、跨页表格、页眉页脚、公式、图注）。
3. 对比报告：自研版面层 vs PyMuPDF 原生 vs 重型方案（跑得动就跑）的 reading-order 正确率与 locator accuracy。
4. 解析层的接口冻结说明：`page.layout` 的字段契约，保证后续替换实现时上层分块与引用不需要改。

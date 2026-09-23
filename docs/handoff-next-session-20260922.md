# 新会话交接 Prompt

> **2026-09-23 接续更新：先读 `docs/minimal-live-results-20260922.md`。**
> 本文件下文保留原交接历史。新增最小批次已有 7 次真实生成，43,509 实测 token，额度已用完。
> 初版报告自动保存并跨进程重开成功；纠错修订的最后合法响应因强制补查撞到调用上限，
> 原 run 仍为 budget_exceeded。随后零新增推理恢复保存该真实响应，未改原失败状态。
> 最终报告在 `data/evaluations/minimal-live-20260922-v2/recovered-report/report.md`，
> JSON/Markdown 重开一致，4 份来源快照、6 条事实已核对；独立人工 rubric 评分仍未做。
> 新增必填栏目和补查额度保护，最新离线 121 passed / 6 skipped；最后的额度保护修复
> 没有再次跑真实自动任务。后续真实调用须另定预算，保留两批全部失败与恢复证据。

请基于 Research-RAG-Agent **当前实际工作区**继续实现和验证。先读取本文件及指定证据，
复核最新变更，再动手；不要只给架构建议或继续增加模块。当前最重要的目标是：
**用真实本地 Qwen 模型完成跨文档比较，交付可保存、可重新打开、可追溯的报告。**

## 1. 用户目标与范围

- 科研材料比较与证据核查；保留已有摄入、Dense/BM25/RRF/Reranker 检索基础。
- 首先完成真实比较报告，再验证多轮澄清修订、有界证据核查、取消和恢复。
- 不接 Mandol，不扩展外网搜索、任意代码执行、多 Agent、OCR 或新文档格式。
- 不以最少工具步数、自动记忆、大量模块拆分作为 Agent 或任务完成标准。
- 保留 B0 强单步 RAG、B1 修复后的既有工具循环、B2 研究报告工作流。
- 区分程序正确性、真实服务集成、真实模型效果；报告包含条件、基线差异、全部失败、
  token/费用和 P50/P95。20 开发 / 60 留出与 rubric 是起步建议，正式运行前评审并冻结。
- 用户关心项目能否作为简历重点：以真实交付、可靠性和可复现实验作为验收，不以新增模块数衡量。

## 2. 工作区与环境

- 仓库：`~/Research-RAG-Agent`。
- 本次交接时 HEAD：`312f2f9`，分支 main；**大量修改与新增文件尚未提交**。
  新会话先 `git status` / `git diff`，保留这些实现，不 reset/clean，不只检查 HEAD。
- openEuler 24.09 / aarch64；使用
  `~/miniconda3/envs/research_rag/bin/python`（Python 3.10.20）。
  shell 默认 Python 3.13.11 不是验收环境。
- 凭据与服务地址在 `.env`，只在程序内读取，不输出密钥或整个文件。
- 本地部署的真实 Qwen 通过兼容 HTTP 接口调用。`LLM_PROVIDER=local` 是确定性替身，
  **不能用它冒充本地 Qwen 推理**。当前 `.env` 的 provider 读出为 deepseek，客户端同样
  请求配置的 Qwen URL；真实 smoke 启动器显式映射为 openai。
- 沙箱内全套异步测试曾卡在事件循环唤醒，沙箱外通过。自动审批服务两次因连接中断
  无法执行命令，用户手动恢复通道后成功；不是服务/业务失败，不反复当成应用缺陷。

## 3. 必须准确区分的实际测试结果

### 已做过真实模型任务实验，但发生在较早的配置版本

冻结 v1：3 条开发题（比较、核查、无答案）× B0/B1/B2 × 1 次，共 **9 次任务**。
使用真实 HTTP API、PostgreSQL、Embedding、Reranker 和 `Qwen3.8-27B`，向量索引为内存。
六份合成 Markdown **6/6 上传 ready**；摘要增强关闭。随机 PostgreSQL schema 结束后已删除。

结果：**0/9 完成，没有报告产物**；5 次 ReadTimeout，4 次应用 context_tokens 预算耗尽。
实际 13 次生成请求，8 次返回 usage、5 次未知；已报告输入 26,666 / 输出 2,306 token，
保守累计 86,356；费用 unknown。P50/P95：B0 31.48/31.49s，B1 32.97/36.18s，
B2 34.77/35.12s。小样本及失败耗时不能外推生产延迟。

### 最新配置没有做过真实生成复测

最新完成的是：

- **116 passed、6 skipped，15.00 秒**：离线回归，含替身模型。
- 客户端定向测试 3 passed：验证超时与 low 参数透传，仍是 mock HTTP。
- 真实只读 `/models` 与 `/openapi.json` HTTP 200：
  `Qwen3.8-27B` / vLLM / **max_model_len=262144（256K）**；schema 接受 `reasoning_effort=low`。
- 更早真实客户端及 PostgreSQL 契约测试 **2 passed**。
- Gradio 6.10.0 的 48 个组件可构建；尚非浏览器交互验收。

**禁止将这些结果说成“新配置真实模型已通过”“low 已证明生效/提速”或“报告已真实生成”。**
用户最后追问“实际测试了吗”，必须先回答这一差别。

## 4. 已实施的功能与修复

已有：稳定 run 内 EvidenceRegistry、原文快照/哈希、坏参数观察、跨轮持久指令、
多维预算、无静默 fallback、session/run/checkpoint、任务查询/取消/恢复、结构化报告、
Markdown 下载、报告父版本、澄清与有界补查。主要通过离线测试，真实效果尚未验收。

近期修复：

- Reranker 用 `top_n`；保留 `relevance_score`；校验分数、索引和返回条数。
- 去掉生成客户端的 30 秒硬编码；记录 finish_reason，空响应/输出截断不当作成功。
- 模型观察省略检索排名等诊断字段，trace 仍保留完整信息，证据正文和编号不变。
- B0 也保存检索调用/观察/失败轨迹并检查累计预算。
- 评测入口冻结代码/评测器/语料哈希；核对 runtime；缺报告判失败；未知用量保留预留额。
- 最近加入较长超时、low 参数透传、Agent Top-K 强制上限、runtime 配置身份；
  smoke 入口不再硬编码旧预算，改为冻结 RunBudget.configured()。

重点代码：

```
src/core/agent/{orchestrator,runtime,tools,reports,service}.py
src/api/chat.py
src/db/{chat_store,models}.py
src/schemas/chat.py
src/utils/{llm_client,async_llm_client}.py
src/config/settings.py
frontend/app.py
migrations/versions/20260921_01_research_reports.py
scripts/{evaluate_tasks,run_research_smoke,run_offline_tests,run_shared_service_tests}.py
```

## 5. 用户指定的最新推理配置

下列值已写入当前 `.env` 并同步相关默认值/示例；运行中的应用需重新加载配置：

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

模型/工具次数仍为 6 / 8。生成超时取配置与 run 剩余时间较小者。
B0 和 Agent search 工具最多返回 3 个 chunk；模型要求 top_k=20 也会被限制，
观察记录 requested/effective Top-K。Dense/BM25 和重排候选池保持原配置。
比较任务仍须覆盖全部目标材料，可以按 document_id 读取，不能因 Top-K 小而漏掉材料。

256K 是服务单次输入+输出窗口。应用 context_tokens 仍用 UTF-8 字节保守上界，
不是精确 tokenizer token；整轮 total_tokens 又是跨调用累计。旧的 24,000 应用限制
在请求前拦截，不能解释成模型 256K 不够。旧 checkpoint 不重置预算。

## 6. 尚未解决的已知问题

1. **Reranker 仍缺显式聊天模板。** 本轮只读检查容器启动时间仍是
   2026-09-20 07:59:08 UTC，未启用 `--chat-template`。普通中文首都问题仍把法国巴黎排第一。
   服务可调用不等于排序质量合格。已有部署诊断建议追加镜像内官方模板：
   `/vllm-workspace/vllm/examples/pooling/score/template/qwen3_reranker.jinja`。
   外部 Compose 为 `~/qwen3-models/docker-compose.yml`；**尚未修改或重启它**。
   应先只读复核最新状态，不能假设部署至今未被用户修改。
2. **Milvus 未接通。** 上次 `127.0.0.1:19530` errno 111，真实持久向量与重启未验收。
   仓库有独立测试 Compose，但未启动；需确认 ARM64 镜像/资源，不删除业务集合。
3. **low 实际效果待验证。** OpenAPI 支持只说明参数契约；需检查当前服务/模板如何应用它。
4. 2,048 输出上限可能仍不足以容纳思考与结构化报告；必须检查 finish_reason 和 usage，
   不要通过空回答、截断 JSON、不断自动重试掩盖问题。
5. 真实 PostgreSQL 迁移、浏览器操作、三轮修订与中断恢复仍须独立验收。
6. 评测器对多轮 waiting_user/resume 的完整支持和人工 rubric 评分尚需继续检查；
   不要因为应用有接口就宣称评测器已覆盖全部流程。

## 7. 建议接续顺序与预算边界

1. 先复核实际 checkout、配置和证据，简短确认范围，不重写整套架构。
2. 准备最小的新版本真实生成/单比较任务验证：明确模型、low、超时、调用次数和
   token 上限，保留原始响应、finish_reason、usage、全部失败。先证明一次完整生成，
   不一开始就重跑全部基线或扩展 80 题。
3. 处理服务模板与执行缺陷，完成一条真实“摄入→定位→读原文→比较→保存→重开”流程。
   每个阶段分别记录内存向量/Milvus、mock/真实模型等边界。
4. 冻结新版本小规模 B0/B1/B2 对照，再验证澄清修订、有界核查和恢复。
5. 最后评审 20/60 题规模、标注工作量、rubric 和正式运行预算。

**授权记录：**用户先前批准过 9 次任务、最多 54 个生成请求、1,152,000 token 的
v1 实验；9 次任务已全部用完。随后只批准恢复离线回归和只读模型元数据核验，
明确没有追加模型任务实验。**新一批真实生成的具体预算尚未确认**，不要把剩余调用
上限当作自动获得更多任务运行名额。先把新实验准备成可审阅方案，再按新会话的用户授权执行。
此前也没有获得更改/重启外部模型容器的执行授权；需要修改时先准备具体差异与复验步骤。

## 8. 阅读顺序、证据与命令

优先读：

1. `docs/local-inference-and-project-readiness-20260922.md`
2. `docs/evaluation-continuation-20260922.md`
3. `docs/implementation-plan-20260921.md`
4. `docs/reranker_deployment_diagnosis_20260921.md`
5. `docs/model_api_configuration.md`

实际产物（位于 git 忽略的 data 内，勿丢弃）：

```
data/evaluations/research-smoke-20260922-v1/
  frozen.json
  results/{manifest.json,samples.jsonl,metrics.json,summary.md}
  source/                 # v1 实验源码，当前工作区已改动
  setup.json              # 上传及随机 schema 删除确认
  service-probe.json
  context-replay-after-fix.json
  verification/           # 修改前/阶段/最终测试的 XML 与源码哈希

data/evaluations/local-inference-config-20260922/
  protocol.json           # 实际 256K 与 reasoning_effort schema
  protocol-probe.py
  offline-results.xml     # 最新 116 passed / 6 skipped
  client-results.xml
  source_hashes.json
```

```bash
cd ~/Research-RAG-Agent
git status --short --branch
~/miniconda3/envs/research_rag/bin/python scripts/run_offline_tests.py
# 下列入口会使用真实服务；按明确的实验/测试范围执行。
~/miniconda3/envs/research_rag/bin/python scripts/run_shared_service_tests.py --suite contracts
# 当前 smoke 入口是完整 3×3，不能拿它冒充一次最小生成探针：
# python scripts/run_research_smoke.py --output data/evaluations/<new-version>
```

历史 v1 manifest 的代码哈希与当前代码不同是预期行为；创建新版本，不改旧哈希使它“通过”。
人工评分尚未完成就保留 null；本地部署没有已知 API 单价时保留费用 unknown，不虚构收益。

最终交付应回答：这轮到底发了哪些真实请求、是否有可打开的报告、哪些问题仍失败，
以及下一步还需什么。不要把 mock 的通过数作为真实模型任务成功的替代。

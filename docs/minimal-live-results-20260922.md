# 最小真实生成与报告交付（2026-09-23 更新）

**已经有真实 Qwen 生成、保存到 PostgreSQL、由新进程重新打开并核对出处的报告。**
初版自动完成保存；质量核对后，修订任务在调用上限处失败。最终完整修订内容来自其第 3 次
真实响应，经显式、零新增推理的产物恢复保存。原修订 run 仍为 budget_exceeded，
没有把它改成成功，也没有把恢复过程当作新模型任务成功。

最终可读报告：
`data/evaluations/minimal-live-20260922-v2/recovered-report/report.md`。
报告 ID `17a11634-e735-4fba-8666-c3b33b2c1bee`；父报告
`de2d5ea8-af19-4911-be82-53cb5f2c5e6a` 保留。

## 最新真实证据与预算

用户明确批准 EPERM 后重试；后续执行审批另批准一条纠错修订，最多再用 3 个生成请求。
整个 v2 仍限制 7 生成请求 / 132,096 token；13 次 Embedding、1 次 Reranker 均在辅助预算内。
使用 Qwen3.8-27B、temperature=0、low、180 秒请求超时、2,048 单次输出，串行运行。
本地服务单价未知，费用保持 unknown。没有追加未授权的模型调用。

| 阶段 | 实际结果 | 生成 / 工具次数 | 输入 / 输出 token | 耗时 |
|---|---|---|---|---|
| 最小探针 | 完整正确回答 Alpha，stop；附加解释违反 exact-only 格式，原严格判定 false 保留 | 1 / 0 | 75 / 86 | HTTP 2.86 秒 |
| 首次摄入/提交 | 6/6 ready；入口会话 ID 超过 PostgreSQL 36 字符，提交 HTTP 500，未创建模型任务 | 0 / 0 | 无生成 | 初始化不计入任务延迟 |
| 修正 ID 后比较 | 6/6 ready；真实 list→search→读取两份原文→submit_report→保存→新进程重开 | 3 / 5 | 12,116 / 2,487 | 86.67 秒 |
| 纠错修订 | 前两次缺字段；第三次提交完整合法报告，但强制补查导致 model_calls 预算终止 | 3 / 3 | 24,181 / 4,564 | 156.90 秒 |
| 显式产物恢复 | 原样保存最后一次合法结构化响应，新进程重开通过；原失败 run 不变 | 0 / 0 | 无新增 | 不作为模型任务耗时 |

v2 合计 **7 次真实生成响应，输入 36,372 / 输出 7,137，总计 43,509 token**，全部有 usage。
v1 的沙箱连接失败仍保留 2,335 unknown 预留；跨两版本保守记账为 45,844，不把预留算作实测。
7 个生成 HTTP 响应的 nearest-rank P50/P95 为 **42.10 / 69.32 秒**。
比较和修订各仅 1 例，各自 P50/P95 均等于单例耗时 86.67 / 156.90 秒；这些不是可靠的
生产延迟估计，更不是成功率对照。B0/B1 未在本批运行，不声称相对收益。

## 报告与出处复核

最终报告保留原始第 7 次模型响应的全部结构化字段，`structured_payload_unchanged=true`。
保存进程 PID 1299186，重开进程 PID 1299293。JSON 和下载 Markdown 完全一致；
4 份证据的文档 ID、chunk 范围、文档 SHA-256、正文 SHA-256、数据库快照与原文件均匹配。

Codex 对照原文逐条复核：6/6 比较行均有原文支持，Alpha/Beta 的方法、数据集、7B 模型、
8/16 GB、长度 1024/2048、batch 1/4、81%/86% 和局限性正确。推荐限于记录中的配置；
不把跨数据集准确率当成配对优势；能耗缺测和更改 Beta 配置后的可行性明确保持未知。
这是逐条来源复核，独立人工 rubric 评分仍为 null。

初版报告的两个质量问题完整保留：缺少不可比/未解决栏目，推荐包含无测量支持的参数调整暗示。
不将初版的 completed 状态直接当作全部质量条件通过。

## 已修复的具体缺陷与测试边界

1. 实验入口使用标准 UUID，修复会话 ID 超过 VARCHAR(36) 的提交错误。
2. 报告的 incomparable/unresolved 改为显式必填，不再把遗漏默认为空列表。
   真实修订的前两次不完整提交已被拒绝，完整错误和原响应均保留。
3. 补查是最多一次；模型/工具额度不足以继续时，已有合法、明确保留缺证据的报告应直接保存为
   insufficient_evidence，不再因强制补查丢弃报告。增加额度边界离线回归。
4. 报告指令明确限定推荐只适用于实测配置；未经测量的参数调整仍须标为未知。

最终离线回归：**121 passed / 6 skipped，15.31 秒**。此前必填字段修复阶段为
119 passed / 6 skipped，15.50 秒。最新预算边界修复没有再发真实模型请求复测；
本轮实际完成的是旧失败任务的显式产物恢复，不冒充修复后的自动修订成功。

边界仍是：真实 PostgreSQL create_all，内存向量；没有验收 Milvus、迁移升级或浏览器交互。
Reranker 仍缺评分模板；此次 Top-3 检索漏掉 Beta，模型随后按 ID 读取两份目标原文补齐覆盖。
low 参数确实随真实请求发送，但模型仍输出较长解释，不能宣称模板已正确应用或提速。
两次响应输出恰为 2,048 且 finish_reason=tool_calls，部分必填字段遗漏；不能仅凭终止字段
认定报告完整，也不能无证据断言服务端发生截断。

证据根目录 `data/evaluations/minimal-live-20260922-v2/`：
`summary.json` 汇总全部请求；各阶段保存原始 HTTP、run、usage、失败和源码冻结；
`recovered-report/{recovery,reopened,semantic-review}.json` 记录恢复、重开与语义核对；
`verification/` 保存两次离线 XML 与源码哈希。两个实验 PostgreSQL schema 均保留，未删业务数据。

后续若继续真实验证，应另定预算，重点检验最新代码自动保存带缺证据报告、模型输出格式和
Reranker 模板；不扩大题量或增加架构模块。

## 历史 v1：首次沙箱阻塞（保留）

**尚未通过最小真实生成；比较、保存报告、重新打开、核对出处均未进入实测。**
本次调用真实 HTTP 客户端时，沙箱禁止 TCP 连接。不能把此次连接失败记为模型能力失败，
也不能把新增离线测试通过记为真实模型成功。

## 实际代码和旧证据复核

- 基于 main / `312f2f9` 的未提交工作区，未 reset、clean 或覆盖已有修改。
- 交接列出的最新离线 XML：122 项，116 passed / 6 skipped；客户端定向 XML：3 passed。
- 最新证据中的 118 个源码/测试文件均存在，SHA-256 全部与本次开始时工作区一致。
- 再次只读读取 Reranker 容器：running，启动时间仍为 2026-09-20 07:59:08.73179831Z，
  max-model-len=1024，未配置 --chat-template。没有修改或重启服务。

## 本批预算及实际请求

预算在对话中展示后，用户回复“请继续”。采用
`minimal-live-budget-20260922.md` 的 1 探针 + 1 B2 比较任务，最多 7 个生成请求、
132,096 个保守计量 token，low / 180 秒 / 2,048 输出 token，无自动重试。

| 项目 | 本次实测 |
|---|---|
| 生成客户端尝试 | 1，使用实际 LLMClient.acomplete 和真实 HTTP transport |
| 模型响应 | 0；未收到 HTTP 状态码、finish_reason 或 usage |
| 失败 | ConnectError: All connection attempts failed，约 0.01 秒 |
| 独立连接诊断 | TCP connect_ex 返回 errno=1 / EPERM；该诊断不发模型请求 |
| Embedding / Reranker 推理 | 0 / 0 |
| 比较任务 / 摄入 | 0 / 0；探针失败后停止 |
| 保守 token 预留 | 2,335；unknown_usage_calls=1，不是实际消耗 |
| 实测输入/输出 token | 未知，不能把账本的 0 当作零消耗 |
| 费用 | unknown |
| 报告 | 无 |
| P50 / P95 | 不报告；没有完成的模型响应或比较任务 |

运行时权限策略已切换为禁止提权（approval policy=never），因此无法按此前方式在沙箱外
执行。未请求被禁止的提权、换通道绕过限制或偷偷追加生成重试。

## 本次实现与离线检查

新增 `scripts/run_minimal_live.py`，复用既有 Agent、摄入、检索、报告 API：

- prepare 冻结源码/配置/语料；run 检查冻结身份并用一次性 started.json 防止重复消费预算。
- 先真实生成探针，完整正确且 finish_reason=stop 才启动单条 B2 比较。
- 六份既有合成文档共享候选库，选定 Alpha/Beta，保留完整请求、原始响应及失败。
- 计划保存独立 PostgreSQL schema，在生成进程退出后由新进程通过 HTTP 重开报告，
  比较 JSON/Markdown，再核对原文、chunk 和哈希。这部分本轮没有实际运行。
- 来源字面匹配不等于语义支持；脚本把 semantic_review 留为 pending，仍须逐条复核。

新增两项离线预算/记录测试：**2 passed / 122 deselected，2.97 秒**。
使用 MockTransport，仅验证原始响应与 usage 记录、重复探针拦截、超时保留预留额度。
开始时发起的完整离线回归在执行通道切换后进程不可查询，未产出 XML；不声称本轮完整回归通过。
Python 编译及 git diff --check 通过。既有应用架构未扩展。

## 证据及接续条件

目录：`data/evaluations/minimal-live-20260922-v1/`（git 忽略，但保留本地）。

- `frozen.json`、`source/`：实际运行源码、配置及六份语料。
- `started.json`、`http-generation-01.json`：唯一一次客户端尝试，原始请求和失败类型。
- `usage.json`、`failure.json`、`network-diagnosis.json`：完整失败与 EPERM 证据。
- `verification/`：本次定向离线测试 XML 和源文件哈希。

后续需要恢复允许连接配置中模型/数据库服务的执行权限，然后明确失败后新增探针的重试额度。
下一次使用新版本输出目录，保留 v1；不要删除 started.json 来原地重跑。
当前仍不能宣称 low 已生效/提速、真实报告可交付、Milvus 或浏览器流程通过。

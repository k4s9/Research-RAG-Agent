# 自动比较与修订验收结果（2026-09-24）

**本批未通过自动闭环验收。** 比较在224.84秒后因工具额度耗尽失败，没有保存报告；
修订按预定停止条件未启动。没有手工恢复产物，没有挪用剩余修订额度再次比较。

## 实际执行与用量

用户回复“开始执行”批准 `report-closure-budget-20260924.md`。运行前131个冻结文件哈希
全部一致，基于 `b121a9a` 和执行前已验证的验收脚本；模型、预算及提示均未临时更改。
最初一次命令的计划哈希误写，保护检查在创建 started.json 和发送任何请求前拒绝启动；
修正命令后才执行本批，未清空任务状态或预算。

| 项目 | 实际结果 |
|---|---|
| 摄入 | 六份合成Markdown，6/6 ready |
| 比较 | 1条已启动，`budget_exceeded / tool_calls`，224.835秒，0份报告 |
| 修订 | 未启动；不算作一次模型失败或成功 |
| 生成 | 6次请求，6次HTTP响应，全部有usage |
| token | 输入38,365，输出6,560，总计44,925；unknown usage为0 |
| 工具 | 8次已执行，第9个待执行工具被额度拦截 |
| 辅助推理 | Embedding 7次，Reranker 1次 |
| HTTP生成延迟 | nearest-rank P50/P95：43.230 / 68.048秒，仅6个响应 |
| 完整任务延迟 | 只有1个失败任务，P50/P95均为224.835秒，不是成功延迟或稳定性估计 |
| 费用 | unknown |
| 报告重开、报告出处验收 | 未进入：没有已保存报告 |

本批总上限9次生成，剩余3次原属修订，不授权追加比较或新版本。旧的9任务失败、
7次真实生成、零新增推理恢复及各自账本保持原样，不合并改写为本次成功。

## 失败链与修复

1. 前两次生成主动调用 list_documents、search_knowledge，并读取Alpha和Beta两份原文。
2. 第3次生成输出正好2,048 token、finish_reason为tool_calls；提交缺少claims、recommendation、
   incomparable、unresolved，被严格拒绝。保留完整响应；不能仅据finish_reason认定完整，
   也不能无服务端证据断言截断原因。
3. 第4次提交给一个source_id配了多条quotes，违反当前一一对应契约，被拒绝。
4. 第5次提交已合法。此时只剩1次模型和1次工具调用，但执行器只检查剩余额度大于0，
   仍要求补查；合法报告因此未落盘。
5. 第6次生成请求读取两个section，第一个耗尽第8次工具额度，第二个被拦截。
   原run结束为budget_exceeded，report_id为null。

修复只调整现有执行器的额度判断：补查需要至少2次模型调用（请求取证、依据观察重新提交）
和2次工具调用（读取、submit_report）。额度不足时，已经通过原有严格校验的报告直接以
insufficient_evidence结束并保存未知项。不放宽报告字段或出处校验，不补写模型字段，
不改变历史run或冻结源码。本次第5次响应中的合法内容仍只是原始轨迹中的提交，未恢复入库。

新增回归在修复前实际得到4失败、4通过；包括仅剩1次模型调用、仅剩1次工具调用及两者均仅剩1次。
同时保留额度足够时仍执行一次补查的正例。测试替身意外多调用时改为明确报错，避免
StopIteration传入asyncio后等待；第一次复现的中断日志也保留。

修复后完整离线回归：**137通过、6跳过，15.603秒**。122个Python文件哈希与测试快照一致，
`git diff --check`通过。这是程序回归，**最新修复未再次发送真实模型请求验收**。

## 失败持久化核对

生成进程退出后，以独立进程、PostgreSQL只读事务重新读取：失败状态、error、完整state/
checkpoint和token预算均与原run.json一致；数据库中该run报告数为0、消息数为1。
生产进程PID 1374883，读取进程PID 1376523；读取新增推理0。

这验证失败记录保留，不代表报告重开成功，也不是PostgreSQL迁移或Milvus持久检索验收。
未新增浏览器验收，未修改或重启外部模型容器，未开始后续10场景或B0/B1/B2对照。

## 本机证据与后续

目录 `data/evaluations/report-closure-20260924-v1/`：

- `authorization.json`、`frozen.json`、`started.json`：批准范围、实际快照及一次性执行记录。
- `http-generation-01.json`至`06.json`、`usage.json`：全部原始响应和用量。
- `comparison/{request,submitted,run,timing}.json`、`failure.json`、`execution.log`：完整失败轨迹。
- `summary.json`：真实结果和离线修复结果分开记账。
- `verification/failed-run-reopened.json`：新进程只读核对；`read-only-http/usage.json`记录0推理。
- `verification/failed-run-hashes.json`：失败证据哈希，修复后再次确认未变。
- `verification/boundary-{before,after}-fix-{results.xml,source_hashes.json}`：修复前后的程序证据。

下一次需基于当前修复另建冻结版本、明确新额度，再完整验证比较→修订→保存→重开→出处核对。
不能原地重跑v1。闭环通过后再推进取消/恢复、持久化及小规模真实材料对照；独立人工rubric
分数仍为null，不把本次合成回归称作泛化效果。

# 最小真实生成与报告交付：新批次预算

本批次独立于已用完的 v1 九次任务。先离线 prepare 冻结代码/语料哈希，再执行 run。
在本次对话展示此预算后，用户回复“请继续”，本批按此上限执行；旧批次剩余请求额度
不转入。所有已有未提交修改保留。实际结果见 `minimal-live-results-20260922.md`。

更新：首次连接被 EPERM 阻止后，用户明确批准重试，生成新版本 minimal-live-20260922-v2。
其后单独批准一次纠错修订（最多额外 3 请求、117,332 token），整批仍在 7 请求 /
132,096 token 内。7 次真实生成额度现已全部使用，实际 43,509 token。
最后从已有合法响应恢复保存报告新增推理为 0；旧沙箱失败仍另保留 2,335 unknown 预留。
下面的表格为初始方案；最新执行差异、失败与恢复结果以结果文档为准。

| 阶段 | 次数与限额 | 通过条件 |
|---|---|---|
| 最小生成 | 1 请求；输入保守估算 ≤2,048，输出 ≤2,048，总计 ≤4,096 token；180 秒 | 真实 Qwen 返回完整正确答案，finish_reason=stop；失败即不启动比较 |
| B2 比较 | 1 任务；≤6 模型请求、8 工具、600 活跃秒、65,536 单次上下文估算、2,048 输出、128,000 累计 token | 完整报告覆盖 Alpha/Beta 三个维度并已写入 PostgreSQL |
| 重开与来源核对 | 0 生成请求；新进程启动 API 读取报告 | JSON/Markdown 完全一致；原文、chunk、文档及正文哈希匹配；随后逐项语义复核 |

整批最多 **7 个生成请求 / 132,096 个保守计量 token**，并发 1、无自动重试。
模型 Qwen3.8-27B，temperature=0，reasoning_effort=low，单请求超时 180 秒，Top-K=3。
费用单价未知，记 unknown；这里是资源上限，不是人民币价格承诺。
辅助请求：Embedding 最多 14（6 份摄入 + 最多 8 次检索），Reranker 最多 8。
上传摘要增强关闭；全部请求原始响应、finish_reason、usage 和失败均保留，不记录凭据。

使用现有六份合成 Markdown，全部在同一候选库；选定 Alpha/Beta 比较方法、实验设置、
局限性与 8 GB 可行性，保留不同比较条件和能耗缺测。此次不构成 B0/B1/B2 对照评测。
真实 PostgreSQL 使用保留的独立随机 schema，以便后续重开；create_all 不算迁移验证。
向量为内存，重开报告不算 Milvus 持久检索或浏览器验收。外部模型容器不在修改范围内。

已核对旧证据：最新 XML 为 116 passed / 6 skipped；118 个源码/测试哈希全部匹配工作区。
这是离线证据，不是新配置真实生成成功。2026-09-22 再次只读检查 Reranker：仍自
2026-09-20 07:59:08 UTC 运行，未配置 --chat-template，max-model-len=1024；保留此质量限制。

```bash
~/miniconda3/envs/research_rag/bin/python scripts/run_minimal_live.py prepare --output data/evaluations/minimal-live-20260922-v1
# 历史执行命令：v1 已尝试且失败，不能原地重跑或覆盖证据。
~/miniconda3/envs/research_rag/bin/python scripts/run_minimal_live.py run --output data/evaluations/minimal-live-20260922-v1
# 只读重开已有报告，不追加推理：
~/miniconda3/envs/research_rag/bin/python scripts/run_minimal_live.py reopen --output data/evaluations/minimal-live-20260922-v1
```

上述 reopen 只适用于已生成报告的批次；v1 在连接阶段被 EPERM 阻止，没有报告可重开。
恢复执行权限后，应明确失败后探针重试额度并建立新版本；不删除 started.json 绕过一次性保护。

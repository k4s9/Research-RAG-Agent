# 离线检索评测

`PYTHONPATH=. python scripts/evaluate_retrieval.py --dataset eval/datasets/sample.jsonl`
会运行本地 BM25 检索并输出 Recall@1/3/5、平均延迟和 P95 延迟。每行
JSONL 至少包含 `query`、`gold_ids` 和 `documents`；`documents` 是带 `id`
与 `content` 的数组。评测会使用本地 Dense、BM25、RRF 和 Reranker 全链路。
这个入口用于冻结语料后的快速回归，不替代真实
Milvus/模型服务的集成评测。

科研任务评测使用 `scripts/evaluate_tasks.py` 的 `freeze` / `run` / `summarize`。
冻结前必须明确模型、重复次数、完整语料、rubric、基线及调用/token 上限；独立人工判分
缺失时不记任务质量成功。`scripts/run_research_smoke.py` 可在随机 PostgreSQL schema
上传六份合成材料，冻结三条开发题并运行 B0/B1/B2，每题各一次；它会调用真实服务，
默认最多 54 次生成请求 / 1,152,000 token，向量索引仍为内存模式。
不能把这三题称为 20/60 正式数据集。运行结束保留全部失败和报告，删除本次临时 schema。

2026-09-22 首轮结果为 9/9 任务失败（5 次请求超时、4 次上下文预算耗尽）。
修复及后续门槛见 `docs/evaluation-continuation-20260922.md`；不要将修复后的离线通过
误写为真实任务已经成功。

最新小批次已有真实比较报告和显式恢复的修订报告；结果与限制见
`docs/minimal-live-results-20260922.md`。继续验收请读
`docs/handoff-next-session-20260923.md`。真实请求和本机连接配置留在 Git 忽略的
`data/` 与 `.env` 中；历史额度已用完，运行入口前须明确新批次预算。

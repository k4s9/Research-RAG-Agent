# 离线检索评测

`PYTHONPATH=. python scripts/evaluate_retrieval.py --dataset eval/datasets/sample.jsonl`
会运行本地 BM25 检索并输出 Recall@1/3/5、平均延迟和 P95 延迟。每行
JSONL 至少包含 `query`、`gold_ids` 和 `documents`；`documents` 是带 `id`
与 `content` 的数组。评测会使用本地 Dense、BM25、RRF 和 Reranker 全链路。
这个入口用于冻结语料后的快速回归，不替代真实
Milvus/模型服务的集成评测。

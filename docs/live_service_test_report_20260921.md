# 共享服务接入检查与测试记录（2026-09-21）

本次检查依据 `docs/SHARED_SERVICES_GUIDE.md` 和当前工作区实现（包含尚未提交的修改）。
**真实服务验证未完成**：沙箱禁止网络；沙箱外网络探测、Docker 只读检查和随后对具体
只读检查脚本的执行申请，均被自动审批拒绝。审批端返回 `503 Service Unavailable`，
提示审批模型无可用渠道。请求未实际到达内网服务，不能据此判定服务宕机或 Milvus 未部署。
指南中的 2026-09-20 基线也不作为本次实测结果。

## 已完成的验证

| 检查 | 结果 | 范围 |
|---|---|---|
| 当前 `.env` 导入应用配置 | 失败 | `ValidationError`，7 个 `extra_forbidden` 字段；未输出字段值 |
| 当前源码的离线回归 | 79 passed，3 skipped，13.12 秒 | SQLite、内存向量和本地/替身模型；不是共享服务测试 |
| 共享配置适配检查 | 失败 | 数据库/LLM 变量未映射；Embedding 路径、维度不匹配 |
| 指南 Reranker 响应结构复现 | 分数丢失 | `relevance_score=[0.93,0.12]` 被读取为 `[0.0,0.0]` |
| Milvus 已有集合维度检查复现 | 缺少校验 | 模拟 1024 维集合、配置 2560 维，初始化仍通过 |
| 新检查脚本 | 语法、Ruff、离线协议自检通过 | 真实网络执行仍待批准 |

离线回归使用 `research_rag` conda 环境，在临时目录复制 `src`、`tests`、`migrations`、
`alembic.ini`、`pyproject.toml`，仅将副本的 `.env` 设置为 `.env.local.example`。
对 89 个 Python 文件做了 SHA-256 一致性核验，测试期间没有更改应用源码或原始 `.env`。
该隔离措施是为了绕过当前共享 `.env` 的配置加载错误，不能掩盖原始配置无法启动的问题。

本次离线结果与源码哈希存于：

- `/tmp/research-rag-service-check-20260921-09tcqxa5/offline-results.xml`
- `/tmp/research-rag-service-check-20260921-09tcqxa5/source_hashes.json`

3 项跳过分别是：真实模型协议测试、真实 PostgreSQL 注册表测试、完整真实服务 E2E。

## 已复现的问题

1. **共享 `.env` 无法直接启动应用。** `src/config/settings.py` 的 `BaseSettings` 不接受
   未声明的 dotenv 字段；共享文件包含 `DATABASE_URL`、`LLM_MODEL`、PostgreSQL 拆分配置
   等变量。即使过滤掉未知字段，应用也仍连接默认 `localhost` 数据库、使用默认
   `deepseek-chat`，因为实际字段叫 `postgres_url` 和 `llm_model_name`。
   修复需要同时处理额外变量、变量别名，以及 PostgreSQL DSN 到 asyncpg 驱动的转换。
   仅设置 `extra="ignore"` 不足以接入真实服务。
2. **Embedding 请求 URL 重复 `/v1`。** 当前 `.env` 的 base URL 已以 `/v1` 结尾，
   默认 endpoint 为 `/v1/embeddings`，拼接得到 `/v1/v1/embeddings`。
   应配置 `EMBEDDING_ENDPOINT=/embeddings`，或统一规范化 URL。
3. **Embedding 维度配置不匹配。** 当前默认 1024；指南记录共享模型返回 2560。
   客户端严格校验维度，正常返回的 2560 维向量也会被拒绝。本次尚不能重新确认远端维度。
4. **Reranker 分数没有归一化。** `Qwen3Reranker` 原样返回响应，
   `HybridSearch._rerank` 只取 `item.get("score", 0.0)`。
   因而指南定义的 `relevance_score` 被变成 0，且没有标记降级。服务返回的顺序仍会保留，
   不能把这个问题描述为排序一定失效。现有真实协议测试仅断言索引范围，抓不到分数丢失。
5. **Milvus 已有集合没有维度校验。** 初始化只检查字段名，不检查 `dense_vector` 维度。
   使用共享 Embedding 前，需要按实测维度创建独立测试集合，并在复用已有集合时校验维度。
   不应通过删除已有集合来解决维度不匹配。

其他接入注意事项：当前 Milvus 集合名固定为 `knowledge_chunks`，没有独立配置的
collection/token 字段；共享 Milvus 接入与隔离测试需要完善这部分。Reranker 的 1024 token
输入限制也尚未被真实长文档验证。指南建议 `temperature=0`，应用现有 LLM 默认温度为
0.7/0.2，复测时应明确区分调用参数。

## 是否还需要真实服务

| 服务 | 当前实现是否需要 | 本次结论 |
|---|---|---|
| LLM、Embedding、Reranker | 是 | 指南已提供地址，当前会话尚未成功发出请求 |
| PostgreSQL | 是 | 指南已提供地址；应用需要变量/驱动适配及独立 schema 测试 |
| Milvus | 完整持久化链路需要 | 指南及当前 `.env` 未提供共享地址；代码默认 `localhost:19530`，本地运行状态未知 |
| MinIO / S3 兼容存储 | 仓库中的 Milvus 部署需要 | 已在 `docker-compose.yml` 中定义；复用已有 Milvus 时通常由其运维方提供 |
| etcd | Milvus 的依赖 | 当前 Compose 使用内嵌 etcd，无需额外提供独立 etcd 服务 |
| Redis、Elasticsearch、独立 BM25 服务 | 当前实现不需要 | 没有对应运行依赖；BM25 当前在应用进程内计算 |

当前 `get_vector_store()` 只支持 `memory`、`milvus`。内存实现重启丢失索引；PostgreSQL
目前只承载关系数据，没有 pgvector 适配器，因此已有 PostgreSQL 不能直接替代 Milvus。
如果决定采用 pgvector，需要另做存储适配和索引方案；不能只安装扩展或换配置。

当前代码已经调用 Dense → BM25 → RRF → Reranker，README 和模型配置文档中
“仍未接入重排序”的描述落后于工作区实现。

## 已准备的复测入口

新增 `scripts/check_shared_services.py`，补齐指南原来引用但仓库缺失的脚本：

```bash
conda run --no-capture-output -n research_rag \
  python scripts/check_shared_services.py --env-file .env --skip-concurrency --timeout 10
```

去掉 `--skip-concurrency` 后，对模型 `/models` 和 PostgreSQL 进行 1/2/4/8 并发基线。
模型功能请求使用合成文本；PostgreSQL 强制只读事务，仅执行 `SHOW server_version` 和
`SELECT 1`。脚本禁用代理和 HTTP 重定向，只输出状态、耗时、模型、字段、维度及 hash，
不输出密钥、完整 DSN、异常正文或模型输出正文。失败返回非零退出码。
该脚本检查共享服务本身，不检查 Milvus，也不等同于应用 E2E。

网络执行恢复后，仍需完成：

1. 运行服务探测，确认当前模型、向量维度、Reranker 协议与连通性。
2. 在测试进程中适配配置，执行 `tests/integration/test_live_model_contracts.py`。
3. 通过仅在内存/环境中设置的 `TEST_POSTGRES_URL`，执行
   `tests/integration/test_postgres_document_registry.py`；该测试创建随机 schema 并回滚。
4. 提供或启动 Milvus，使用隔离集合及 PostgreSQL schema 验证 PDF/Markdown 上传、
   索引、项目隔离、混合检索、引用、摘要、多轮对话与 run/step 落库。
   现有 `tests/e2e/test_live_stack.py` 会创建项目、上传文档且没有清理逻辑，
   不应直接指向共享业务数据空间。

应用代码中的上述问题本次仅复现和记录，尚未修复。此次新增内容是服务检查脚本和本报告；
并将指南中的运行环境改为 `research_rag`，因为原示例 `migration_memory_system` 环境
缺少脚本所需的 requests、python-dotenv、asyncpg。

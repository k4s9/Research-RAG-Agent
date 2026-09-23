# 共享服务实测与依赖结论（2026-09-21）

**共享 LLM、Embedding、Reranker、PostgreSQL 均可用，但当前应用还不能完整运行：
共享配置不兼容、真实 PostgreSQL 上传失败、重排序分数丢失，摘要输出也不稳定。
完整持久化链路还需可用的 Milvus 地址；默认 `localhost:19530` 已实测拒绝连接。**

本次依据 `docs/SHARED_SERVICES_GUIDE.md`，测试当前工作区实现，包含用户尚未提交的代码。
最初自动审批服务返回 503；用户手动批准后已恢复执行。以下真实结果均来自**沙箱外**请求，
并清除了大小写 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`。没有将历史基线或沙箱拒绝访问
当作本次服务结果。业务源码未修改。

## 服务实测

| 服务 | 结果 | 最小功能请求耗时 |
|---|---|---|
| LLM | HTTP 200，`Qwen3.8-27B`，非空回答 | 约 0.93 秒 |
| Embedding | HTTP 200，`Qwen3-Embedding-4B`，两条 **2560 维**向量 | 约 0.04–0.10 秒 |
| Reranker | HTTP 200，`Qwen/Qwen3-Reranker-4B`，返回 `relevance_score` | 约 0.05 秒 |
| PostgreSQL | 强制只读连接和查询通过，版本 **16.14** | 约 0.10 秒 |
| Milvus | 默认 `127.0.0.1:19530` 返回 errno 111，connection refused | 未监听 |

Docker 只读检查仅发现一个运行中的 Reranker 容器，没有运行中的 Milvus 容器。
指南和当前 `.env` 也没有提供其他共享 Milvus 地址；没有扫描其他主机。

三个模型服务的 `/models` 和 PostgreSQL 只读连接均通过 1/2/4/8 并发：
每个服务共 15/15 成功。模型元数据请求范围约 2.4–12.1 ms；PostgreSQL 在 8 并发时
约 396–403 ms。这是轻量连通性基线，**不是并发推理吞吐或生产容量测试**。

## 应用测试结果

最终完整测试入口得到 **2 passed、3 failed，55.60 秒**；此前定向下游测试曾通过，
完整复测暴露了真实模型输出波动，不能只保留成功的一次。
随后加强诊断和引用提示的定向测试运行 61.57 秒，三轮对话、引用和持久化均通过，
仅因摘要成功率为 3/4 而失败。

| 测试 | 结果 | 边界 |
|---|---|---|
| 原有离线套件 | 79 passed，3 skipped，13.12 秒 | SQLite、内存向量、本地/替身模型 |
| 真实模型客户端协议 | 通过 | 实际调用应用 Embedding/Reranker 客户端 |
| 真实 PostgreSQL 文档注册表 | 通过 | JSON 字段、跨项目去重、项目范围；随机 schema 回滚 |
| 真实上传工作流 | **失败** | 第一份 PDF 上传返回 500，数据库时间类型错误 |
| 真实 Reranker 分数一致性 | **失败** | 服务分数约 `[0.9796, 0.2248]`，应用输出 `[0, 0]` |
| 直接准备数据后的下游工作流 | **有波动** | 首次通过；完整复测时摘要仅 2/4 成功，第二轮引用为空 |

下游工作流直接向随机 PostgreSQL schema 准备两份 PDF 文本、两份 Markdown 文本，
避免上传故障遮住其他功能。它不代表上传、解析或 Milvus 已通过。已观察到：

- 首次定向测试 4/4 文档成功生成真实摘要和标签；完整复测只有 2/4，进一步诊断为 3/4。
- 诊断中的失败摘要返回 HTTP 200、`finish_reason=stop`、218 个生成 token，
  但应用无法解析成有效摘要 JSON。不是服务超时或达到输出长度上限。
- 真实 Embedding + 内存 Dense/BM25 + RRF + 真实 Reranker 返回正确首条文档及页码；
  该链路仍有分数为 0 的独立缺陷。
- 其他项目搜索返回空列表；人工修改 tags/doc_type 后，检索过滤立即生效。
- 首次运行三轮对话均完成，前两轮正确回答 `ALPHA-731` 并提供有效引用，第三轮列出全部文件。
  每次请求新建 Agent 实例，仍从 PostgreSQL 读取历史；保存 6 条消息、3 个 run。
- 首次运行三个 run 分别保存 4/2/2 个 step，实际调用了 `search_knowledge`、
  `get_document_section`、`list_documents`。完整复测第二轮缺引用后触发断言停止，
  因此不能宣称所有复测都完成了三轮。
- 针对第二轮原先未明确再次要求引用的测试提示，已加强为显式检索并提供 `[S1]`；
  定向复测前两轮均得到有效引用，三轮会话、6 条消息、3 个 run 及 4/2/2 个 step 均通过。
  质量问题汇总到测试末尾后，本轮唯一失败为摘要只生成 3/4。
- 测试结束删除本次随机 schema，并查询 `pg_namespace` 断言清理完成，包括失败用例。

## 已定位问题

1. **P1：真实 PostgreSQL 上传失败。** `src/core/ingest/pipeline.py` 的
   `_create_document`、`_set_status`、`_persist_chunks` 使用带 UTC 时区的 datetime，
   而 `Document` / `Chunk` 对应列是 `TIMESTAMP WITHOUT TIME ZONE`。
   asyncpg 拒绝插入，报 `can't subtract offset-naive and offset-aware datetimes`。
   应统一数据库时间约定；现有 models 的 `utc_now()` 返回无时区 UTC，可作为当前 schema
   下的一致入口。SQLite 离线测试未暴露该问题。
2. **P1：共享 `.env` 无法直接加载。** `src/config/settings.py` 拒绝 7 个未声明字段，
   包括 `DATABASE_URL`、`LLM_MODEL` 和 PostgreSQL 拆分变量。过滤额外字段也不够：
   应用实际读取 `POSTGRES_URL`、`LLM_MODEL_NAME`，否则仍使用 localhost 和 deepseek-chat。
   需要处理变量别名和 asyncpg DSN 格式。
3. **P1：Embedding URL/维度不匹配。** 当前 base 已含 `/v1`，默认 endpoint 又含 `/v1`，
   拼成 `/v1/v1/embeddings`；默认维度 1024，与本次实测 2560 不符。
4. **P2：真实 Reranker 分数丢失。** `Qwen3Reranker` 原样返回 `relevance_score`，
   `HybridSearch._rerank` 只读取 `score`，于是全部变成 0 且没有降级标记。
   远端顺序仍被保留，不能将此描述为排序一定失效。原有真实协议测试只验证索引范围，
   新增分数一致性测试能捕获此问题。
5. **P2：摘要输出需要提高可靠性。** 同一短文档场景出现不能被当前解析器接受的响应。
   当前仅提示“输出 JSON”，没有设置结构化输出约束，解析失败后直接放弃摘要。
   文档仍保持 ready 符合现有降级设计，但摘要成功率不能由服务返回 200 推断。
   可考虑服务支持的 JSON schema/JSON mode、temperature=0、解析/纠错策略；本次未修改。
6. **P2：Milvus 已有集合缺少维度校验。** 初始化仅检查字段名；离线复现中，已有
   1024 维集合在应用配置 2560 维时仍通过检查。真实 Milvus 尚未接入，不能声称已验证其
   schema、索引、过滤、写入可见性或重启持久性。

此外，Milvus 集合名固定为 `knowledge_chunks`，没有独立 collection/token 配置字段。
共享服务接入前需要明确认证方式及测试隔离方案。Reranker 1024 token 限制对真实长文档的
影响尚未验证。应用测试保持现有 LLM 温度（0.7/0.2）；最小服务探测使用 temperature=0。

## 还需要哪些真实服务

| 服务 | 是否还需补充 | 原因 |
|---|---|---|
| Milvus | **需要提供可用实例/地址** | 当前唯一持久化向量后端；默认地址拒绝连接 |
| MinIO / S3 兼容存储 | 自建仓库 Compose 中的 Milvus 时需要 | 其对象存储依赖；复用共享 Milvus 时通常由运维方提供 |
| 独立 etcd | 当前 Compose 不需要额外提供 | 已配置内嵌 etcd；其他部署形式按其配置决定 |
| LLM、Embedding、Reranker、PostgreSQL | 不需要另起一套 | 现有共享实例均已通过连通性/功能验证 |
| Redis、Elasticsearch、独立 BM25 服务 | 当前实现不需要 | 没有对应运行依赖，BM25 在应用进程中计算 |

当前向量接口只有 `memory`、`milvus`；内存模式重启丢失索引。已有 PostgreSQL 没有
pgvector 适配器，不能直接代替 Milvus。若选择 pgvector，是另一项存储适配工作。
本次没有启动新基础设施或修改现有容器。

建议先修复配置、时间类型和分数映射，提高摘要输出的可靠性，再接入 Milvus
进行完整端到端与重启验证。使用共享 2560 维模型时，应创建匹配维度的独立测试集合，
不要删除已有业务集合。以上应用问题不能通过增加服务实例直接解决。

## 可复现入口与证据

服务探测（仅合成推理请求和 PostgreSQL 只读查询）：

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    -u http_proxy -u https_proxy -u all_proxy \
    conda run --no-capture-output -n research_rag \
    python scripts/check_shared_services.py --env-file .env
```

应用集成测试（在 Codex 中应批准沙箱外执行）：

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    -u http_proxy -u https_proxy -u all_proxy \
    conda run --no-capture-output -n research_rag \
    python scripts/run_shared_service_tests.py --suite all
```

`--suite contracts` 仅运行原有两项集成测试；`--suite seeded` 仅验证直接准备数据后的
下游流程。`all` / `workflow` 保留已发现的失败，不将其标记成预期失败。

运行器复制当前源码到临时目录并记录 SHA-256，不复制共享 `.env`，不改应用源码。
凭据仅传入子进程环境；为测试明确映射 `DATABASE_URL` → asyncpg `POSTGRES_URL`、
`LLM_MODEL` → `LLM_MODEL_NAME`，设置正确 Embedding endpoint/2560 维和 remote provider。
**这些测试适配不表示原始应用配置已经修复。**

证据文件：

- `/tmp/research-rag-service-check-20260921-09tcqxa5/shared-services.jsonl`：功能/并发脱敏结果。
- 同目录 `offline-results.xml`、`live-integration-results.xml`、`shared-workflow-results.xml`、
  `shared-seeded-workflow-results.xml`：分阶段结果（包含首次通过的下游测试）。
- `/tmp/research-rag-live-tests-3orra409/`：完整测试入口的源码快照、`source_hashes.json`、
  脱敏 `pytest.log`、`results.xml`；2 passed，3 failed。
- `/tmp/research-rag-live-tests-gfqm_wd5/`：摘要结构和明确引用要求的定向诊断；
  1 failed，仅摘要成功率不满足 4/4，其他下游断言通过。

本次新增两个检查/测试脚本、一组可选真实集成测试和本报告，并更新服务指南。
业务源码中的问题仅定位和复现，尚未修复。

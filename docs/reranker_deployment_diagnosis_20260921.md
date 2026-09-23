# 本机 Qwen3 Reranker 部署诊断（2026-09-21）

**服务在本机正常运行，未发现 ARM64 / openEuler 导致的不可用问题；但部署漏配了
Qwen3 Reranker 的评分提示模板，已实测影响语义排序。原先分数归零另属客户端字段适配问题。**

本次仅检查部署并发送有限的诊断请求，没有修改 Compose、模型或业务代码，没有重启容器。
真实服务请求在沙箱外直连，禁用环境代理；沙箱内的连接失败不计入服务故障。

## 部署与可用性

| 检查项 | 本次结果 |
|---|---|
| 主机 | `test-host`，`aarch64`，openEuler 24.09 |
| 服务地址 | 项目配置的 `reranker.example.com` 是本机网卡地址；`remote` 表示通过 HTTP 调用 |
| 容器来源 | `~/qwen3-models/docker-compose.yml`，工作目录与模型挂载均匹配 |
| 镜像 | `quay.io/ascend/vllm-ascend:v0.23.0-openeuler`，实际架构 `arm64/linux` |
| 运行状态 | `running`，`RestartCount=0`，`OOMKilled=false`，本轮启动时间为 2026-09-20 07:59:08 UTC |
| NPU | Ascend 910B4，驱动工具版本 25.2.3，Health `OK`；引擎正在使用 NPU |
| 模型 | `Qwen/Qwen3-Reranker-4B`，`pooling`，`bfloat16`，分类头覆盖参数已生效 |
| 健康/模型接口 | 本机 `/health`、`/v1/models` 和配置地址 `/v1/models` 均为 HTTP 200 |
| 推理接口 | `/rerank`、`/v1/rerank` 均为 HTTP 200，英文两文档约 49–52 ms |
| 20 条短文档 | HTTP 200，约 66 ms；相关文档排第一 |
| 1/2/4 并发推理 | 7/7 请求成功；首次 2 并发约 2.67 秒，复测约 53–54 ms；4 并发约 61–63 ms |

首次并发延迟的具体原因未证实。这些短输入测试不代表生产容量或长期稳定性测试。
历史日志中的 `EngineDeadError` 处于上一次 SIGTERM 关闭过程中，随后服务成功启动，
不能将其直接解释为当前引擎故障。

## 已证实的部署问题：评分模板没有启用

实际容器命令包含 `--hf_overrides`，但没有 `--chat-template`。
该镜像自带的示例 `examples/pooling/score/qwen3_reranker_online.py` 明确同时配置这两项。
镜像内 `vllm/entrypoints/pooling/scoring/io_processor.py` 的 `get_score_prompt()`
也明确说明：评分接口仅在显式传入模板时应用模板，不自动采用 tokenizer 的聊天模板。
当前分支会直接拼接 query/document，因此磁盘上存在 `chat_template.jinja` 并不等于已生效。

没有重启服务，直接读取镜像自带官方模板，在诊断请求中将每个 query/document 渲染为完整
提示文本，并以空 query 和渲染后的 document 调用同一评分接口。该方法利用当前服务的
直接拼接行为，用于验证模型在完整提示下的输出；不是已完成部署修复的宣称。

| 用例 | 原始请求首条 | 请求中补齐官方模板后的首条 |
|---|---|---|
| 中国的首都是哪里？ | 法国首都是巴黎，错误 | 中国首都是北京，正确 |
| 法国的首都是哪里？ | 正确 | 正确 |
| What is the capital of China? | 正确，但水果文档也有 0.9722 高分 | 正确，水果文档约 0.0000195 |
| Explain gravity | 中国首都是北京，错误 | 重力定义，正确 |
| verified calibration | 正确 | 正确 |
| Python 如何将列表按降序排序？ | 正确 | 正确 |

这六个人工诊断样例首条命中从 **4/6 变为 6/6**，不是完整质量评测。
“中国首都”用例按固定文档顺序的分数如下：

| 文档 | 原始请求 | 补齐官方模板 |
|---|---:|---:|
| 苹果是一种水果。 | 0.876688 | 0.00003463 |
| 中国的首都是北京。 | 0.868876 | 0.998612 |
| 法国的首都是巴黎。 | 0.892952 | 0.00002401 |

建议在 Compose 的 `reranker.command` 中追加下列参数。该绝对路径已在当前镜像内核实：

```yaml
      - --chat-template
      - /vllm-workspace/vllm/examples/pooling/score/template/qwen3_reranker.jinja
```

应用配置需要重建该容器，并重新验证普通 query/document 请求的结果；本次没有执行。
如果后续将模板保存到模型挂载目录，应明确通过 `--chat-template` 指向该文件。

## 分数归零：客户端适配，与模板问题独立

检查开始时 `HybridSearch._rerank()` 只读取 `score`；服务实际返回 `relevance_score`，
因此该版本会写入 0。它按服务返回列表构造结果，远端顺序仍被保留，不能据此断言排序必然失效。

检查过程中工作区由其他修改更新为：

```python
float(item.get("score", item.get("relevance_score", 0.0)))
```

最新源码快照上的真实调用结果：服务和应用分数均为
`[0.9795737266540527, 0.22475947439670563]`，索引顺序一致，`degraded=[]`。
现有 `test_shared_reranker_score_contract` 单独执行为 **1 passed，1.41 秒**。
因此旧版本归零原因已明确，但最新工作区已不复现；这项兼容修改不是本诊断所作。

## 其他已验证的配置与协议差异

1. **客户端发送 `top_k`，此版本接口使用 `top_n`。** OpenAPI 和实际请求都证实：
   两文档发送 `top_k=1` 仍返回两条，`top_n=1` 只返回一条；20 条发送 `top_k=5`
   仍返回 20 条。应用最终 `[:top_k]` 仍会限制结果条数，但服务端参数未生效。
   应修正远端客户端的请求字段，并增加返回条数契约检查。
2. **当前容器没有应用文件中的 API Key 配置。** Compose 中有 `VLLM_API_KEY`，
   运行中容器却没有该环境变量，也没有 `--api-key` 参数。实测无 Authorization、
   错误 Bearer 和现有项目密钥均返回 HTTP 200。这是运行配置与文件不一致，
   不是项目密钥已经验证有效。应用配置后应重新验证鉴权。
3. **1024 token 上限已真实触发。** 超长输入返回 HTTP 400，明确提示最大 1024 token。
   上限涉及 query/document 及完整模板；当前客户端未发送截断参数。
   应根据实际分块的 tokenizer 长度、问题长度与模板开销决定分块/截断及服务上限。
   当前按词/空白分块，不能把 384 词当成 384 模型 token。
4. **共用 NPU 需要另行规划。** 实测 HBM 占用约 31901/32768 MiB，进程约 29078 MiB，
   这包含引擎预留，不能等同于模型权重或断言内存泄漏。当前仅 Reranker 容器运行；
   Compose 的两个服务默认使用同一设备且各配置 0.95 显存比例，因此若要同时启动
   Embedding，需要先调整设备分配和内存预算。本次未进行双模型共存测试。

## 证据与复现

诊断输出目录：`/tmp/reranker-diagnosis-20260921-tbvl79xm/`。

- `results.json`：部署、NPU、OpenAPI、基础推理、长度边界和应用分数映射。
- `template-results.json`：六组模板对照、实际鉴权配置与并发复测。
- `official-qwen3-reranker.jinja`：直接读取自运行镜像的官方模板。
- `scoring-io-processor-excerpt.py.txt`：运行镜像的评分预处理源码片段。
- `score-contract.xml`、`score-contract-source-hashes.json`：通过的单项测试和源码校验值。
- `score-contract-pytest.log`：保留首次隔离配置缺少 aiosqlite 的收集失败，以及改用已安装
  asyncpg 驱动占位配置后的成功结果；此测试未使用数据库 fixture 或连接数据库。

复现脚本分别为 `/tmp/reranker_diagnose_20260921.py`、
`/tmp/reranker_template_probe_20260921.py`、`/tmp/reranker_contract_pytest_20260921.py`。
密钥从现有 `.env` 读取，报告与输出未写入密钥。

# 科研 RAG Agent 系统

## 项目介绍

科研 RAG (Retrieval-Augmented Generation) Agent 系统是一个专为科研人员设计的智能知识管理和问答系统。它能够帮助科研人员上传、管理和检索科研文档，提取关键信息，并通过大语言模型提供智能问答服务。

## 功能特点

- **文档上传与解析**：支持 PDF、Markdown 等格式的文档上传和解析
- **智能切片**：自动将文档切分为合适大小的 chunks，便于检索
- **向量嵌入**：使用 Qwen3-Embedding 模型将文本转换为向量表示
- **检索**：当前生产路径为 Dense 检索，时间衰减为可选后处理；BM25 + RRF + Reranker 仍在 Phase 3 实现中
- **RAG 问答**：基于检索结果和大语言模型，提供智能问答服务
- **项目管理**：支持按项目组织和管理文档
- **Web 界面**：提供直观的 Gradio 界面，方便用户操作

## 技术栈

- **后端框架**：FastAPI
- **前端框架**：Gradio
- **向量数据库**：Milvus Standalone
- **关系数据库**：PostgreSQL
- **嵌入模型**：Qwen3-Embedding-0.6B
- **重排序模型**：Qwen3-Reranker-0.6B
- **大语言模型**：DeepSeek Chat API
- **文档解析**：PyMuPDF (fitz)

## 安装步骤

> 当前项目处于 experimental 状态。本地离线 E2E 已可运行，真实 PostgreSQL/Milvus/模型
> E2E 仍需按环境配置执行；BM25 + RRF + Reranker 生产链路尚未完成。

### 1. 克隆项目

```bash
git clone <repo-url>
cd research-rag-agent
```

### 2. 启动基础设施

使用 Docker Compose 启动 Milvus、PostgreSQL 等服务：

```bash
cp .env.example .env  # 编辑填入实际配置
docker compose up -d
```

### 3. 创建 Python 环境

```bash
conda create -n rag python=3.10 -y
conda activate rag
pip install -e ".[dev]"
```

### 4. 初始化数据库

```bash
python scripts/init_db.py      # 安全执行 Alembic 迁移，不会删除旧表
python scripts/init_milvus.py  # 创建 Milvus Collection
```

### 无 Docker 的 WSL 离线流程

```bash
cp .env.local.example .env
pip install -e ".[dev]"
python scripts/init_db.py
python -m pytest -m e2e -v
uvicorn src.main:app --host 127.0.0.1 --port 8002
```

Embedding/Reranker 远程 API 协议、环境变量和验证命令见
[`docs/model_api_configuration.md`](docs/model_api_configuration.md)。

### 5. 启动服务

#### 启动 API 服务

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8002
```

#### 启动 Gradio 前端

```bash
python frontend/app.py
```

## 使用方法

1. **访问 Gradio 界面**：在浏览器中打开 `http://localhost:7860`
2. **上传文档**：在 "文件上传" 标签页上传 PDF 或 Markdown 文件
3. **进行对话**：在 "对话" 标签页与系统进行交互，提问关于文档的问题
4. **搜索知识**：在 "搜索" 标签页搜索系统中的知识

## 项目结构

```
research-rag-agent/
├── src/                # 源代码
│   ├── api/            # API 接口
│   ├── core/           # 核心功能
│   │   ├── ingest/     # 文档摄入
│   │   ├── retrieval/  # 检索功能
│   │   └── agent/      # Agent 功能
│   ├── db/             # 数据库相关
│   ├── schemas/        # 数据模型
│   └── utils/          # 工具函数
├── frontend/           # 前端界面
├── scripts/            # 脚本文件
├── data/               # 数据存储
├── volumes/            # Docker 卷
├── docker-compose.yml  # Docker 配置
└── README.md           # 项目文档
```

## 配置说明

项目配置通过 `.env` 文件管理，主要配置项包括：

- **数据库配置**：PostgreSQL 和 Milvus 的连接信息
- **模型配置**：嵌入模型和重排序模型的路径
- **LLM 配置**：DeepSeek API 的密钥和模型名称
- **检索配置**：检索参数和时间衰减参数

## 注意事项

- 确保 Docker 服务正常运行
- 确保 `.env` 文件中的 API 密钥正确配置
- 首次使用时，系统需要下载模型，可能需要一些时间
- 对于大型文档，解析和向量化可能需要较长时间

## 许可证

MIT

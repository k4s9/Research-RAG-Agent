#!/bin/bash

# 启动脚本 - 一键初始化所有服务

echo "=== 启动科研 RAG Agent 系统 ==="

# 检查端口是否被占用
check_port() {
    local port=$1
    if lsof -i:$port > /dev/null 2>&1; then
        return 0  # 端口被占用
    else
        return 1  # 端口未被占用
    fi
}

# 1. 检查 Docker 服务是否运行
echo "检查 Docker 服务..."
if ! docker info > /dev/null 2>&1; then
    echo "错误: Docker 服务未运行，请先启动 Docker"
    exit 1
fi

# 2. 启动必要的 Docker 容器
echo "启动 Docker 容器..."
docker-compose up -d

# 3. 等待容器启动
echo "等待容器启动..."
sleep 5

# 4. 启动后端 API 服务
echo "检查后端 API 服务端口 8002..."
if check_port 8002; then
    echo "后端 API 服务端口 8002 已被占用，跳过启动"
else
    echo "启动后端 API 服务..."
    nohup /home/guozy/miniconda3/envs/research_rag/bin/python -m uvicorn src.main:app --host 0.0.0.0 --port 8002 > backend.log 2>&1 &
    echo "后端 API 服务已启动，运行在 http://0.0.0.0:8002"
    # 等待后端服务启动
    echo "等待后端服务启动..."
    sleep 3
fi

# 5. 启动前端 Gradio 服务
echo "检查前端 Gradio 服务端口 7860..."
if check_port 7860; then
    echo "前端 Gradio 服务端口 7860 已被占用，跳过启动"
else
    echo "启动前端 Gradio 服务..."
    nohup python frontend/app.py > frontend.log 2>&1 &
    echo "前端 Gradio 服务已启动，运行在 http://0.0.0.0:7860"
fi

echo "=== 系统启动完成 ==="
echo "您可以通过以下地址访问系统："
echo "- 后端 API 服务：http://localhost:8002"
echo "- 前端 Gradio 界面：http://localhost:7860"

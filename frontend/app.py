import gradio as gr
import requests
import json
import mimetypes
import uuid
from src.config.settings import settings

# API 基础 URL
API_BASE_URL = settings.api_client_url.rstrip("/")

# 生成会话 ID
session_id = str(uuid.uuid4())

def upload_file(file, project_ids, description):
    """上传文件"""
    if not file:
        return "请选择文件"
    
    # 构建项目 ID 列表
    project_ids_list = [pid.strip() for pid in project_ids.split(',') if pid.strip()]
    
    try:
        # 提取文件名
        import os
        filename = os.path.basename(file)
        # 构建文件对象
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        files = {"file": (filename, open(file, "rb"), mime_type)}
        data = {
            "project_ids": json.dumps(project_ids_list),
            "description": description
        }
        
        response = requests.post(
            f"{API_BASE_URL}/api/v1/documents/upload",
            files=files,
            data=data
        )
        response.raise_for_status()
        result = response.json()
        return f"上传成功！文档ID: {result.get('document_id')}\n状态: {result.get('status')}\n{result.get('message')}"
    except Exception as e:
        return f"上传失败: {str(e)}"

def send_message(message, project_ids, include_outdated, history):
    """发送对话消息"""
    if not message:
        return "", history
    
    # 构建项目 ID 列表
    project_ids_list = [pid.strip() for pid in project_ids.split(',') if pid.strip()]
    
    data = {
        "session_id": session_id,
        "project_ids": project_ids_list,
        "message": message,
        "include_outdated": include_outdated
    }
    
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/v1/chat/message",
            json=data
        )
        response.raise_for_status()
        result = response.json()
        response_text = result.get('response', '无响应')
        # Gradio Chatbot 需要 {'role': 'user/assistant', 'content': '...'} 格式
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": response_text})
        return "", history
    except Exception as e:
        error_message = f"发送失败: {str(e)}"
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": error_message})
        return "", history

def search(query, project_ids, cross_project, include_outdated, content_types, top_k, time_decay_enabled):
    """执行搜索"""
    if not query:
        return "请输入查询内容"
    
    # 构建项目 ID 列表
    project_ids_list = [pid.strip() for pid in project_ids.split(',') if pid.strip()]
    # 构建内容类型列表
    content_types_list = [ct.strip() for ct in content_types.split(',') if ct.strip()]
    
    data = {
        "query": query,
        "project_ids": project_ids_list,
        "cross_project": cross_project,
        "include_outdated": include_outdated,
        "content_types": content_types_list if content_types_list else ["text", "decision", "milestone"],
        "top_k": top_k,
        "time_decay_enabled": time_decay_enabled
    }
    
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/v1/search",
            json=data
        )
        response.raise_for_status()
        result = response.json()
        
        # 格式化搜索结果
        results = result.get('results', [])
        formatted_results = []
        for item in results:
            formatted = f"ID: {item.get('id')}\n"
            formatted += f"类型: {item.get('entity_type')}\n"
            formatted += f"内容类型: {item.get('content_type')}\n"
            formatted += f"状态: {item.get('version_status')}\n"
            formatted += f"得分: {item.get('score', 0):.2f}\n"
            formatted += f"项目: {', '.join(item.get('project_ids', []))}\n"
            formatted += "-" * 50
            formatted_results.append(formatted)
        
        return "\n\n".join(formatted_results) if formatted_results else "无结果"
    except Exception as e:
        return f"搜索失败: {str(e)}"

# 创建 Gradio 界面
with gr.Blocks(title="科研 RAG Agent") as demo:
    gr.Markdown("# 科研 RAG Agent 系统")
    
    with gr.Tab("文件上传"):
        file_input = gr.File(label="选择文件 (PDF/Markdown)")
        project_ids = gr.Textbox(label="项目 ID (逗号分隔)", placeholder="例如: proj-1, proj-2")
        description = gr.Textbox(label="描述", placeholder="请输入文档描述")
        upload_button = gr.Button("上传")
        upload_output = gr.Textbox(label="上传结果", lines=3)
        upload_button.click(upload_file, inputs=[file_input, project_ids, description], outputs=upload_output)
    
    with gr.Tab("对话"):
        gr.Markdown(f"会话 ID: {session_id}")
        chat_project_ids = gr.Textbox(label="项目 ID (逗号分隔)", placeholder="例如: proj-1, proj-2")
        chatbot = gr.Chatbot(label="对话历史")
        message = gr.Textbox(label="消息", lines=3, placeholder="请输入您的问题...")
        include_outdated = gr.Checkbox(label="包含过时知识")
        send_button = gr.Button("发送")
        send_button.click(send_message, inputs=[message, chat_project_ids, include_outdated, chatbot], outputs=[message, chatbot])
    
    with gr.Tab("搜索"):
        search_query = gr.Textbox(label="查询内容", lines=2, placeholder="请输入搜索关键词...")
        search_project_ids = gr.Textbox(label="项目 ID (逗号分隔)", placeholder="例如: proj-1, proj-2")
        cross_project = gr.Checkbox(label="跨项目搜索")
        search_include_outdated = gr.Checkbox(label="包含过时知识")
        content_types = gr.Textbox(label="内容类型 (逗号分隔)", placeholder="例如: text,table,decision")
        top_k = gr.Slider(label="返回结果数", minimum=1, maximum=20, value=5, step=1)
        time_decay_enabled = gr.Checkbox(label="启用时间衰减", value=True)
        search_button = gr.Button("搜索")
        search_output = gr.Textbox(label="搜索结果", lines=10)
        search_button.click(search, inputs=[search_query, search_project_ids, cross_project, search_include_outdated, content_types, top_k, time_decay_enabled], outputs=search_output)

if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=settings.gradio_port,
        share=False
    )

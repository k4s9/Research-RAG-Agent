import gradio as gr
import requests
from src.config.settings import settings

# API 基础 URL
API_BASE_URL = f"http://{settings.api_host}:{settings.api_port}"

def upload_file(file, project_ids, description):
    """上传文件"""
    if not file:
        return "请选择文件"
    
    files = {"file": (file.name, open(file.name, "rb"), file.type)}
    data = {
        "project_ids": project_ids,
        "description": description
    }
    
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/v1/documents/upload",
            files=files,
            data=data
        )
        response.raise_for_status()
        result = response.json()
        return f"上传成功！文档ID: {result.get('document_id')}\n状态: {result.get('status')}"
    except Exception as e:
        return f"上传失败: {str(e)}"

def send_message(session_id, project_ids, message, include_outdated):
    """发送对话消息"""
    if not message:
        return "请输入消息"
    
    data = {
        "session_id": session_id,
        "project_ids": project_ids,
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
        return result.get('response', '无响应')
    except Exception as e:
        return f"发送失败: {str(e)}"

def search(query, project_ids, cross_project, include_outdated, content_types, top_k, time_decay_enabled):
    """执行搜索"""
    if not query:
        return "请输入查询内容"
    
    data = {
        "query": query,
        "project_ids": project_ids,
        "cross_project": cross_project,
        "include_outdated": include_outdated,
        "content_types": content_types,
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
            formatted += f"内容: {item.get('content', '')[:100]}...\n"
            formatted += f"状态: {item.get('version_status')}\n"
            formatted += f"得分: {item.get('score')}\n"
            formatted += f"创建时间: {item.get('created_at')}\n"
            formatted += "-" * 50
            formatted_results.append(formatted)
        
        return "\n\n".join(formatted_results) if formatted_results else "无结果"
    except Exception as e:
        return f"搜索失败: {str(e)}"

# 创建 Gradio 界面
with gr.Blocks(title="科研 RAG Agent") as demo:
    gr.Markdown("# 科研 RAG Agent 系统")
    
    with gr.Tab("文件上传"):
        file_input = gr.File(label="选择文件 (PDF/PPTX/Markdown)")
        project_ids = gr.Textbox(label="项目 ID (逗号分隔)")
        description = gr.Textbox(label="描述")
        upload_button = gr.Button("上传")
        upload_output = gr.Textbox(label="上传结果")
        upload_button.click(upload_file, inputs=[file_input, project_ids, description], outputs=upload_output)
    
    with gr.Tab("对话"):
        session_id = gr.Textbox(label="会话 ID")
        chat_project_ids = gr.Textbox(label="项目 ID (逗号分隔)")
        message = gr.Textbox(label="消息", lines=3)
        include_outdated = gr.Checkbox(label="包含过时知识")
        send_button = gr.Button("发送")
        chat_output = gr.Textbox(label="回复", lines=5)
        send_button.click(send_message, inputs=[session_id, chat_project_ids, message, include_outdated], outputs=chat_output)
    
    with gr.Tab("搜索"):
        search_query = gr.Textbox(label="查询内容", lines=2)
        search_project_ids = gr.Textbox(label="项目 ID (逗号分隔)")
        cross_project = gr.Checkbox(label="跨项目搜索")
        search_include_outdated = gr.Checkbox(label="包含过时知识")
        content_types = gr.Textbox(label="内容类型 (逗号分隔，如: text,table,decision)")
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

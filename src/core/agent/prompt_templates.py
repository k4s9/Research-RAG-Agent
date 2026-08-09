from typing import List, Dict, Any, Optional


def get_rag_prompt(
    query: str, search_results: List[Dict[str, Any]], history: Optional[List[Dict[str, str]]] = None
) -> str:
    """构建 RAG Prompt"""
    # 构建检索到的相关知识
    retrieved_chunks = []
    for i, result in enumerate(search_results):
        chunk = f"""[S{i + 1}]
内容: {result.get("content", "")}
来源: {result.get("filename", result.get("source", ""))} {result.get("locator", "")}
"""
        retrieved_chunks.append(chunk)
    retrieved_content = "\n".join(retrieved_chunks)

    # 构建最近对话历史
    recent_conversation = []
    if history:
        for msg in history[-5:]:  # 只取最近 5 轮对话
            role = "用户" if msg["role"] == "user" else "助手"
            recent_conversation.append(f"{role}: {msg['content']}")
    recent_content = "\n".join(recent_conversation)

    # 构建完整 Prompt
    prompt = f"""
你是一个科研助手，帮助用户管理项目知识和进行方案设计。

[检索到的相关知识]
{retrieved_content}

[最近对话历史]
{recent_content}

[用户消息]
{query}

请仅根据以上来源回答。每个事实性 claim 后紧跟一个或多个来源标识，如 [S1]。
证据不足时明确说明无法从资料中确认，不得编造来源标识或事实。
"""

    return prompt

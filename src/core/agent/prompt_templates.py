from typing import List, Dict, Any, Optional


def get_rag_prompt(query: str, search_results: List[Dict[str, Any]], history: Optional[List[Dict[str, str]]] = None) -> str:
    """构建 RAG Prompt"""
    # 构建检索到的相关知识
    retrieved_chunks = []
    for i, result in enumerate(search_results):
        chunk = f"""[知识 {i+1}]
内容: {result.get('content', '')}
来源: {result.get('source', '')}
相关度: {result.get('score', 0):.2f}
"""
        retrieved_chunks.append(chunk)
    retrieved_content = '\n'.join(retrieved_chunks)
    
    # 构建最近对话历史
    recent_conversation = []
    if history:
        for msg in history[-5:]:  # 只取最近 5 轮对话
            role = "用户" if msg["role"] == "user" else "助手"
            recent_conversation.append(f"{role}: {msg['content']}")
    recent_content = '\n'.join(recent_conversation)
    
    # 构建完整 Prompt
    prompt = f"""
你是一个科研助手，帮助用户管理项目知识和进行方案设计。

[检索到的相关知识]
{retrieved_content}

[最近对话历史]
{recent_content}

[用户消息]
{query}

请根据以上信息，为用户提供一个详细、准确的回答。回答应该：
1. 基于检索到的知识，不要编造信息
2. 语言自然、流畅，符合科研场景
3. 对于不确定的信息，明确表示
4. 提供具体的建议和解决方案
"""
    
    return prompt

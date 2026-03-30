from fastapi import APIRouter, HTTPException
from src.schemas.chat import ChatMessageRequest, ChatMessageResponse, ChatSessionResponse

router = APIRouter()

@router.post("/message", response_model=ChatMessageResponse)
async def send_message(request: ChatMessageRequest):
    """发送对话消息"""
    # 这里将实现对话消息发送逻辑
    # 暂时返回模拟响应
    return ChatMessageResponse(
        session_id=request.session_id,
        response="我已记录导师的意见。我注意到您之前在3月15日的讨论中提到过使用标准Multi-Head Attention的方案，该条记录已标记为过时。关于Flash Attention的集成，建议您可以参考...",
        extracted_memories=[
            {
                "memory_id": "mem-uuid-xxx",
                "type": "decision",
                "summary": "导师决定将注意力机制从标准实现改为 Flash Attention",
                "outdated_references": ["mem-uuid-old"]
            }
        ],
        retrieved_context=[
            {
                "chunk_id": "chunk-uuid-xxx",
                "content_preview": "Flash Attention 通过分块计算...",
                "source": "attention_survey.pdf",
                "relevance_score": 0.92
            }
        ]
    )

@router.get("/sessions/{session_id}", response_model=ChatSessionResponse)
async def get_chat_session(session_id: str):
    """获取对话历史"""
    # 这里将实现对话历史查询逻辑
    # 暂时返回模拟响应
    return ChatSessionResponse(
        session_id=session_id,
        messages=[
            {
                "role": "user",
                "content": "导师今天说我们的注意力机制方案需要改为用 Flash Attention，之前的标准实现效率太低了",
                "timestamp": "2026-03-30T10:00:00Z"
            },
            {
                "role": "assistant",
                "content": "我已记录导师的意见。我注意到您之前在3月15日的讨论中提到过使用标准Multi-Head Attention的方案，该条记录已标记为过时。关于Flash Attention的集成，建议您可以参考...",
                "timestamp": "2026-03-30T10:01:00Z"
            }
        ]
    )

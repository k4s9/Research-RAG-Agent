from fastapi import APIRouter, HTTPException
from src.schemas.chat import ChatMessageRequest, ChatMessageResponse, ChatSessionResponse
from src.core.agent.orchestrator import AgentOrchestrator
from loguru import logger

router = APIRouter()

@router.post("/message", response_model=ChatMessageResponse)
async def send_message(request: ChatMessageRequest):
    """发送对话消息"""
    try:
        # 初始化 Agent 编排器
        orchestrator = AgentOrchestrator()
        
        # 处理消息
        result = await orchestrator.handle_message(
            message=request.message,
            project_ids=request.project_ids,
            session_id=request.session_id,
            history=None  # 暂时不处理历史对话
        )
        
        # 构建响应
        response = ChatMessageResponse(
            session_id=result["session_id"],
            response=result["response"],
            extracted_memories=result["extracted_memories"],
            retrieved_context=result["retrieved_context"]
        )
        
        logger.info(f"聊天消息处理完成: session_id={request.session_id}")
        return response
        
    except Exception as e:
        logger.error(f"聊天消息处理失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"聊天消息处理失败: {str(e)}")


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

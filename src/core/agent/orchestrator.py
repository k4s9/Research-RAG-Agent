from typing import List, Dict, Any, Optional
from loguru import logger
from src.utils.llm_client import LLMClient
from src.core.retrieval.hybrid_search import HybridSearch
from src.core.agent.prompt_templates import get_rag_prompt

class AgentOrchestrator:
    def __init__(self):
        self.llm_client = LLMClient()
        self.searcher = HybridSearch()
    
    async def handle_message(self, message: str, project_ids: List[str], session_id: str, history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """处理用户消息并返回 Agent 响应"""
        try:
            # 1. 意图识别（简化版，直接视为问答/检索型）
            intent = "qa"
            
            # 2. 根据意图执行相应操作
            if intent == "qa":
                # 执行检索
                search_results = self.searcher.search(
                    query=message,
                    project_ids=project_ids,
                    top_k=5
                )
                
                # 构建 RAG Prompt
                prompt = get_rag_prompt(message, search_results, history)
                
                # 调用 LLM 生成回答
                response = self.llm_client.generate(
                    prompt=prompt,
                    history=history,
                    session_id=session_id,
                    project_ids=project_ids
                )
                
                # 构建返回结果
                result = {
                    "session_id": session_id,
                    "response": response,
                    "extracted_memories": [],
                    "retrieved_context": [
                        {
                            "chunk_id": result.get("id"),
                            "content_preview": "",
                            "source": "",
                            "relevance_score": result.get("score", 0)
                        }
                        for result in search_results[:3]
                    ]
                }
                
                logger.info(f"Agent 处理完成: 消息='{message}', 检索结果数={len(search_results)}")
                return result
            
            else:
                # 其他意图处理（后续扩展）
                response = "抱歉，我暂时只能处理问答类型的请求。"
                return {
                    "session_id": session_id,
                    "response": response,
                    "extracted_memories": [],
                    "retrieved_context": []
                }
                
        except Exception as e:
            logger.error(f"Agent 处理失败: {str(e)}")
            return {
                "session_id": session_id,
                "response": f"处理失败: {str(e)}",
                "extracted_memories": [],
                "retrieved_context": []
            }

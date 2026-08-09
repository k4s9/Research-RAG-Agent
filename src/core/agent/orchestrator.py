from typing import List, Dict, Any, Optional
from loguru import logger
from src.utils.llm_client import LLMClient
from src.core.retrieval.hybrid_search import HybridSearch
from src.core.agent.prompt_templates import get_rag_prompt
from src.core.citations import extract_citation_ids, validate_citation_ids
from src.core.retrieval.hydration import hydrate_search_results


class AgentOrchestrator:
    def __init__(self, llm_client=None, searcher=None, hydrator=hydrate_search_results):
        self.llm_client = llm_client or LLMClient()
        self.searcher = searcher or HybridSearch()
        self.hydrator = hydrator

    async def handle_message(
        self,
        message: str,
        project_ids: List[str],
        session_id: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """处理用户消息并返回 Agent 响应"""
        try:
            # 1. 意图识别（简化版，直接视为问答/检索型）
            intent = "qa"

            # 2. 根据意图执行相应操作
            if intent == "qa":
                # 执行检索
                search_results = self.searcher.search(
                    query=message, project_ids=project_ids, top_k=5
                )
                search_results = await self.hydrator(search_results, project_ids)

                # 构建 RAG Prompt
                prompt = get_rag_prompt(message, search_results, history)

                # 调用 LLM 生成回答
                response = self.llm_client.generate(
                    prompt=prompt, history=history, session_id=session_id, project_ids=project_ids
                )

                # 构建返回结果
                sources = {
                    f"S{i + 1}": {
                        "source_id": f"S{i + 1}",
                        "chunk_id": item.get("id"),
                        "filename": item.get("filename", item.get("source", "")),
                        "locator": item.get("locator", {}),
                        "quote": item.get("content", "")[:500],
                    }
                    for i, item in enumerate(search_results)
                }
                valid_ids = validate_citation_ids(response, sources)
                invalid_ids = [
                    source_id
                    for source_id in extract_citation_ids(response)
                    if source_id not in sources
                ]
                result = {
                    "session_id": session_id,
                    "response": response,
                    "extracted_memories": [],
                    "retrieved_context": [
                        {
                            "chunk_id": item.get("id"),
                            "content_preview": item.get("content", "")[:500],
                            "source": item.get("filename", item.get("source", "")),
                            "filename": item.get("filename", ""),
                            "locator": item.get("locator", {}),
                            "relevance_score": item.get("score", 0),
                        }
                        for item in search_results[:3]
                    ],
                }
                result["citations"] = [sources[source_id] for source_id in valid_ids]
                result["invalid_citation_ids"] = invalid_ids

                logger.info(f"Agent 处理完成: 消息='{message}', 检索结果数={len(search_results)}")
                return result

            else:
                # 其他意图处理（后续扩展）
                response = "抱歉，我暂时只能处理问答类型的请求。"
                return {
                    "session_id": session_id,
                    "response": response,
                    "extracted_memories": [],
                    "retrieved_context": [],
                }

        except Exception as e:
            logger.error(f"Agent 处理失败: {str(e)}")
            raise

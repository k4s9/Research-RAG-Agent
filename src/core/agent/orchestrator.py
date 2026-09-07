import json
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from src.core.agent.prompt_templates import get_rag_prompt
from src.core.agent.tools import TOOL_DEFINITIONS, ResearchToolRegistry, ToolExecutionError
from src.core.citations import extract_citation_ids, validate_citation_ids
from src.core.retrieval.hybrid_search import HybridSearch
from src.core.retrieval.hydration import hydrate_search_results
from src.utils.llm_client import LLMClient


class AgentOrchestrator:
    def __init__(
        self,
        llm_client: object | None = None,
        searcher: object | None = None,
        hydrator: Callable[..., Awaitable[list[dict[str, Any]]]] = hydrate_search_results,
        tool_registry: ResearchToolRegistry | None = None,
    ) -> None:
        self.llm_client = llm_client or LLMClient()
        self.searcher = searcher or HybridSearch()
        self.hydrator = hydrator
        self.tool_registry = tool_registry or ResearchToolRegistry(self.searcher, hydrator=hydrator)

    async def handle_message(
        self,
        message: str,
        project_ids: list[str],
        session_id: str,
        history: list[dict[str, str]] | None = None,
        include_outdated: bool = False,
    ) -> dict[str, Any]:
        """处理用户消息并返回 Agent 响应"""
        try:
            if hasattr(self.llm_client, "generate_with_tools"):
                try:
                    tool_result = await self._handle_with_tools(
                        message=message,
                        project_ids=project_ids,
                        session_id=session_id,
                        history=history,
                        include_outdated=include_outdated,
                    )
                except Exception as exc:
                    logger.warning(f"Tool calling 失败，回退到标准 RAG: {exc}")
                    tool_result = None
                if tool_result is not None:
                    return tool_result
            # 1. 意图识别（简化版，直接视为问答/检索型）
            intent = "qa"

            # 2. 根据意图执行相应操作
            if intent == "qa":
                # 执行检索
                search_results = self.searcher.search(
                    query=message,
                    project_ids=project_ids,
                    top_k=5,
                    include_outdated=include_outdated,
                )
                search_results = await self.hydrator(search_results, project_ids)

                # 构建 RAG Prompt
                prompt = get_rag_prompt(message, search_results, history)

                # 调用 LLM 生成回答
                response = self.llm_client.generate(
                    prompt=prompt,
                    history=history,
                    session_id=session_id,
                    project_ids=project_ids,
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

    async def _handle_with_tools(
        self,
        *,
        message: str,
        project_ids: list[str],
        session_id: str,
        history: list[dict[str, str]] | None,
        include_outdated: bool,
    ) -> dict[str, Any] | None:
        """Run at most four native tool-calling steps, then validate citations."""
        tool_messages: list[dict[str, Any]] = []
        trace: list[dict[str, Any]] = []
        seen_calls: set[tuple[str, str]] = set()
        current_prompt = (
            "Use search_knowledge for factual project questions. "
            "Use get_document_section when exact source text is needed. "
            "Use save_memory only when the user explicitly asks to remember something.\n\n"
            f"User question: {message}"
        )
        for step in range(4):
            envelope = self.llm_client.generate_with_tools(
                current_prompt,
                history=history,
                tools=TOOL_DEFINITIONS,
                tool_messages=tool_messages,
            )
            model_message = envelope.get("message", {})
            tool_calls = model_message.get("tool_calls") or []
            if not tool_calls:
                answer = model_message.get("content")
                if not isinstance(answer, str):
                    return None
                if not trace:
                    # A provider may decline tools for a normal conversational
                    # turn. Fall back to the established RAG path in that case.
                    return None
                # Tool-enabled providers are responsible for their final
                # answer, but return the same response contract as the fixed
                # RAG path. Citations are mapped from tool search payloads.
                search_items: list[dict[str, Any]] = []
                for entry in trace:
                    if entry.get("tool") == "search_knowledge":
                        search_items.extend(entry.get("result", {}).get("results", []))
                    elif entry.get("tool") == "get_document_section":
                        section = entry.get("result", {})
                        if section.get("chunk_id"):
                            search_items.append(
                                {
                                    "id": section.get("chunk_id"),
                                    "content": section.get("content", ""),
                                    "filename": section.get("filename", ""),
                                    "locator": section.get("locator", {}),
                                    "score": section.get("score", 1.0),
                                },
                            )
                sources = {f"S{i + 1}": item for i, item in enumerate(search_items)}
                valid_ids = validate_citation_ids(answer, sources)
                citations = [
                    {
                        "source_id": source_id,
                        "chunk_id": sources[source_id].get("id"),
                        "filename": sources[source_id].get("filename", ""),
                        "locator": sources[source_id].get("locator", {}),
                        "quote": sources[source_id].get("content", "")[:500],
                    }
                    for source_id in valid_ids
                ]
                return {
                    "session_id": session_id,
                    "response": answer,
                    "extracted_memories": [],
                    "retrieved_context": [
                        {
                            "chunk_id": item.get("id"),
                            "content_preview": item.get("content", "")[:500],
                            "source": item.get("filename", ""),
                            "filename": item.get("filename", ""),
                            "locator": item.get("locator", {}),
                            "relevance_score": item.get("score", 0),
                        }
                        for item in sources.values()
                    ],
                    "citations": citations,
                    "invalid_citation_ids": [
                        source_id
                        for source_id in extract_citation_ids(answer)
                        if source_id not in sources
                    ],
                    "tool_trace": trace,
                }

            assistant_message = {
                "role": "assistant",
                "content": model_message.get("content"),
                "tool_calls": tool_calls,
            }
            if not tool_messages:
                tool_messages.append({"role": "user", "content": message})
            tool_messages.append(assistant_message)
            for call in tool_calls:
                function = call.get("function", {})
                name = function.get("name")
                raw_arguments = function.get("arguments", "{}")
                try:
                    arguments = (
                        json.loads(raw_arguments)
                        if isinstance(raw_arguments, str)
                        else raw_arguments
                    )
                    if (
                        name == "search_knowledge"
                        and include_outdated
                        and isinstance(arguments, dict)
                    ):
                        arguments.setdefault("include_outdated", True)
                    call_key = (str(name), json.dumps(arguments, sort_keys=True))
                    if call_key in seen_calls:
                        raise ToolExecutionError("repeated tool call rejected")
                    seen_calls.add(call_key)
                    result = await self.tool_registry.execute(
                        str(name),
                        arguments,
                        project_ids=project_ids,
                        session_id=session_id,
                    )
                    error = None
                except Exception as exc:
                    result = {"error": str(exc)}
                    error = str(exc)
                trace.append(
                    {
                        "step": step + 1,
                        "tool": name,
                        "arguments": arguments,
                        "result": result,
                        "error": error,
                    },
                )
                tool_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id", f"call-{step}"),
                        "content": json.dumps(result, ensure_ascii=False),
                    },
                )
            current_prompt = ""
        return None

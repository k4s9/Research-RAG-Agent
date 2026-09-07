import json
import os
from datetime import datetime
from pathlib import Path

import requests
from loguru import logger

from src.config.settings import settings
from src.utils.retry import retry_sync


def _get_prompt_log_dir() -> Path:
    log_dir = Path("./data/logs/prompts")
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _generate_prompt_log_filename() -> str:
    now = datetime.now()
    return f"prompt_{now.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.jsonl"


def _write_prompt_log(log_file: Path, log_entry: dict):
    try:
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"写入 Prompt 日志失败: {e}")


class LLMClient:
    def __init__(self):
        self.provider = settings.llm_provider
        self.api_key = settings.llm_api_key
        self.model_name = settings.llm_model_name
        self.base_url = settings.llm_base_url
        self._prompt_log_enabled = True
        self._current_log_file = None

    def generate(
        self,
        prompt: str,
        history: list[dict[str, str]] | None = None,
        temperature: float = 0.7,
        session_id: str | None = None,
        project_ids: list[str] | None = None,
    ) -> str:
        """调用 LLM API 生成回答"""
        call_id = f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        log_file = _get_prompt_log_dir() / _generate_prompt_log_filename()

        request_log = {
            "call_id": call_id,
            "type": "request",
            "timestamp": datetime.now().isoformat(),
            "provider": self.provider,
            "model": self.model_name,
            "session_id": session_id,
            "project_ids": project_ids,
            "temperature": temperature,
            "history_count": len(history) if history else 0,
            "prompt_length": len(prompt),
            "prompt": prompt,
        }

        if self._prompt_log_enabled:
            _write_prompt_log(log_file, request_log)

        try:
            if self.provider == "local":
                raw_response = self._call_local(prompt)
            elif self.provider == "deepseek":
                raw_response = self._call_deepseek(prompt, history, temperature)
            elif self.provider == "openai":
                raw_response = self._call_openai(prompt, history, temperature)
            else:
                raise ValueError(f"不支持的 LLM 提供商: {self.provider}")

            response_log = {
                "call_id": call_id,
                "type": "response",
                "timestamp": datetime.now().isoformat(),
                "success": True,
                "response_length": len(raw_response),
                "response": raw_response,
            }

            if self._prompt_log_enabled:
                _write_prompt_log(log_file, response_log)

            return raw_response

        except Exception as e:
            error_log = {
                "call_id": call_id,
                "type": "error",
                "timestamp": datetime.now().isoformat(),
                "success": False,
                "error_type": type(e).__name__,
                "error_message": str(e),
            }
            if self._prompt_log_enabled:
                _write_prompt_log(log_file, error_log)

            logger.error(f"LLM 调用失败: {str(e)}")
            raise

    def generate_with_tools(
        self,
        prompt: str,
        *,
        history: list[dict[str, str]] | None = None,
        tools: list[dict] | None = None,
        tool_messages: list[dict] | None = None,
        temperature: float = 0.2,
    ) -> dict:
        """Return the native chat completion envelope, including tool calls.

        This is deliberately separate from ``generate`` so existing callers
        and deterministic local tests keep their string-returning contract.
        """
        if self.provider == "local":
            return {"message": {"role": "assistant", "content": self._call_local(prompt)}}
        if self.provider not in {"deepseek", "openai"}:
            raise ValueError(f"不支持的 LLM 提供商: {self.provider}")
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        messages = list(history or [])
        if prompt:
            messages.append({"role": "user", "content": prompt})
        messages.extend(tool_messages or [])
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 1024,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        response = retry_sync(
            lambda: requests.post(url, json=payload, headers=headers, timeout=30),
            attempts=settings.request_retry_attempts,
            backoff_seconds=settings.request_retry_backoff_seconds,
        )
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0].get("message"), dict):
            raise RuntimeError("LLM response is missing choices[0].message")
        return {"message": choices[0]["message"], "usage": data.get("usage", {})}

    @staticmethod
    def _call_local(prompt: str) -> str:
        """Deterministic extractive response for local workflow verification only."""
        marker = "内容:"
        content = (
            prompt.split(marker, 1)[1].split("\n来源:", 1)[0].strip() if marker in prompt else ""
        )
        if not content:
            return "当前资料不足，无法确认。"
        return f"本地流程验证结果：{content[:300]} [S1]"

    def _call_deepseek(
        self, prompt: str, history: list[dict[str, str]] | None = None, temperature: float = 0.7,
    ) -> str:
        """调用 DeepSeek API"""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        messages = []
        # 添加历史对话
        if history:
            for msg in history:
                messages.append({"role": msg["role"], "content": msg["content"]})
        # 添加当前提示
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 1024,
        }

        response = retry_sync(
            lambda: requests.post(url, json=payload, headers=headers, timeout=30),
            attempts=settings.request_retry_attempts,
            backoff_seconds=settings.request_retry_backoff_seconds,
        )
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["message"]["content"]

    def _call_openai(
        self, prompt: str, history: list[dict[str, str]] | None = None, temperature: float = 0.7,
    ) -> str:
        """调用 OpenAI API"""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        messages = []
        # 添加历史对话
        if history:
            for msg in history:
                messages.append({"role": msg["role"], "content": msg["content"]})
        # 添加当前提示
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 1024,
        }

        response = retry_sync(
            lambda: requests.post(url, json=payload, headers=headers, timeout=30),
            attempts=settings.request_retry_attempts,
            backoff_seconds=settings.request_retry_backoff_seconds,
        )
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["message"]["content"]

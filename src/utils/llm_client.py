from typing import List, Dict, Any, Optional
import requests
import json
import os
from datetime import datetime
from pathlib import Path
from loguru import logger
from src.config.settings import settings


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

    def generate(self, prompt: str, history: Optional[List[Dict[str, str]]] = None, temperature: float = 0.7, session_id: Optional[str] = None, project_ids: Optional[List[str]] = None) -> str:
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
            if self.provider == "deepseek":
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
            return f"错误: {str(e)}"
    
    def _call_deepseek(self, prompt: str, history: Optional[List[Dict[str, str]]] = None, temperature: float = 0.7) -> str:
        """调用 DeepSeek API"""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
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
            "max_tokens": 1024
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["message"]["content"]
    
    def _call_openai(self, prompt: str, history: Optional[List[Dict[str, str]]] = None, temperature: float = 0.7) -> str:
        """调用 OpenAI API"""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
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
            "max_tokens": 1024
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["message"]["content"]

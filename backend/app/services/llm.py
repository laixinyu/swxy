"""对话大模型客户端（OpenAI 兼容接口），全进程复用连接池。"""
import json
import re
import threading

from openai import OpenAI

from core.config import settings

_client_lock = threading.Lock()
_client: OpenAI | None = None
_CODE_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


def get_llm_client() -> OpenAI:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = OpenAI(
                    api_key=settings.dashscope_api_key,
                    base_url=settings.dashscope_base_url,
                    timeout=settings.chat_timeout,
                    max_retries=1,
                )
    return _client


def complete_json(prompt: str, timeout: int = 30, model: str = "") -> dict:
    """要求模型只返回 JSON。部分模型会包一层 ```json 代码块，这里剥掉再解析。"""
    completion = get_llm_client().chat.completions.create(
        model=model or settings.chat_model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        timeout=timeout,
    )
    content = (completion.choices[0].message.content or "").strip() if completion.choices else ""
    match = _CODE_FENCE.match(content)
    return json.loads(match.group(1) if match else content)

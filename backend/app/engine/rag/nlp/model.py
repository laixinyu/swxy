import logging
import threading
import time
from typing import List

import httpx
import numpy as np
from openai import OpenAI

from core.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_embedding_client: OpenAI | None = None
_rerank_client: httpx.Client | None = None


def _with_retry(fn, what: str, attempts: int = 3, base_delay: float = 1.0):
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            if attempt == attempts:
                raise
            delay = base_delay * 2 ** (attempt - 1)
            logger.warning("%s 失败（第 %d 次），%.0f 秒后重试: %s", what, attempt, delay, e)
            time.sleep(delay)


def get_embedding_client() -> OpenAI:
    """向量走阿里云百炼，和对话用的中转站分开。客户端线程安全，全进程复用连接池。"""
    global _embedding_client
    if _embedding_client is None:
        with _lock:
            if _embedding_client is None:
                _embedding_client = OpenAI(
                    api_key=settings.embedding_api_key,
                    base_url=settings.embedding_base_url,
                    timeout=20,
                    max_retries=0,
                )
    return _embedding_client


def _get_rerank_client() -> httpx.Client:
    global _rerank_client
    if _rerank_client is None:
        with _lock:
            if _rerank_client is None:
                _rerank_client = httpx.Client(timeout=settings.rerank_timeout)
    return _rerank_client


def rerank_similarity(query, texts):
    """返回 (分数数组, None)。重排失败时返回全 0，排序退化为只看关键词分，并记录告警。"""
    texts = list(texts)
    scores = np.zeros(len(texts), dtype=float)
    if not texts:
        return scores, None

    api_key = settings.effective_rerank_api_key
    if not api_key:
        logger.warning("未配置重排密钥，排序只使用关键词分")
        return scores, None

    documents = [text[:12000] for text in texts]
    body = {
        "model": settings.rerank_model,
        "input": {"query": query[:12000], "documents": documents},
        "parameters": {
            "top_n": len(documents),
            "instruct": "Given a web search query, retrieve relevant passages that answer the query.",
        },
    }

    def _call():
        response = _get_rerank_client().post(
            settings.rerank_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
        )
        payload = response.json()
        results = (payload.get("output") or {}).get("results") if response.status_code < 400 else None
        if not results:
            raise RuntimeError(payload.get("message") or payload.get("code") or response.status_code)
        return results

    try:
        results = _with_retry(_call, "重排", attempts=2, base_delay=0.5)
    except Exception as e:
        logger.warning("重排失败，排序只使用关键词分: %s", e)
        return scores, None

    for item in results:
        index = item.get("index")
        if index is None or index >= len(scores):
            continue
        scores[index] = float(item.get("relevance_score") or 0)
    return scores, None


def generate_embedding(text: str | List[str]):
    """用阿里云百炼生成向量，维度与 ES 字段 q_{维度}_vec 对应。传字符串返回一个向量，传列表返回向量列表。"""
    client = get_embedding_client()

    def _create(payload):
        return _with_retry(
            lambda: client.embeddings.create(
                model=settings.embedding_model,
                input=payload,
                dimensions=settings.embedding_dimensions,
                encoding_format="float",
            ),
            "向量生成",
        )

    if isinstance(text, str):
        try:
            return _create(text).data[0].embedding
        except Exception as e:
            raise RuntimeError(f"向量生成失败: {e}") from e

    all_embeddings = []
    batch_size = settings.embedding_batch_size
    for i in range(0, len(text), batch_size):
        batch = text[i:i + batch_size]
        try:
            completion = _create(batch)
        except Exception as e:
            raise RuntimeError(f"向量批量生成失败 (batch {i // batch_size + 1}): {e}") from e
        all_embeddings.extend(item.embedding for item in completion.data)
    return all_embeddings

from openai import OpenAI
import httpx
import numpy as np
from typing import List

import os
from dotenv import load_dotenv
load_dotenv()

def get_chat_completion_block(session_id, question, references):
    """
    结合知识库内容生成回答，并在回答中标注引用来源。

    :param question: 用户问题
    :param references: 知识库内容，格式为 [{"id": 1, "content": "..."}, ...]
    :return: 模型的回答
    """
    try:
        
        # 初始化 OpenAI 客户端
        client = OpenAI(
            api_key=os.getenv("DASHSCOPE_API_KEY"),
            base_url=os.getenv("DASHSCOPE_BASE_URL")
        )
        # 格式化参考内容
        formatted_references = "\n".join([f"[{ref['id']}] {ref['content']}" for ref in references])
    
        # 构造提示词
        
    
        # 调用模型生成回答
        completion = client.chat.completions.create(
            model="gpt-6.1-sol",
            messages=[{"role": "user", "content": prompt}],
            stream=False,
        )
    
        return completion.choices[0].message.content

    except Exception as e:
        return f"Error: {str(e)}"

def rerank_similarity(query, texts):
    texts = list(texts)
    scores = np.zeros(len(texts), dtype=float)
    if not texts:
        return scores, None

    # 和向量同一个百炼北京密钥。对话中转站的密钥不能拿来重排。
    api_key = os.getenv("RERANK_API_KEY") or os.getenv("EMBEDDING_API_KEY")
    if not api_key:
        print("[rerank_similarity] 未配置百炼密钥，保留向量召回顺序")
        return scores, None

    model = os.getenv("RERANK_MODEL", "qwen3.7-text-rerank")
    url = os.getenv(
        "RERANK_URL",
        "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
    )
    documents = [text[:12000] for text in texts]
    body = {
        "model": model,
        "input": {"query": query[:12000], "documents": documents},
        "parameters": {
            "top_n": len(documents),
            "instruct": "Given a web search query, retrieve relevant passages that answer the query.",
        },
    }
    try:
        response = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=20,
        )
        payload = response.json()
    except Exception as e:
        print(f"[rerank_similarity] 重排失败，保留向量召回顺序: {e}")
        return scores, None

    results = ((payload.get("output") or {}).get("results")) if response.status_code < 400 else None
    if not results:
        message = payload.get("message") or payload.get("code") or response.status_code
        print(f"[rerank_similarity] 重排失败，保留向量召回顺序: {message}")
        return scores, None

    for item in results:
        index = item.get("index")
        if index is None or index >= len(scores):
            continue
        scores[index] = float(item.get("relevance_score") or 0)
    return scores, None




def _embedding_client():
    """向量走阿里云百炼北京地域，和对话用的中转站分开。"""
    api_key = os.getenv("EMBEDDING_API_KEY")
    base_url = os.getenv(
        "EMBEDDING_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    if not api_key:
        raise RuntimeError("未配置 EMBEDDING_API_KEY，向量模型需要阿里云百炼（北京）的 API Key")
    return OpenAI(api_key=api_key, base_url=base_url, timeout=20)


def generate_embedding(text: str | List[str], api_key: str = None, base_url: str = None, model_name: str = "qwen3.7-text-embedding", dimensions: int = 1024, encoding_format: str = "float", max_batch_size: int = 10):
    """
    用阿里云百炼 qwen3.7-text-embedding 生成向量。
    默认 1024 维，和现有 Elasticsearch 字段 q_1024_vec 一致。
    该模型单批最多 20 条，这里按 10 条分批。
    """
    model_name = os.getenv("EMBEDDING_MODEL", model_name)
    client = _embedding_client()

    def _create(payload):
        completion = client.embeddings.create(
            model=model_name,
            input=payload,
            dimensions=dimensions,
            encoding_format=encoding_format,
        )
        return completion

    if isinstance(text, str):
        try:
            completion = _create(text)
            return completion.data[0].embedding
        except Exception as e:
            raise RuntimeError(f"向量生成失败: {e}") from e

    if isinstance(text, list):
        all_embeddings = []
        for i in range(0, len(text), max_batch_size):
            batch = text[i:i + max_batch_size]
            try:
                completion = _create(batch)
                all_embeddings.extend(item.embedding for item in completion.data)
            except Exception as e:
                raise RuntimeError(
                    f"向量批量生成失败 (batch {i // max_batch_size + 1}): {e}"
                ) from e
        return all_embeddings


# 示例调用
if __name__ == "__main__":
    # 示例调用
    question = "法国的首都是哪里？"
    references = [
        {"id": 1, "content": "法国的首都是巴黎。"},
        {"id": 2, "content": "巴黎是欧洲的文化中心之一。"},
    ]
    session_id = "sd"
    
    response = get_chat_completion_block(session_id, question, references)
    print(response)
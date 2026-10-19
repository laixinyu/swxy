"""回答生成：拼提示词、流式调用大模型、推送 SSE、保存对话。"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Iterator

from core.config import settings
from db.database import session_scope
from services import quick_parse
from services.llm import complete_json, get_llm_client
from services.session import rename_session, save_message

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="chat-aux")

SYSTEM_PROMPT = """你是一个专业的智能助手，擅长基于提供的参考资料回答用户问题。请遵循以下原则：
1. 优先基于参考内容回答，确保答案准确可靠。
2. 参考内容按 [0]、[1]、[2] …… 编号。回答中用到参考内容的地方，要在该句末尾标注来源，格式为 ##编号$$，例如 ##0$$ 表示来自第 [0] 条参考内容。
3. 参考内容不足以完全回答问题时，可以结合常识补充，但要明确说明哪些是补充内容。
4. 没有相关参考内容时，如实说明，并给出一般性建议。
5. 回答要条理清晰、语言自然流畅。
6. 这是多轮对话，前面的问答只用来理解当前问题指的是什么。引用编号只对应本轮提供的参考内容。"""


def build_references(session_id: str, retrieved: list[dict]) -> list[dict]:
    """提示词里的编号和返回给前端的 documents 必须一一对应，前端按下标取引用。"""
    references = list(retrieved)
    quick_content = quick_parse.get_content(session_id)
    if quick_content:
        limit = settings.quick_parse_prompt_chars
        text = quick_content[:limit] + ("...(内容已截断)" if len(quick_content) > limit else "")
        references.append({
            "document_id": f"quick_parse_{session_id}",
            "document_name": "当前会话文档",
            "content_with_weight": text,
            "positions": [],
        })
    return references


def format_references(references: list[dict]) -> str:
    if not references:
        return "暂无相关参考内容"
    return "\n\n".join(
        f"[{i}] 《{ref['document_name']}》\n{ref['content_with_weight']}" for i, ref in enumerate(references)
    )


def generate_recommended_questions(question: str, document_names: list[str]) -> list[str]:
    context = f"当前对话基于这些文档：{', '.join(document_names)}" if document_names else ""
    prompt = f"""请基于用户的问题生成 3 个相关的推荐问题，帮助用户更深入地探索这个话题。

用户问题：{question}
{context}

要求：
1. 和用户问题相关，但从不同角度深入
2. 问题具体、有价值
3. 只返回 JSON：{{"recommended_questions": ["问题1", "问题2", "问题3"]}}"""
    try:
        questions = complete_json(prompt).get("recommended_questions", [])
        return [q for q in questions if isinstance(q, str) and q.strip()][:3]
    except Exception:
        logger.warning("生成推荐问题失败", exc_info=True)
        return []


def generate_session_name(question: str) -> str:
    prompt = f"""请根据用户提问生成一个简洁、能概括主题的会话名称，不超过 20 个字。

用户提问：{question}

只返回 JSON：{{"session_name": "会话名称"}}"""
    try:
        return str(complete_json(prompt).get("session_name") or "").strip()[:50]
    except Exception:
        logger.warning("生成会话名称失败", exc_info=True)
        return question[:20]


def _name_session(session_id: str, question: str) -> None:
    try:
        name = generate_session_name(question)
        with session_scope() as db:
            rename_session(db, session_id, name)
    except Exception:
        logger.exception("会话 %s 命名失败", session_id)


def _sse(event: str, payload) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def stream_chat(session_id: str, question: str, retrieved: list[dict], need_name: bool,
                history: list[dict] | None = None, retrieval_query: str | None = None) -> Iterator[str]:
    """history 是之前几轮的 user / assistant 消息；retrieval_query 是结合上下文改写后、实际拿去检索的问题。"""
    retrieval_query = retrieval_query or question
    references = build_references(session_id, retrieved)
    document_names = list(dict.fromkeys(ref["document_name"] for ref in retrieved))[:3]
    recommended_future = _executor.submit(generate_recommended_questions, retrieval_query, document_names)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *(history or []),
        {"role": "user", "content": f"参考内容：\n{format_references(references)}\n\n用户问题：\n{question}"},
    ]
    logger.debug("提示词: %s", messages)

    answer, think = [], []
    try:
        if retrieval_query != question:
            yield _sse("message", {"retrieval_query": retrieval_query})
        yield _sse("message", {"documents": references})
        stream = get_llm_client().chat.completions.create(
            model=settings.chat_model, messages=messages, stream=True,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            content = getattr(delta, "content", None) or ""
            reasoning = getattr(delta, "reasoning_content", None) or ""
            if content:
                answer.append(content)
                yield _sse("message", {"role": "assistant", "content": content, "thinking": False})
            elif reasoning:
                think.append(reasoning)
                yield _sse("message", {"role": "assistant", "content": reasoning, "thinking": True})
    except Exception:
        logger.exception("会话 %s 生成回答失败", session_id)
        recommended_future.cancel()
        yield _sse("error", {"error": "生成回答失败，请稍后重试"})
        return

    try:
        recommended = recommended_future.result(timeout=15)
    except Exception:
        recommended = []
    if recommended:
        yield _sse("message", {"recommended_questions": recommended})

    try:
        with session_scope() as db:
            save_message(db, session_id, question, "".join(answer), references, recommended, "".join(think),
                         retrieval_query=retrieval_query)
    except Exception:
        logger.exception("会话 %s 保存对话失败", session_id)
        yield _sse("error", {"error": "回答已生成，但保存对话记录失败"})

    if need_name:
        _executor.submit(_name_session, session_id, retrieval_query)
    yield "event: end\ndata: [DONE]\n\n"

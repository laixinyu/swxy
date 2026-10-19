"""多轮对话：把历史整理成模型消息，并把追问改写成可以单独检索的问题。"""
import logging
import re

from core.config import settings
from models import Message
from services.llm import complete_json

logger = logging.getLogger(__name__)

_CITATION = re.compile(r"\s*##\d+\$\$")

REWRITE_PROMPT = """你负责把用户在多轮对话中的最新问题，改写成一个不依赖上下文、可以单独拿去检索资料的问题。

要求：
1. 把代词和省略的内容补全，例如“它”“那家公司”“净利率呢”要补成具体的对象和指标。
2. 保留原问题的意图、时间范围和专有名词，不要回答问题，也不要添加对话里没有的信息。
3. 如果最新问题本身已经完整，原样返回。
4. 只返回 JSON：{"query": "改写后的问题"}

对话历史：
{history}

最新问题：{question}"""


def history_messages(messages: list[Message]) -> list[dict]:
    """取最近几轮问答，转成模型的 user / assistant 消息。

    历史回答里的 ##n$$ 引用指向的是当轮的参考列表，和本轮编号无关，必须去掉，否则模型会乱引用。
    """
    recent = messages[-settings.chat_history_turns:] if settings.chat_history_turns > 0 else []
    result = []
    for message in recent:
        answer = _CITATION.sub("", message.model_answer or "").strip()
        limit = settings.chat_history_answer_chars
        if len(answer) > limit:
            answer = answer[:limit] + "……"
        result.append({"role": "user", "content": message.user_question})
        result.append({"role": "assistant", "content": answer})
    return result


def rewrite_question(question: str, history: list[dict]) -> str:
    """没有历史时直接返回原问题。改写失败时也返回原问题，不影响回答。"""
    if not history:
        return question
    lines = [f"{'用户' if item['role'] == 'user' else '助手'}：{item['content'][:500]}" for item in history]
    # 用 replace 而不是 format：用户文字里的花括号不能被当成占位符
    prompt = REWRITE_PROMPT.replace("{history}", "\n".join(lines)).replace("{question}", question)
    try:
        result = complete_json(prompt, timeout=settings.query_rewrite_timeout, model=settings.rewrite_model)
        query = str(result.get("query") or "").strip()
    except Exception:
        logger.warning("改写问题失败，使用原问题检索", exc_info=True)
        return question
    if not query or len(query) > 500:
        return question
    if query != question:
        logger.info("问题改写: %s -> %s", question, query)
    return query

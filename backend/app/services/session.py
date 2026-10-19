"""会话与消息。所有按 session_id 访问的接口都必须先经过 get_owned_session 校验归属。"""
import json
import re
import uuid

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from models import DEFAULT_SESSION_NAME, ChatSession, Message

_SESSION_ID = re.compile(r"^[0-9a-f]{16}$")


def new_session_id() -> str:
    return uuid.uuid4().hex[:16]


def create_session(db: Session, user_id: str) -> ChatSession:
    session = ChatSession(session_id=new_session_id(), user_id=user_id, session_name=DEFAULT_SESSION_NAME)
    db.add(session)
    db.commit()
    return session


def get_owned_session(db: Session, session_id: str, user_id: str, create: bool = False) -> ChatSession:
    """会话不属于当前用户时一律返回 404，不暴露会话是否存在。

    create=True 时，不存在的会话会归当前用户所有。旧版前端在第一次提问前不会把会话写库，需要兼容。
    """
    if not _SESSION_ID.match(session_id or ""):
        raise HTTPException(status_code=400, detail="会话 ID 格式错误")
    session = db.get(ChatSession, session_id)
    if session is None and create:
        session = ChatSession(session_id=session_id, user_id=user_id, session_name=DEFAULT_SESSION_NAME)
        db.add(session)
        try:
            db.commit()
        except IntegrityError:
            # 同一个新会话的两个请求同时到达，另一个已经建好了
            db.rollback()
            session = db.get(ChatSession, session_id)
    if session is None or session.user_id != user_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    return session


def list_sessions(db: Session, user_id: str) -> list[ChatSession]:
    """只返回有过对话的会话，空会话不出现在历史列表里。"""
    has_message = db.query(Message.session_id).filter(Message.session_id == ChatSession.session_id).exists()
    return (
        db.query(ChatSession)
        .filter(ChatSession.user_id == user_id, has_message)
        .order_by(ChatSession.updated_at.desc())
        .all()
    )


def list_messages(db: Session, session_id: str) -> list[Message]:
    return (
        db.query(Message)
        .filter(Message.session_id == session_id)
        .order_by(Message.created_at.asc())
        .all()
    )


def save_message(db: Session, session_id: str, question: str, answer: str, documents: list,
                 recommended_questions: list, think: str, retrieval_query: str | None = None) -> Message:
    message = Message(
        session_id=session_id,
        user_question=question,
        retrieval_query=retrieval_query or question,
        model_answer=answer,
        documents=json.dumps(documents, ensure_ascii=False),
        recommended_questions=json.dumps(recommended_questions, ensure_ascii=False),
        think=think,
    )
    db.add(message)
    session = db.get(ChatSession, session_id)
    if session is not None:
        session.updated_at = func.now()
    db.commit()
    return message


def rename_session(db: Session, session_id: str, name: str) -> None:
    session = db.get(ChatSession, session_id)
    if session is not None and session.session_name == DEFAULT_SESSION_NAME and name:
        session.session_name = name[:255]
        db.commit()


def get_scope(session: ChatSession) -> list[str]:
    """会话的检索范围。空列表表示检索全部文档。"""
    try:
        doc_ids = json.loads(session.doc_ids) if session.doc_ids else []
    except ValueError:
        return []
    return [doc_id for doc_id in doc_ids if isinstance(doc_id, str)]


def set_scope(db: Session, session: ChatSession, doc_ids: list[str]) -> None:
    session.doc_ids = json.dumps(doc_ids) if doc_ids else None
    db.commit()

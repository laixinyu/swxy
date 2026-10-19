from typing import List
from urllib.parse import unquote

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.deps import current_user_id
from db.database import get_db
from schemas.message import FilestResponse, SessionListResponse, SessionResponse
from services import documents as document_service
from services.session import get_owned_session, list_messages, list_sessions

router = APIRouter()


def _fmt(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


@router.get("/get_files", response_model=List[FilestResponse])
def get_documents_by_user_id(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    return [
        FilestResponse(
            id=doc.id,
            user_id=doc.user_id,
            file_name=doc.file_name,
            file_size=doc.file_size,
            status=doc.status,
            chunk_count=doc.chunk_count,
            progress=doc.progress,
            progress_msg=doc.progress_msg,
            error=doc.error,
            created_at=doc.created_at.isoformat(),
            updated_at=doc.updated_at.isoformat(),
        )
        for doc in document_service.list_documents(db, user_id)
    ]


@router.post("/documents/{doc_id}/reparse")
def reparse_document_endpoint(doc_id: str, user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    """重新解析：用于解析失败的文档，或者改了切块参数后单独更新某个文档。"""
    doc = document_service.reparse_document(db, user_id, doc_id)
    return {"id": doc.id, "status": doc.status}


@router.delete("/delete_file/{file_name}")
def delete_document_endpoint(file_name: str, user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    deleted = document_service.delete_document(db, user_id, unquote(file_name))
    return {"message": f"已删除，清理 {deleted} 个切片"}


@router.get("/get_messages")
def get_messages_by_session_id(session_id: str, user_id: str = Depends(current_user_id),
                               db: Session = Depends(get_db)):
    get_owned_session(db, session_id, user_id)
    return [
        {
            "message_id": message.message_id,
            "session_id": message.session_id,
            "user_question": message.user_question,
            "retrieval_query": message.retrieval_query,
            "model_answer": message.model_answer,
            "documents": message.documents,
            "recommended_questions": message.recommended_questions,
            "think": message.think,
            "created_at": _fmt(message.created_at),
        }
        for message in list_messages(db, session_id)
    ]


@router.get("/get_sessions", response_model=SessionListResponse)
def get_sessions_by_user_id(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    sessions = [
        SessionResponse(
            session_id=session.session_id,
            session_name=session.session_name,
            user_id=session.user_id,
            created_at=_fmt(session.created_at),
            updated_at=_fmt(session.updated_at),
        )
        for session in list_sessions(db, user_id)
    ]
    return {"user_id": user_id, "sessions": sessions}

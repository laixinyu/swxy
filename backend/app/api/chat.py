"""会话、上传、问答接口。

涉及数据库、ES、外部模型调用的接口都用普通 def：FastAPI 会放进线程池执行，
不会像 async def 里直接调同步代码那样卡住整个事件循环。
"""
import logging
from typing import List

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import current_user_id
from core.config import settings
from db.database import get_db
from models import DEFAULT_SESSION_NAME, DocumentStatus
from schemas.document_upload import DocumentUploadResponse, SessionDocumentsResponse, SessionDocumentSummary
from services import documents as document_service
from services import quick_parse
from services import storage
from services.chat import stream_chat
from services.conversation import history_messages, rewrite_question
from services.retrieval import retrieve_content
from services.session import create_session as create_chat_session
from services.session import get_owned_session, get_scope, list_messages, set_scope
from services.uploads import DocumentUploadService

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ScopeRequest(BaseModel):
    # 为空表示检索全部文档
    doc_ids: List[str] = Field(default_factory=list, max_length=200)


def _scope_response(db: Session, user_id: str, doc_ids: list[str]) -> dict:
    docs = document_service.owned_documents(db, user_id, doc_ids)
    by_id = {doc.id: doc for doc in docs}
    selected = [by_id[doc_id] for doc_id in doc_ids if doc_id in by_id]
    return {
        "doc_ids": [doc.id for doc in selected],
        "documents": [{"id": doc.id, "file_name": doc.file_name, "status": doc.status} for doc in selected],
    }


@router.get("/sessions/{session_id}/scope")
def get_session_scope(session_id: str, user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    """会话的检索范围。doc_ids 为空表示检索全部文档。已删除的文档不会返回。"""
    session = get_owned_session(db, session_id, user_id, create=True)
    return _scope_response(db, user_id, get_scope(session))


@router.put("/sessions/{session_id}/scope")
def update_session_scope(session_id: str, request: ScopeRequest, user_id: str = Depends(current_user_id),
                         db: Session = Depends(get_db)):
    session = get_owned_session(db, session_id, user_id, create=True)
    requested = list(dict.fromkeys(request.doc_ids))
    owned = {doc.id for doc in document_service.owned_documents(db, user_id, requested)}
    if len(owned) != len(requested):
        raise HTTPException(status_code=404, detail="部分文档不存在")
    set_scope(db, session, requested)
    return _scope_response(db, user_id, requested)


@router.post("/create_session")
def create_session(user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    session = create_chat_session(db, user_id)
    return {"session_id": session.session_id, "status": "success", "message": "Session created successfully"}


@router.post("/quick_parse")
def quick_parse_document(
    session_id: str = Query(..., description="会话ID"),
    file: UploadFile = File(..., description="要解析的文档"),
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """会话临时文档：docx / pdf / txt，PDF 不超过 4 页，文字不超过 4000 字，保存 2 小时。"""
    get_owned_session(db, session_id, user_id, create=True)
    file_name = storage.safe_filename(file.filename)
    ext = storage.file_extension(file_name)
    content = storage.read_upload(file, settings.max_upload_bytes)
    storage.validate_content(ext, content)

    result = quick_parse.quick_parse_document(session_id, ext, content)
    result["filename"] = file_name
    DocumentUploadService.create_upload_record(
        db=db, session_id=session_id, document_name=file_name, document_type=ext, file_size=len(content),
    )
    return result


@router.get("/get_parsed_content")
def get_parsed_content(
    session_id: str = Query(..., description="会话ID"),
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    get_owned_session(db, session_id, user_id)
    return quick_parse.get_parsed_content(session_id)


@router.post("/chat_on_docs")
def chat_on_docs(
    session_id: str = Query(...),
    request: ChatRequest = Body(...),
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    session = get_owned_session(db, session_id, user_id, create=True)
    need_name = session.session_name == DEFAULT_SESSION_NAME
    question = request.message.strip()

    # 多轮对话：带上最近几轮历史，并把追问改写成可以单独检索的问题
    history = history_messages(list_messages(db, session_id))
    retrieval_query = rewrite_question(question, history)

    # 检索范围：只用还存在、已解析完成的文档。一个都不剩时不检索，避免悄悄扩大到全部文档
    scope = get_scope(session)
    doc_ids = None
    if scope:
        doc_ids = [doc.id for doc in document_service.owned_documents(db, user_id, scope)
                   if doc.status == DocumentStatus.DONE]

    references = []
    if doc_ids is None or doc_ids:
        try:
            references = retrieve_content(user_id, retrieval_query, doc_ids)
        except Exception:
            # 检索出错时仍然回答（只是没有参考内容），但必须留下日志
            logger.exception("用户 %s 检索失败", user_id)

    return StreamingResponse(
        stream_chat(session_id, question, references, need_name, history=history, retrieval_query=retrieval_query),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/upload_files")
def upload_files(
    files: List[UploadFile] = File(...),
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """保存文件并加入解析队列后立即返回。解析状态和进度通过 /get_files 的 status、progress 字段查看。"""
    accepted, failed = [], []
    for file in files:
        try:
            file_name = storage.safe_filename(file.filename)
            content = storage.read_upload(file, settings.max_upload_bytes)
            doc = document_service.create_document(db, user_id, file_name, content)
            accepted.append({"id": doc.id, "file_name": doc.file_name, "status": doc.status})
        except Exception as e:
            detail = getattr(e, "detail", None)
            if detail is None:
                logger.exception("用户 %s 上传 %s 失败", user_id, file.filename)
                detail = "上传失败"
            # 数据库出错后要回滚，否则同一请求里后面的文件都会失败
            db.rollback()
            failed.append(f"{file.filename}: {detail}")

    if not accepted:
        raise HTTPException(status_code=400, detail="；".join(failed))
    return {
        "status": "success",
        "message": "已开始解析" if not failed else f"{len(accepted)} 个文件已开始解析，{len(failed)} 个失败",
        "documents": accepted,
        "failed_files": failed,
        "total_files": len(files),
    }


@router.get("/sessions/{session_id}/documents", response_model=SessionDocumentsResponse)
def get_session_documents(session_id: str, user_id: str = Depends(current_user_id), db: Session = Depends(get_db)):
    get_owned_session(db, session_id, user_id)
    docs = DocumentUploadService.get_session_documents(db, session_id)
    return SessionDocumentsResponse(
        session_id=session_id,
        has_documents=bool(docs),
        documents=[DocumentUploadResponse.model_validate(doc) for doc in docs],
        total_count=len(docs),
    )


@router.get("/sessions/{session_id}/documents/summary", response_model=SessionDocumentSummary)
def get_session_document_summary(session_id: str, user_id: str = Depends(current_user_id),
                                 db: Session = Depends(get_db)):
    get_owned_session(db, session_id, user_id)
    docs = DocumentUploadService.get_session_documents(db, session_id)
    latest = docs[0] if docs else None
    return SessionDocumentSummary(
        session_id=session_id,
        has_documents=bool(docs),
        latest_document_name=latest.document_name if latest else None,
        latest_document_type=latest.document_type if latest else None,
        latest_upload_time=latest.upload_time if latest else None,
        total_documents=len(docs),
    )

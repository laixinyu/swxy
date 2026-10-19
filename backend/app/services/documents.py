"""知识库文档的增删查。数据库只走 ORM。"""
import logging
import uuid

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.config import settings
from engine.rag.utils.es_conn import ESConnection
from models import Document, DocumentStatus
from services import storage
from services.tasks import enqueue_ingest

logger = logging.getLogger(__name__)


def list_documents(db: Session, user_id: str) -> list[Document]:
    return (
        db.query(Document)
        .filter(Document.user_id == user_id)
        .order_by(Document.created_at.desc())
        .all()
    )


def owned_documents(db: Session, user_id: str, doc_ids: list[str]) -> list[Document]:
    """只返回属于该用户的文档，别人的文档 ID 会被忽略。"""
    if not doc_ids:
        return []
    return db.query(Document).filter(Document.user_id == user_id, Document.id.in_(doc_ids)).all()


def reparse_document(db: Session, user_id: str, doc_id: str) -> Document:
    doc = db.query(Document).filter(Document.user_id == user_id, Document.id == doc_id).first()
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    if doc.status in (DocumentStatus.PENDING, DocumentStatus.PARSING):
        raise HTTPException(status_code=409, detail="文档正在解析中")
    doc.status = DocumentStatus.PENDING
    doc.progress = 0
    doc.progress_msg = None
    doc.error = None
    db.commit()
    enqueue_ingest(doc.id)
    db.refresh(doc)
    return doc


def create_document(db: Session, user_id: str, file_name: str, content: bytes) -> Document:
    """保存文件、写入记录、加入入库队列。解析在后台进行，调用方立即返回。"""
    ext = storage.file_extension(file_name)
    if ext not in settings.allowed_extension_set:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型 .{ext}")
    storage.validate_content(ext, content)

    if db.query(Document.id).filter(Document.user_id == user_id, Document.file_name == file_name).first():
        raise HTTPException(status_code=409, detail=f"文件已存在，请勿重复上传: {file_name}")

    doc_id = uuid.uuid4().hex
    relative = storage.document_path(user_id, doc_id, ext)
    storage.save_file(relative, content)
    doc = Document(
        id=doc_id,
        user_id=user_id,
        kb_id=user_id,
        file_name=file_name,
        file_type=ext,
        file_size=len(content),
        storage_path=relative,
        status=DocumentStatus.PENDING,
    )
    db.add(doc)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        storage.delete_file(relative)
        raise HTTPException(status_code=409, detail=f"文件已存在，请勿重复上传: {file_name}")

    try:
        enqueue_ingest(doc_id)
    except Exception:
        logger.exception("文档 %s 加入入库队列失败", doc_id)
        doc.status = DocumentStatus.FAILED
        doc.error = "加入解析队列失败，请删除后重新上传"
        db.commit()
    db.refresh(doc)
    return doc


def delete_document(db: Session, user_id: str, file_name: str) -> int:
    doc = (
        db.query(Document)
        .filter(Document.user_id == user_id, Document.file_name == file_name)
        .first()
    )
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 先删切片再删记录：中途失败时记录还在，可以重试删除
    deleted = ESConnection().delete_by_doc(settings.es_index, doc.id)
    storage.delete_file(doc.storage_path)
    db.delete(doc)
    db.commit()
    logger.info("用户 %s 删除文档 %s，清理 %d 个切片", user_id, file_name, deleted)
    return deleted

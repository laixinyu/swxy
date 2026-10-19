"""会话临时文档：小文件直接取文字存 Redis，问答时整篇放进提示词，不进知识库。"""
import logging
from io import BytesIO
from typing import Tuple

import pdfplumber
import redis
from docx import Document as DocxDocument
from fastapi import HTTPException

from core.config import settings

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS = ("docx", "pdf", "txt")
_redis = redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _key(session_id: str) -> str:
    return f"quick_parse:{session_id}"


def get_content(session_id: str) -> str | None:
    try:
        return _redis.get(_key(session_id))
    except redis.RedisError:
        logger.exception("读取会话 %s 的临时文档失败", session_id)
        return None


def _parse_docx(content: bytes) -> Tuple[str, int]:
    try:
        doc = DocxDocument(BytesIO(content))
    except Exception:
        raise HTTPException(status_code=400, detail="DOCX 文件解析失败")
    text = "\n".join(p.text.strip() for p in doc.paragraphs if p.text.strip())
    if len(text) > settings.quick_parse_max_chars:
        raise HTTPException(status_code=400,
                            detail=f"DOCX 文档字符数({len(text)})超过限制({settings.quick_parse_max_chars}字符)")
    return text, len(text)


def _parse_pdf(content: bytes) -> Tuple[str, int]:
    try:
        with pdfplumber.open(BytesIO(content)) as pdf:
            pages = len(pdf.pages)
            if pages > settings.quick_parse_max_pages:
                raise HTTPException(status_code=400,
                                    detail=f"PDF 文档页数({pages})不能超过 {settings.quick_parse_max_pages} 页")
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="PDF 文件解析失败")
    return text, pages


def _parse_txt(content: bytes) -> Tuple[str, int]:
    for encoding in ("utf-8", "gbk", "gb2312"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise HTTPException(status_code=400, detail="无法识别文本文件编码")
    if len(text) > settings.quick_parse_max_chars:
        raise HTTPException(status_code=400,
                            detail=f"TXT 文档字符数({len(text)})超过限制({settings.quick_parse_max_chars}字符)")
    return text, len(text)


_PARSERS = {"docx": _parse_docx, "pdf": _parse_pdf, "txt": _parse_txt}


def quick_parse_document(session_id: str, ext: str, content: bytes) -> dict:
    if ext not in SUPPORTED_FORMATS:
        raise HTTPException(status_code=400, detail=f"不支持的文件格式，仅支持 {', '.join(SUPPORTED_FORMATS)}")
    if _redis.exists(_key(session_id)):
        raise HTTPException(status_code=400, detail="该会话已有文档，每个会话只能上传一个文档")

    text, count = _PARSERS[ext](content)
    if not text.strip():
        raise HTTPException(status_code=400, detail="没有从文档中解析出文字")
    _redis.setex(_key(session_id), settings.quick_parse_ttl_seconds, text)

    result = {
        "status": "success",
        "message": "文档解析完成",
        "session_id": session_id,
        "file_type": ext,
        "content_length": len(text),
        "expiry_hours": settings.quick_parse_ttl_seconds // 3600,
    }
    if ext == "pdf":
        result.update(pages=count, limit_info=f"PDF页数限制: {settings.quick_parse_max_pages}页")
    else:
        result.update(character_count=count, limit_info=f"字符数限制: {settings.quick_parse_max_chars}字符")
    return result


def get_parsed_content(session_id: str) -> dict:
    content = get_content(session_id)
    if not content:
        raise HTTPException(status_code=404, detail="未找到该会话的文档内容，可能已过期或未上传")
    ttl = _redis.ttl(_key(session_id))
    return {
        "status": "success",
        "session_id": session_id,
        "content": content,
        "content_length": len(content),
        "remaining_seconds": max(ttl, 0),
    }

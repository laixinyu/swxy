"""原始文件存储。磁盘路径只由文档 ID 决定，用户提供的文件名只用于展示，不参与拼路径。"""
import os
import re
import unicodedata
from pathlib import Path

from fastapi import HTTPException, UploadFile

from core.config import settings

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

# 文件头校验：扩展名可以随便改，文件头不能
_MAGIC = {
    "pdf": (b"%PDF",),
    "docx": (b"PK",),
    "xlsx": (b"PK",),
    "xls": (b"\xd0\xcf\x11\xe0", b"\x09\x08"),
    "doc": (b"\xd0\xcf\x11\xe0",),
}


def safe_filename(raw: str | None) -> str:
    """去掉路径部分和控制字符，保留中文等正常字符。"""
    name = unicodedata.normalize("NFC", raw or "")
    name = name.replace("\\", "/").split("/")[-1]
    name = _CONTROL_CHARS.sub("", name).strip().lstrip(".")
    if not name or len(name) > 200:
        raise HTTPException(status_code=400, detail="文件名无效")
    return name


def file_extension(name: str) -> str:
    return os.path.splitext(name)[1].lower().lstrip(".")


def read_upload(file: UploadFile, max_bytes: int) -> bytes:
    """分块读取，超过上限立即中止，避免把超大文件整个读进内存。"""
    chunks, total = [], 0
    while True:
        chunk = file.file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail=f"文件大小不能超过 {max_bytes // (1024 * 1024)}MB")
        chunks.append(chunk)
    return b"".join(chunks)


def validate_content(ext: str, content: bytes) -> None:
    if not content:
        raise HTTPException(status_code=400, detail="文件内容为空")
    magics = _MAGIC.get(ext)
    if magics and not content.startswith(magics):
        raise HTTPException(status_code=400, detail=f"文件内容与扩展名 .{ext} 不符")


def document_path(user_id: str, doc_id: str, ext: str) -> str:
    """相对 storage_dir 的路径"""
    return f"{user_id}/{doc_id}.{ext}" if ext else f"{user_id}/{doc_id}"


def absolute_path(relative: str) -> Path:
    root = settings.storage_dir.resolve()
    path = (root / relative).resolve()
    if root not in path.parents:
        raise ValueError(f"非法存储路径: {relative}")
    return path


def save_file(relative: str, content: bytes) -> None:
    path = absolute_path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(content)
    tmp.replace(path)


def delete_file(relative: str) -> None:
    try:
        absolute_path(relative).unlink(missing_ok=True)
    except ValueError:
        pass

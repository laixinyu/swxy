"""文档入库：解析 → 切块 → 向量化 → 写入 ES。在 worker 进程中执行，可重复执行。"""
import datetime
import logging
import time

import xxhash

from core.config import settings
from db.database import session_scope
from engine.rag.app.naive import chunk
from engine.rag.nlp.model import generate_embedding
from engine.rag.utils.es_conn import ESConnection
from models import Document, DocumentStatus
from services.storage import absolute_path

logger = logging.getLogger(__name__)

ES_BATCH_SIZE = 100
# 切块阶段算好、需要原样写入 ES 的字段
CHUNK_FIELDS = (
    "content_with_weight", "content_ltks", "content_sm_ltks",
    "title_tks", "title_sm_tks", "page_num_int", "position_int", "top_int",
)


def _set_status(doc_id: str, status: str, **fields) -> bool:
    with session_scope() as db:
        doc = db.get(Document, doc_id)
        if doc is None:
            return False
        doc.status = status
        for key, value in fields.items():
            setattr(doc, key, value)
        return True


class ProgressReporter:
    """把解析进度写回数据库，前端轮询时能看到。限频，避免每页都写一次库。"""

    def __init__(self, doc_id: str, min_interval: float = 1.0):
        self.doc_id = doc_id
        self.min_interval = min_interval
        self._last = 0.0
        self._progress = 0.0
        self._msg = "开始解析"

    def update(self, progress: float | None = None, msg: str = "", force: bool = False) -> None:
        if progress is not None:
            self._progress = max(self._progress, min(progress, 0.99))
        if msg:
            self._msg = msg[:255]
        now = time.monotonic()
        if not force and now - self._last < self.min_interval:
            return
        self._last = now
        try:
            with session_scope() as db:
                doc = db.get(Document, self.doc_id)
                if doc is not None and doc.status == DocumentStatus.PARSING:
                    doc.progress = round(self._progress, 3)
                    doc.progress_msg = self._msg
        except Exception:
            logger.warning("更新文档 %s 进度失败", self.doc_id, exc_info=True)

    def parser_callback(self, prog=None, msg=""):
        """解析器自己报告 0～1 的进度，映射到总进度的 5%～70%。"""
        self.update(None if prog is None else 0.05 + float(prog) * 0.65, _PARSER_MESSAGES.get(msg[:6], ""))


# 解析器回调里的英文提示换成中文，前端直接显示
_PARSER_MESSAGES = {
    "OCR st": "识别文字",
    "OCR fi": "识别文字完成",
    "Layout": "分析版面",
    "Table ": "识别表格",
    "Text m": "合并段落",
    "Start ": "读取文档",
    "Finish": "读取完成",
}


def build_es_documents(doc: Document, items: list[dict], embeddings: list[list[float]]) -> list[dict]:
    now = datetime.datetime.now()
    results = []
    for i, (item, embedding) in enumerate(zip(items, embeddings)):
        d = {field: item[field] for field in CHUNK_FIELDS if item.get(field) not in (None, "", [])}
        d.update({
            "id": xxhash.xxh64(f"{doc.id}:{i}:{item['content_with_weight']}".encode("utf-8")).hexdigest(),
            "doc_id": doc.id,
            "kb_id": doc.kb_id,
            "docnm_kwd": doc.file_name,
            "docnm": doc.file_name,
            "available_int": 1,
            "create_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "create_timestamp_flt": now.timestamp(),
            f"q_{len(embedding)}_vec": embedding,
        })
        results.append(d)
    return results


def ingest_document(doc_id: str) -> int:
    with session_scope() as db:
        doc = db.get(Document, doc_id)
        if doc is None:
            logger.info("文档 %s 已删除，跳过入库", doc_id)
            return 0
        db.expunge(doc)
    # 重新解析时，旧切片要等新结果写入时才替换。失败了就保留旧结果，不能让一个能用的文档变空
    previous_chunks = doc.chunk_count
    replaced = False

    _set_status(doc_id, DocumentStatus.PARSING, error=None, progress=0.02, progress_msg="开始解析")
    progress = ProgressReporter(doc_id)
    es = ESConnection()
    index = settings.es_index
    try:
        binary = absolute_path(doc.storage_path).read_bytes()
        # 解析器把空内容当作“没传内容”，会改去按文件名读磁盘，报出看不懂的错误
        if not binary:
            raise ValueError("原始文件为空或已损坏，请删除后重新上传")
        items = chunk(doc.file_name, binary=binary,
                      parser_config={"chunk_token_num": settings.chunk_token_num,
                                     "delimiter": "\n!?。；！？", "layout_recognize": "DeepDOC"},
                      callback=progress.parser_callback)
        items = [item for item in items if item.get("content_with_weight", "").strip()]
        if not items:
            raise ValueError("没有从文档中解析出任何文字")

        progress.update(0.72, f"生成向量（{len(items)} 个切片）", force=True)
        embeddings = generate_embedding([item["content_with_weight"] for item in items])
        if len(embeddings) != len(items):
            raise RuntimeError(f"向量数量 {len(embeddings)} 与切块数量 {len(items)} 不一致")

        progress.update(0.9, "写入索引", force=True)
        es_docs = build_es_documents(doc, items, embeddings)
        replaced = True
        es.delete_by_doc(index, doc.id)
        for i in range(0, len(es_docs), ES_BATCH_SIZE):
            errors = es.insert(documents=es_docs[i:i + ES_BATCH_SIZE], indexName=index)
            if errors:
                raise RuntimeError(f"写入 ES 失败: {errors[:3]}")
        es.refresh(index)

        if not _set_status(doc_id, DocumentStatus.DONE, chunk_count=len(es_docs), error=None,
                           progress=1, progress_msg="已完成"):
            # 入库期间文档被删了，撤销刚写入的切片
            es.delete_by_doc(index, doc_id)
            return 0
        logger.info("文档 %s（%s）入库完成，%d 个切片", doc.file_name, doc_id, len(es_docs))
        return len(es_docs)
    except Exception as e:
        logger.exception("文档 %s（%s）入库失败", doc.file_name, doc_id)
        if not replaced and previous_chunks > 0:
            # 还没动旧切片，旧结果仍然完整可用
            _set_status(doc_id, DocumentStatus.DONE, chunk_count=previous_chunks, progress=1,
                        error=f"重新解析失败，仍在使用上次的解析结果：{str(e)[:400]}",
                        progress_msg="重新解析失败")
            raise
        try:
            es.delete_by_doc(index, doc_id)
        except Exception:
            logger.exception("清理文档 %s 的残留切片失败", doc_id)
        _set_status(doc_id, DocumentStatus.FAILED, chunk_count=0, error=str(e)[:500], progress_msg="解析失败")
        raise

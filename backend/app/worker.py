"""入库 worker：python worker.py"""
import datetime
import logging

from rq import SimpleWorker
from rq.job import Job

from common import setup_logging
from core.config import settings
from db.database import session_scope
from engine.rag.utils.es_conn import ESConnection
from models import Document, DocumentStatus
from services.tasks import enqueue_ingest, ingest_queue, redis_connection

logger = logging.getLogger("worker")


def recover_interrupted() -> None:
    """worker 崩溃或重启时，正在处理的文档会停在 parsing；还没排上队的停在 pending。重新入队。"""
    stale_before = datetime.datetime.now() - datetime.timedelta(seconds=settings.ingest_job_timeout)
    with session_scope() as db:
        docs = (
            db.query(Document)
            .filter(
                (Document.status == DocumentStatus.PENDING)
                | ((Document.status == DocumentStatus.PARSING) & (Document.updated_at < stale_before))
            )
            .all()
        )
        pending = [(doc.id, doc.storage_path) for doc in docs]
    for doc_id, storage_path in pending:
        if storage_path.startswith("legacy:"):
            continue
        if Job.exists(f"ingest-{doc_id}", connection=redis_connection()):
            job = Job.fetch(f"ingest-{doc_id}", connection=redis_connection())
            if job.get_status(refresh=True) in ("queued", "started", "scheduled", "deferred"):
                continue
        logger.info("重新加入入库队列: %s", doc_id)
        enqueue_ingest(doc_id)


def main() -> None:
    setup_logging()
    ESConnection().ensure_index(settings.es_index)
    # 先加载分词词典等重资源，避免第一个任务慢
    import engine.rag.app.naive  # noqa: F401

    try:
        recover_interrupted()
    except Exception:
        # 例如和 API 同时重启、迁移还没执行完。不影响处理新任务，下次启动会再检查
        logger.exception("恢复中断的入库任务失败")
    worker = SimpleWorker([ingest_queue()], connection=redis_connection())
    worker.work(with_scheduler=True, logging_level=settings.log_level.upper())


if __name__ == "__main__":
    main()

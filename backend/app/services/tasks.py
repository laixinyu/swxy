"""入库任务队列（Redis + RQ）。API 只负责入队，解析和向量化在 worker 进程里执行。"""
from functools import lru_cache

from redis import Redis
from rq import Queue, Retry

from core.config import settings


@lru_cache
def redis_connection() -> Redis:
    return Redis.from_url(settings.redis_url)


@lru_cache
def ingest_queue() -> Queue:
    return Queue(settings.ingest_queue, connection=redis_connection())


def enqueue_ingest(doc_id: str):
    return ingest_queue().enqueue(
        "services.ingestion.ingest_document",
        doc_id,
        job_id=f"ingest-{doc_id}",
        job_timeout=settings.ingest_job_timeout,
        retry=Retry(max=2, interval=[10, 60]),
        failure_ttl=7 * 24 * 3600,
    )

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from db.database import engine
from engine.rag.utils.es_conn import ESConnection
from services.tasks import redis_connection

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/healthz", include_in_schema=False)
def healthz():
    """存活检查：进程能响应即可。"""
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
def readyz():
    """就绪检查：依赖的 PostgreSQL、Elasticsearch、Redis 都可用才返回 200。"""
    checks = {}
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as e:
        checks["postgres"] = f"error: {type(e).__name__}"
    try:
        checks["elasticsearch"] = "ok" if ESConnection().ping() else "error: ping failed"
    except Exception as e:
        checks["elasticsearch"] = f"error: {type(e).__name__}"
    try:
        redis_connection().ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {type(e).__name__}"

    ready = all(value == "ok" for value in checks.values())
    if not ready:
        logger.warning("就绪检查未通过: %s", checks)
    return JSONResponse(status_code=200 if ready else 503, content={"status": "ok" if ready else "unavailable",
                                                                     "checks": checks})

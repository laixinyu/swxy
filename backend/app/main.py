import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from common import setup_logging
from core.config import settings
from core.exceptions import register_exception_handlers

setup_logging()

from api import asr, chat, health, history, user  # noqa: E402
from engine.rag.utils.es_conn import ESConnection  # noqa: E402

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        ESConnection().ensure_index(settings.es_index)
    except Exception:
        # ES 暂时不可用时也允许启动，/readyz 会报告状态，写入时会再次建索引
        logger.exception("初始化 ES 索引失败")
    yield


app = FastAPI(root_path=settings.root_path, lifespan=lifespan)

if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

register_exception_handlers(app)

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(user.router)
app.include_router(history.router)
app.include_router(asr.router)

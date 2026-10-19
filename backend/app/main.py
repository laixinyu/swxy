import logging
from contextlib import asynccontextmanager

from authlib.jose.errors import JoseError
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from common import setup_logging
from core.config import settings

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


# fastapi-jwt 0.3.0 只把过期、格式错误转成 401，签名不对时 Authlib 抛出的 BadSignatureError 会变成 500。
@app.exception_handler(JoseError)
async def invalid_token_handler(request: Request, exc: JoseError):
    return JSONResponse(status_code=401, content={"detail": "Invalid token"})


_FIELD_NAMES = {"username": "用户名", "password": "密码", "message": "问题", "session_id": "会话 ID"}


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """把参数校验错误转成一句话，前端可以直接弹出。"""
    messages = []
    for error in exc.errors():
        field = _FIELD_NAMES.get(str(error["loc"][-1]), str(error["loc"][-1]))
        ctx = error.get("ctx") or {}
        if error["type"] == "string_too_short":
            messages.append(f"{field}至少 {ctx.get('min_length')} 个字符")
        elif error["type"] == "string_too_long":
            messages.append(f"{field}最多 {ctx.get('max_length')} 个字符")
        elif error["type"] == "string_pattern_mismatch":
            messages.append(f"{field}只能包含字母、数字、下划线、短横线或中文")
        elif error["type"] == "missing":
            messages.append(f"缺少{field}")
        else:
            messages.append(f"{field}格式错误")
    return JSONResponse(status_code=422, content={"detail": "；".join(messages)})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # 异常细节只写日志，不返回给客户端
    logger.exception("未处理的异常: %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})


app.include_router(health.router)
app.include_router(chat.router)
app.include_router(user.router)
app.include_router(history.router)
app.include_router(asr.router)

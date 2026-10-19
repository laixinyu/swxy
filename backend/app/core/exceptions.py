"""全局异常处理：统一转成 {"detail": "..."} 的 JSON 响应。"""
import logging

from authlib.jose.errors import JoseError
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

_FIELD_NAMES = {"username": "用户名", "password": "密码", "message": "问题", "session_id": "会话 ID"}


# fastapi-jwt 0.3.0 只把过期、格式错误转成 401，签名不对时 Authlib 抛出的 BadSignatureError 会变成 500。
async def invalid_token_handler(request: Request, exc: JoseError):
    return JSONResponse(status_code=401, content={"detail": "Invalid token"})


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


async def unhandled_exception_handler(request: Request, exc: Exception):
    # 异常细节只写日志，不返回给客户端
    logger.exception("未处理的异常: %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(JoseError, invalid_token_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from authlib.jose.errors import JoseError
from api import asr, chat, user, history
import os

# 从环境变量获取 root_path
root_path = os.getenv("ROOT_PATH", "http://localhost:8000")

app = FastAPI(root_path=root_path)

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有源，生产环境中应该设置具体的源
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有方法
    allow_headers=["*"],  # 允许所有头
)

# fastapi-jwt 0.3.0 只把过期、格式错误转成 401，签名不对时 Authlib 抛出的 BadSignatureError 会变成 500。
@app.exception_handler(JoseError)
async def invalid_token_handler(request: Request, exc: JoseError):
    return JSONResponse(status_code=401, content={"detail": "Invalid token"})

app.include_router(chat.router)
app.include_router(user.router)
app.include_router(history.router)
app.include_router(asr.router)

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

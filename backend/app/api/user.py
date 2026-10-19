import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.config import settings
from db.database import get_db
from exceptions.auth import AuthError
from services.auth import authenticate, register_user

logger = logging.getLogger(__name__)
router = APIRouter()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_\-\u4e00-\u9fff]+$")
    password: str = Field(min_length=8, max_length=128)


@router.post("/login")
def login(request: LoginRequest, db: Session = Depends(get_db)):
    try:
        token = authenticate(db, request.username, request.password)
    except AuthError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e),
                            headers={"WWW-Authenticate": "Bearer"})
    return {"access_token": token, "token_type": "bearer"}


@router.post("/register")
def register(request: RegisterRequest, db: Session = Depends(get_db)):
    try:
        register_user(db, request.username, request.password)
    except AuthError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return {"message": "User registered successfully"}


class STSTokenRequest(BaseModel):
    appid: str | None = None
    accessKey: str | None = None


@router.post("/sts-token")
async def get_sts_token(request: STSTokenRequest | None = None):
    """旧版火山引擎语音凭证，当前前端主流程不再使用。"""
    request = request or STSTokenRequest()
    appid = (request.appid or settings.volc_app_id).strip()
    access_key = (request.accessKey or settings.volc_access_key).strip()
    if not appid or not access_key:
        raise HTTPException(status_code=400, detail="未配置语音识别。请在后端环境变量中设置 VOLC_APP_ID 和 VOLC_ACCESS_KEY")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://openspeech.bytedance.com/api/v1/sts/token",
                headers={"Authorization": f"Bearer; {access_key}", "Content-Type": "application/json"},
                json={"appid": appid, "duration": 300},
            )
        return response.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=status.HTTP_408_REQUEST_TIMEOUT, detail="语音凭证接口超时")
    except httpx.HTTPError:
        logger.exception("调用火山引擎语音凭证接口失败")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="语音凭证接口不可用")

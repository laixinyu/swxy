import logging
import secrets
from datetime import timedelta

from fastapi_jwt import JwtAccessBearerCookie
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from common.password import hash_password, verify_password
from core.config import settings
from exceptions.auth import AuthError
from models import User

logger = logging.getLogger(__name__)

# 从请求头或 cookie 中读取访问令牌（优先请求头）。密钥长度由 Settings 校验。
access_security = JwtAccessBearerCookie(
    secret_key=settings.jwt_secret_key,
    auto_error=True,
    access_expires_delta=timedelta(days=settings.jwt_expire_days),
)


_DUMMY_HASH = hash_password(secrets.token_hex(16))


def create_token(user_id: int, user_name: str) -> str:
    subject = {"user_id": user_id, "user_name": user_name, "salting": secrets.token_hex(16)}
    return access_security.create_access_token(subject=subject)


def authenticate(db: Session, username: str, password: str) -> str:
    user = db.query(User).filter(User.username == username).first()
    # 用户不存在时也做一次完整的哈希校验，避免通过响应时间判断用户名是否存在
    try:
        valid = verify_password(password, user.password_hash if user else _DUMMY_HASH)
    except ValueError:
        valid = False
    if not user or not valid:
        raise AuthError("用户名或密码错误")
    return create_token(user.id, user.username)


def register_user(db: Session, username: str, password: str) -> None:
    if db.query(User.id).filter(User.username == username).first():
        raise AuthError("用户名已存在")
    db.add(User(username=username, password_hash=hash_password(password)))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AuthError("用户名已存在")
    logger.info("用户 %s 注册成功", username)

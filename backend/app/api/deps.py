from fastapi import HTTPException, Security
from fastapi_jwt import JwtAuthorizationCredentials

from services.auth import access_security


def current_user_id(credentials: JwtAuthorizationCredentials = Security(access_security)) -> str:
    user_id = credentials.subject.get("user_id")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")
    return str(user_id)

from pydantic import BaseModel, UUID4
from typing import Optional
from datetime import datetime
from typing import List, Union


class MessageResponse(BaseModel):
    message_id: UUID4
    session_id: str
    user_question: str
    model_answer: str
    created_at: datetime
    documents: Optional[Union[list, dict]] = None
    recommended_questions: Optional[Union[list, dict]] = None
    think: Optional[str] 

    model_config = {"from_attributes": True}

# 定义返回的文档模型
class FilestResponse(BaseModel):
    id: str
    user_id: str
    file_name: str
    file_size: int = 0
    # pending / parsing / done / failed
    status: str
    chunk_count: int = 0
    # 0～1
    progress: float = 0
    progress_msg: Optional[str] = None
    error: Optional[str] = None
    created_at: str
    updated_at: str

# 单个会话的响应模型
class SessionResponse(BaseModel):
    session_id: str
    session_name: str
    user_id: str
    created_at: str
    updated_at: str

# 会话列表的响应模型
class SessionListResponse(BaseModel):
    user_id: str
    sessions: List[SessionResponse]
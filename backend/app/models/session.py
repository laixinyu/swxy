from sqlalchemy import TIMESTAMP, Column, Index, String, Text
from sqlalchemy.sql import func

from models.base import Base

DEFAULT_SESSION_NAME = "新对话"


class ChatSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        Index("idx_sessions_user_id", "user_id"),
        Index("idx_sessions_created_at", "created_at"),
    )

    session_id = Column(String(16), primary_key=True)
    session_name = Column(String(255), nullable=False, default=DEFAULT_SESSION_NAME)
    user_id = Column(String(255), nullable=False)
    # 检索范围：文档 ID 的 JSON 数组。为空表示检索全部文档
    doc_ids = Column(Text)
    created_at = Column(TIMESTAMP, nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP, nullable=False, server_default=func.now(), onupdate=func.now())

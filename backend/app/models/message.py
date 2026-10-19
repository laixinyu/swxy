from sqlalchemy import TIMESTAMP, Column, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from models.base import Base


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("idx_messages_session_id", "session_id"),
        Index("idx_messages_created_at", "created_at"),
    )

    message_id = Column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    session_id = Column(String(16), nullable=False)
    user_question = Column(Text, nullable=False)
    model_answer = Column(Text, nullable=False)
    # 结合上下文改写后、实际拿去检索的问题。第一轮或无需改写时与 user_question 相同
    retrieval_query = Column(Text)
    # JSON 字符串，前端按 JSON 解析
    documents = Column(Text)
    recommended_questions = Column(Text)
    think = Column(Text)
    created_at = Column(TIMESTAMP, nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP, nullable=False, server_default=func.now(), onupdate=func.now())

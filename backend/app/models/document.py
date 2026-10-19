from sqlalchemy import TIMESTAMP, Column, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from models.base import Base


class DocumentStatus:
    PENDING = "pending"
    PARSING = "parsing"
    DONE = "done"
    FAILED = "failed"


class Document(Base):
    """知识库文档。原始文件在 settings.storage_dir / storage_path，切片在 ES 中以 doc_id 关联。"""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("user_id", "file_name", name="uq_documents_user_file"),
        Index("idx_documents_user_id", "user_id"),
        Index("idx_documents_status", "status"),
    )

    id = Column(String(32), primary_key=True)
    user_id = Column(String(255), nullable=False)
    kb_id = Column(String(255), nullable=False)
    file_name = Column(String(255), nullable=False)
    file_type = Column(String(20), nullable=False)
    file_size = Column(Integer, nullable=False, default=0)
    storage_path = Column(String(1024), nullable=False)
    status = Column(String(20), nullable=False, default=DocumentStatus.PENDING)
    chunk_count = Column(Integer, nullable=False, default=0)
    # 0～1，解析中时由 worker 定期更新
    progress = Column(Float, nullable=False, default=0)
    progress_msg = Column(String(255))
    error = Column(Text)
    created_at = Column(TIMESTAMP, nullable=False, server_default=func.now())
    updated_at = Column(TIMESTAMP, nullable=False, server_default=func.now(), onupdate=func.now())

"""Replace knowledgebases with documents

Revision ID: b7d2e1f0a9c3
Revises: 43c6db52e840
Create Date: 2026-10-19 10:00:00.000000

knowledgebases 只记了文件名。documents 增加文档 ID、存储路径、解析状态、切片数和错误信息。
旧记录迁移为 pending 状态，storage_path 先记为旧目录下的相对路径，
由 `python manage.py migrate-legacy-files` 把文件搬到新存储目录并重新入库。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7d2e1f0a9c3'
down_revision: Union[str, None] = '43c6db52e840'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY_PREFIX = 'legacy:'


def upgrade() -> None:
    op.create_table(
        'documents',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('user_id', sa.String(255), nullable=False),
        sa.Column('kb_id', sa.String(255), nullable=False),
        sa.Column('file_name', sa.String(255), nullable=False),
        sa.Column('file_type', sa.String(20), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('storage_path', sa.String(1024), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('chunk_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error', sa.Text()),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.UniqueConstraint('user_id', 'file_name', name='uq_documents_user_file'),
    )
    op.create_index('idx_documents_user_id', 'documents', ['user_id'])
    op.create_index('idx_documents_status', 'documents', ['status'])

    op.execute(f"""
        INSERT INTO documents (id, user_id, kb_id, file_name, file_type, file_size, storage_path,
                               status, created_at, updated_at)
        SELECT DISTINCT ON (user_id, file_name)
               replace(gen_random_uuid()::text, '-', ''),
               user_id,
               user_id,
               file_name,
               lower(regexp_replace(file_name, '^.*\\.', '')),
               0,
               '{LEGACY_PREFIX}' || user_id || '/' || file_name,
               'pending',
               created_at,
               updated_at
        FROM knowledgebases
        ORDER BY user_id, file_name, id
    """)

    op.drop_table('knowledgebases')


def downgrade() -> None:
    op.create_table(
        'knowledgebases',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.String(255), nullable=False),
        sa.Column('file_name', sa.String(255), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    )
    op.execute("""
        INSERT INTO knowledgebases (user_id, file_name, created_at, updated_at)
        SELECT user_id, file_name, created_at, updated_at FROM documents
    """)
    op.drop_table('documents')

"""Initial baseline migration

Revision ID: 980b32f130df
Revises:
Create Date: 2025-06-19 22:32:28.433643

最初的表由 init.sql 创建，这个版本只做了 stamp。现在由它负责建表，
全新部署不再依赖 init.sql；已经 stamp 过的旧库不会重复执行。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '980b32f130df'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps():
    return [
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('username', sa.String(50), nullable=False, unique=True),
        sa.Column('password_hash', sa.String(100), nullable=False),
        *_timestamps(),
    )

    op.create_table(
        'sessions',
        sa.Column('session_id', sa.String(16), primary_key=True),
        sa.Column('session_name', sa.String(255), nullable=False),
        sa.Column('user_id', sa.String(255), nullable=False),
        *_timestamps(),
    )
    op.create_index('idx_sessions_user_id', 'sessions', ['user_id'])
    op.create_index('idx_sessions_created_at', 'sessions', ['created_at'])

    op.create_table(
        'messages',
        sa.Column('message_id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('session_id', sa.String(16), nullable=False),
        sa.Column('user_question', sa.Text(), nullable=False),
        sa.Column('model_answer', sa.Text(), nullable=False),
        sa.Column('documents', sa.Text()),
        sa.Column('recommended_questions', sa.Text()),
        sa.Column('think', sa.Text()),
        *_timestamps(),
    )
    op.create_index('idx_messages_session_id', 'messages', ['session_id'])
    op.create_index('idx_messages_created_at', 'messages', ['created_at'])

    op.create_table(
        'knowledgebases',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.String(255), nullable=False),
        sa.Column('file_name', sa.String(255), nullable=False),
        *_timestamps(),
    )
    op.create_index('idx_knowledgebases_user_id', 'knowledgebases', ['user_id'])
    op.create_index('idx_knowledgebases_created_at', 'knowledgebases', ['created_at'])


def downgrade() -> None:
    op.drop_table('knowledgebases')
    op.drop_table('messages')
    op.drop_table('sessions')
    op.drop_table('users')

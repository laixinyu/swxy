"""Add document progress, session retrieval scope, rewritten retrieval query

Revision ID: c4e8a2b6d1f7
Revises: b7d2e1f0a9c3
Create Date: 2026-10-19 11:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c4e8a2b6d1f7'
down_revision: Union[str, None] = 'b7d2e1f0a9c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('documents', sa.Column('progress', sa.Float(), nullable=False, server_default='0'))
    op.add_column('documents', sa.Column('progress_msg', sa.String(255)))
    op.execute("UPDATE documents SET progress = 1 WHERE status = 'done'")
    op.add_column('sessions', sa.Column('doc_ids', sa.Text()))
    op.add_column('messages', sa.Column('retrieval_query', sa.Text()))


def downgrade() -> None:
    op.drop_column('messages', 'retrieval_query')
    op.drop_column('sessions', 'doc_ids')
    op.drop_column('documents', 'progress_msg')
    op.drop_column('documents', 'progress')

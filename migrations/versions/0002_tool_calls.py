"""tool calls

Revision ID: 0002_tool_calls
Revises: 0001_initial
Create Date: 2026-08-14 17:37:21.897075
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op



revision: str = '0002_tool_calls'
down_revision: str | None = '0001_initial'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    
    op.create_table('tool_calls',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('step_index', sa.Integer(), nullable=False),
    sa.Column('call_index', sa.Integer(), nullable=False),
    sa.Column('agent_id', sa.String(length=36), nullable=True),
    sa.Column('agent_name', sa.String(length=200), nullable=False),
    sa.Column('tool', sa.String(length=80), nullable=False),
    sa.Column('arguments', sa.JSON(), nullable=False),
    sa.Column('result', sa.Text(), nullable=False),
    sa.Column('is_error', sa.Boolean(), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=False),
    sa.Column('meta', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'step_index', 'call_index', name='uq_tool_call_index')
    )
    with op.batch_alter_table('tool_calls', schema=None) as batch_op:
        batch_op.create_index('ix_tool_calls_run', ['run_id'], unique=False)

    # server_default is required, not cosmetic: adding a NOT NULL column to a
    # table that already has rows fails without one. It is dropped again after
    # the backfill so the application layer stays the single source of the
    # default for new rows.
    with op.batch_alter_table('run_steps', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('model_calls', sa.Integer(), nullable=False, server_default='1')
        )
        batch_op.add_column(
            sa.Column('tool_calls', sa.Integer(), nullable=False, server_default='0')
        )

    with op.batch_alter_table('run_steps', schema=None) as batch_op:
        batch_op.alter_column('model_calls', server_default=None)
        batch_op.alter_column('tool_calls', server_default=None)

    


def downgrade() -> None:
    
    with op.batch_alter_table('run_steps', schema=None) as batch_op:
        batch_op.drop_column('tool_calls')
        batch_op.drop_column('model_calls')

    with op.batch_alter_table('tool_calls', schema=None) as batch_op:
        batch_op.drop_index('ix_tool_calls_run')

    op.drop_table('tool_calls')
    

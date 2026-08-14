"""initial schema

Revision ID: 0001_initial
Revises: 
Create Date: 2026-08-14 16:36:07.733470
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op



revision: str = '0001_initial'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    
    op.create_table('audit_log',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=True),
    sa.Column('actor_user_id', sa.String(length=36), nullable=True),
    sa.Column('action', sa.String(length=80), nullable=False),
    sa.Column('target_type', sa.String(length=60), nullable=True),
    sa.Column('target_id', sa.String(length=64), nullable=True),
    sa.Column('ip_address', sa.String(length=64), nullable=True),
    sa.Column('user_agent', sa.String(length=400), nullable=True),
    sa.Column('detail', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('audit_log', schema=None) as batch_op:
        batch_op.create_index('ix_audit_org_created', ['org_id', 'created_at'], unique=False)

    op.create_table('model_prices',
    sa.Column('model', sa.String(length=80), nullable=False),
    sa.Column('input_per_mtok', sa.Float(), nullable=False),
    sa.Column('output_per_mtok', sa.Float(), nullable=False),
    sa.Column('cached_input_per_mtok', sa.Float(), nullable=True),
    sa.Column('note', sa.String(length=300), nullable=False),
    sa.Column('updated_by', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('model')
    )
    op.create_table('orgs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('slug', sa.String(length=80), nullable=False),
    sa.Column('key_mode', sa.String(length=16), nullable=False),
    sa.Column('credits_cents', sa.BigInteger(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("key_mode in ('managed','byok','hybrid')"),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('users',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('is_superuser', sa.Boolean(), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('token_epoch', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)

    op.create_table('agents',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('role', sa.String(length=100), nullable=False),
    sa.Column('system_prompt', sa.Text(), nullable=False),
    sa.Column('model', sa.String(length=80), nullable=False),
    sa.Column('effort', sa.String(length=16), nullable=False),
    sa.Column('max_tokens', sa.Integer(), nullable=False),
    sa.Column('tools', sa.JSON(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("effort in ('low','medium','high','xhigh','max')"),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('agents', schema=None) as batch_op:
        batch_op.create_index('ix_agents_org_active', ['org_id', 'is_active'], unique=False)

    op.create_table('api_keys',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('ciphertext', sa.Text(), nullable=False),
    sa.Column('mask', sa.String(length=64), nullable=False),
    sa.Column('fingerprint', sa.String(length=32), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("provider in ('anthropic','openai','xai','google','openrouter')"),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'provider', name='uq_api_key_org_provider')
    )
    with op.batch_alter_table('api_keys', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_api_keys_org_id'), ['org_id'], unique=False)

    op.create_table('credit_ledger',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('amount_cents', sa.BigInteger(), nullable=False),
    sa.Column('balance_after_cents', sa.BigInteger(), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=True),
    sa.Column('description', sa.String(length=400), nullable=False),
    sa.Column('idempotency_key', sa.String(length=128), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("kind in ('topup','debit','refund','bonus','adjustment')"),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'idempotency_key', name='uq_ledger_idempotency')
    )
    with op.batch_alter_table('credit_ledger', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_credit_ledger_run_id'), ['run_id'], unique=False)
        batch_op.create_index('ix_ledger_org_created', ['org_id', 'created_at'], unique=False)

    op.create_table('memberships',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("role in ('owner','admin','member','viewer')"),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'org_id', name='uq_membership_user_org')
    )
    with op.batch_alter_table('memberships', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_memberships_org_id'), ['org_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_memberships_user_id'), ['user_id'], unique=False)

    op.create_table('refresh_tokens',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('family_id', sa.String(length=36), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('user_agent', sa.String(length=400), nullable=True),
    sa.Column('ip_address', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('refresh_tokens', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_refresh_tokens_family_id'), ['family_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_refresh_tokens_token_hash'), ['token_hash'], unique=True)
        batch_op.create_index(batch_op.f('ix_refresh_tokens_user_id'), ['user_id'], unique=False)

    op.create_table('workflows',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('preset', sa.String(length=32), nullable=False),
    sa.Column('graph', sa.JSON(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('workflows', schema=None) as batch_op:
        batch_op.create_index('ix_workflows_org', ['org_id'], unique=False)

    op.create_table('runs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('workflow_id', sa.String(length=36), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('input', sa.Text(), nullable=False),
    sa.Column('output', sa.Text(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('key_mode', sa.String(length=16), nullable=False),
    sa.Column('cost_microcents', sa.BigInteger(), nullable=False),
    sa.Column('cost_cents', sa.Integer(), nullable=False),
    sa.Column('billable_microcents', sa.BigInteger(), nullable=False),
    sa.Column('tokens_in', sa.BigInteger(), nullable=False),
    sa.Column('tokens_out', sa.BigInteger(), nullable=False),
    sa.Column('steps_used', sa.Integer(), nullable=False),
    sa.Column('max_steps', sa.Integer(), nullable=False),
    sa.Column('max_cost_cents', sa.Integer(), nullable=False),
    sa.Column('cancel_requested', sa.Boolean(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=128), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status in ('queued','running','succeeded','failed','cancelled','budget_exceeded','timed_out')"),
    sa.ForeignKeyConstraint(['org_id'], ['orgs.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workflow_id'], ['workflows.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.create_index('ix_runs_org_created', ['org_id', 'created_at'], unique=False)
        batch_op.create_index('ix_runs_status', ['status'], unique=False)
        batch_op.create_index('uq_runs_idempotency', ['org_id', 'idempotency_key'], unique=True, postgresql_where=sa.text('idempotency_key IS NOT NULL'), sqlite_where=sa.text('idempotency_key IS NOT NULL'))

    op.create_table('artifacts',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('path', sa.String(length=400), nullable=False),
    sa.Column('kind', sa.String(length=40), nullable=False),
    sa.Column('language', sa.String(length=40), nullable=True),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('produced_by_step', sa.Integer(), nullable=True),
    sa.Column('produced_by_agent', sa.String(length=200), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'path', 'version', name='uq_artifact_version')
    )
    with op.batch_alter_table('artifacts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_artifacts_org_id'), ['org_id'], unique=False)
        batch_op.create_index('ix_artifacts_run_path', ['run_id', 'path'], unique=False)

    op.create_table('run_steps',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('run_id', sa.String(length=36), nullable=False),
    sa.Column('index', sa.Integer(), nullable=False),
    sa.Column('agent_id', sa.String(length=36), nullable=True),
    sa.Column('agent_name', sa.String(length=200), nullable=False),
    sa.Column('role', sa.String(length=100), nullable=False),
    sa.Column('model', sa.String(length=80), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('input', sa.Text(), nullable=False),
    sa.Column('output', sa.Text(), nullable=False),
    sa.Column('stop_reason', sa.String(length=48), nullable=True),
    sa.Column('tokens_in', sa.Integer(), nullable=False),
    sa.Column('tokens_out', sa.Integer(), nullable=False),
    sa.Column('cached_tokens', sa.Integer(), nullable=False),
    sa.Column('reasoning_tokens', sa.Integer(), nullable=False),
    sa.Column('cost_microcents', sa.BigInteger(), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'index', name='uq_run_step_index')
    )
    with op.batch_alter_table('run_steps', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_run_steps_run_id'), ['run_id'], unique=False)

    


def downgrade() -> None:
    
    with op.batch_alter_table('run_steps', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_run_steps_run_id'))

    op.drop_table('run_steps')
    with op.batch_alter_table('artifacts', schema=None) as batch_op:
        batch_op.drop_index('ix_artifacts_run_path')
        batch_op.drop_index(batch_op.f('ix_artifacts_org_id'))

    op.drop_table('artifacts')
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.drop_index('uq_runs_idempotency', postgresql_where=sa.text('idempotency_key IS NOT NULL'), sqlite_where=sa.text('idempotency_key IS NOT NULL'))
        batch_op.drop_index('ix_runs_status')
        batch_op.drop_index('ix_runs_org_created')

    op.drop_table('runs')
    with op.batch_alter_table('workflows', schema=None) as batch_op:
        batch_op.drop_index('ix_workflows_org')

    op.drop_table('workflows')
    with op.batch_alter_table('refresh_tokens', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_refresh_tokens_user_id'))
        batch_op.drop_index(batch_op.f('ix_refresh_tokens_token_hash'))
        batch_op.drop_index(batch_op.f('ix_refresh_tokens_family_id'))

    op.drop_table('refresh_tokens')
    with op.batch_alter_table('memberships', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_memberships_user_id'))
        batch_op.drop_index(batch_op.f('ix_memberships_org_id'))

    op.drop_table('memberships')
    with op.batch_alter_table('credit_ledger', schema=None) as batch_op:
        batch_op.drop_index('ix_ledger_org_created')
        batch_op.drop_index(batch_op.f('ix_credit_ledger_run_id'))

    op.drop_table('credit_ledger')
    with op.batch_alter_table('api_keys', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_api_keys_org_id'))

    op.drop_table('api_keys')
    with op.batch_alter_table('agents', schema=None) as batch_op:
        batch_op.drop_index('ix_agents_org_active')

    op.drop_table('agents')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_email'))

    op.drop_table('users')
    op.drop_table('orgs')
    op.drop_table('model_prices')
    with op.batch_alter_table('audit_log', schema=None) as batch_op:
        batch_op.drop_index('ix_audit_org_created')

    op.drop_table('audit_log')
    

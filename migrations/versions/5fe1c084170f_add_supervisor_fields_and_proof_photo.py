"""add_supervisor_fields_and_proof_photo

Revision ID: 5fe1c084170f
Revises: 9eaf43c758ab
Create Date: 2026-11-18 10:42:21.806367

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import app.core.database


# revision identifiers, used by Alembic.
revision: str = '5fe1c084170f'
down_revision: Union[str, None] = '9eaf43c758ab'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('jobs') as batch_op:
        batch_op.add_column(sa.Column('supervisor_name', sa.String(length=150), nullable=True))
        batch_op.add_column(sa.Column('supervisor_phone', sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column('supervisor_token', sa.String(length=64), nullable=True))
        batch_op.create_index(batch_op.f('ix_jobs_supervisor_token'), ['supervisor_token'], unique=True)

    with op.batch_alter_table('job_days') as batch_op:
        batch_op.add_column(sa.Column('proof_photo', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('job_days') as batch_op:
        batch_op.drop_column('proof_photo')

    with op.batch_alter_table('jobs') as batch_op:
        batch_op.drop_index(batch_op.f('ix_jobs_supervisor_token'))
        batch_op.drop_column('supervisor_token')
        batch_op.drop_column('supervisor_phone')
        batch_op.drop_column('supervisor_name')

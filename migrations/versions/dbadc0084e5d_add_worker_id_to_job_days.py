"""add worker_id to job_days

Revision ID: dbadc0084e5d
Revises: 7187472717d0
Create Date: 2026-11-17 14:01:56.751344

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import app.core.database


# revision identifiers, used by Alembic.
revision: str = 'dbadc0084e5d'
down_revision: Union[str, None] = '7187472717d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('job_days', schema=None) as batch_op:
        batch_op.add_column(sa.Column('worker_id', app.core.database.GUID(length=36), nullable=True))
        batch_op.create_index(batch_op.f('ix_job_days_worker_id'), ['worker_id'], unique=False)
        batch_op.create_foreign_key('fk_job_days_worker_id_users', 'users', ['worker_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('job_days', schema=None) as batch_op:
        batch_op.drop_constraint('fk_job_days_worker_id_users', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_job_days_worker_id'))
        batch_op.drop_column('worker_id')

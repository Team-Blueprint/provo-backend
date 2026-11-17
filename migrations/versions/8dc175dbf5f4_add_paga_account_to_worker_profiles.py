"""add_paga_account_to_worker_profiles

Revision ID: 8dc175dbf5f4
Revises: dbadc0084e5d
Create Date: 2026-11-17 17:26:30.839011

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import app.core.database


# revision identifiers, used by Alembic.
revision: str = '8dc175dbf5f4'
down_revision: Union[str, None] = 'dbadc0084e5d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add paga account columns to worker_profiles using SQLite-safe batch mode
    with op.batch_alter_table('worker_profiles') as batch_op:
        batch_op.add_column(sa.Column('paga_account_number', sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column('paga_payment_url', sa.String(length=512), nullable=True))
        batch_op.create_index('ix_worker_profiles_paga_account_number', ['paga_account_number'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('worker_profiles') as batch_op:
        batch_op.drop_index('ix_worker_profiles_paga_account_number')
        batch_op.drop_column('paga_payment_url')
        batch_op.drop_column('paga_account_number')

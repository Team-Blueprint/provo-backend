"""add_pin_hash_to_users

Revision ID: 9eaf43c758ab
Revises: 8dc175dbf5f4
Create Date: 2026-11-17 23:54:25.108563

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import app.core.database


# revision identifiers, used by Alembic.
revision: str = '9eaf43c758ab'
down_revision: Union[str, None] = '8dc175dbf5f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('pin_hash', sa.String(length=128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('pin_hash')

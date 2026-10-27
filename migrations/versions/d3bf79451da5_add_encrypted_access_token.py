"""Add encrypted access token to social accounts

Revision ID: d3bf79451da5
Revises: f4b8d1c2a903
Create Date: 2026-10-26

Stores the OAuth access token for platforms that need one, such as
Instagram. Both columns are nullable, so existing accounts are unaffected.
"""

from alembic import op
import sqlalchemy as sa


revision = "d3bf79451da5"
down_revision = "f4b8d1c2a903"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("social_account", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("encrypted_access_token", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("access_token_expires_at", sa.DateTime(), nullable=True)
        )


def downgrade():
    with op.batch_alter_table("social_account", schema=None) as batch_op:
        batch_op.drop_column("access_token_expires_at")
        batch_op.drop_column("encrypted_access_token")

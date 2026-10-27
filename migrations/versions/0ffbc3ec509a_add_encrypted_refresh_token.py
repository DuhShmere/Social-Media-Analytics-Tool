"""Add encrypted refresh token to social accounts

Revision ID: 0ffbc3ec509a
Revises: d3bf79451da5
Create Date: 2026-10-27

TikTok access tokens last 24 hours, so the long-lived refresh token is
stored (encrypted) to get new ones. Both columns are nullable, so
existing accounts are unaffected.
"""

from alembic import op
import sqlalchemy as sa


revision = "0ffbc3ec509a"
down_revision = "d3bf79451da5"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("social_account", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("encrypted_refresh_token", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("refresh_token_expires_at", sa.DateTime(), nullable=True)
        )


def downgrade():
    with op.batch_alter_table("social_account", schema=None) as batch_op:
        batch_op.drop_column("refresh_token_expires_at")
        batch_op.drop_column("encrypted_refresh_token")

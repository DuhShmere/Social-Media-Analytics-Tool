"""Add failed sign-in attempts, for the sign-in lockout

Revision ID: 9d2f4b7e1a63
Revises: 7c1e5a9b2d40
Create Date: 2026-11-25
"""

from alembic import op
import sqlalchemy as sa


revision = "9d2f4b7e1a63"
down_revision = "7c1e5a9b2d40"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "failed_login",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("attempted_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_failed_login_username", "failed_login", ["username"])
    op.create_index("ix_failed_login_attempted_at", "failed_login", ["attempted_at"])


def downgrade():
    op.drop_index("ix_failed_login_attempted_at", table_name="failed_login")
    op.drop_index("ix_failed_login_username", table_name="failed_login")
    op.drop_table("failed_login")

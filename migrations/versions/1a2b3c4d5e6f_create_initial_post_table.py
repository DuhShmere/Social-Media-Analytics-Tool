"""Create initial post table

Revision ID: 1a2b3c4d5e6f
Revises:
Create Date: 2026-09-23
"""

from alembic import op
import sqlalchemy as sa


revision = "1a2b3c4d5e6f"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "post",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "platform",
            sa.String(length=50),
            nullable=False,
        ),
        sa.Column(
            "caption",
            sa.String(length=300),
            nullable=True,
        ),
        sa.Column(
            "content_type",
            sa.String(length=50),
            nullable=False,
        ),
        sa.Column("posted_at", sa.DateTime(), nullable=False),
        sa.Column("views", sa.Integer(), nullable=True),
        sa.Column("likes", sa.Integer(), nullable=True),
        sa.Column("comments", sa.Integer(), nullable=True),
        sa.Column("shares", sa.Integer(), nullable=True),
        sa.Column("saves", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade():
    op.drop_table("post")

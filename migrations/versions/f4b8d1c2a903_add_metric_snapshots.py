"""Add metric snapshots

Revision ID: f4b8d1c2a903
Revises: ae62ad557fcd
Create Date: 2026-09-23
"""

from alembic import op
import sqlalchemy as sa


revision = "f4b8d1c2a903"
down_revision = "ae62ad557fcd"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "metric_snapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column(
            "captured_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "views",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "likes",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "comments",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "shares",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "saves",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["post_id"],
            ["post.id"],
            name="fk_metric_snapshot_post_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_metric_snapshot_post_id",
        "metric_snapshot",
        ["post_id"],
        unique=False,
    )

    op.execute(
        sa.text(
            """
            INSERT INTO metric_snapshot (
                post_id,
                captured_at,
                views,
                likes,
                comments,
                shares,
                saves
            )
            SELECT
                id,
                CURRENT_TIMESTAMP,
                COALESCE(views, 0),
                COALESCE(likes, 0),
                COALESCE(comments, 0),
                COALESCE(shares, 0),
                COALESCE(saves, 0)
            FROM post
            """
        )
    )


def downgrade():
    op.drop_index(
        "ix_metric_snapshot_post_id",
        table_name="metric_snapshot",
    )
    op.drop_table("metric_snapshot")

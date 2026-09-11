"""Add YouTube import fields

Revision ID: 8af313919e9c
Revises:
Create Date: 2026-09-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Revision identifiers used by Alembic.
revision: str = "8af313919e9c"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # Clean any null values before making the existing
    # columns non-nullable.
    op.execute(
        sa.text(
            "UPDATE post "
            "SET caption = '' "
            "WHERE caption IS NULL"
        )
    )

    op.execute(
        sa.text(
            "UPDATE post "
            "SET views = 0 "
            "WHERE views IS NULL"
        )
    )

    op.execute(
        sa.text(
            "UPDATE post "
            "SET likes = 0 "
            "WHERE likes IS NULL"
        )
    )

    op.execute(
        sa.text(
            "UPDATE post "
            "SET comments = 0 "
            "WHERE comments IS NULL"
        )
    )

    op.execute(
        sa.text(
            "UPDATE post "
            "SET shares = 0 "
            "WHERE shares IS NULL"
        )
    )

    op.execute(
        sa.text(
            "UPDATE post "
            "SET saves = 0 "
            "WHERE saves IS NULL"
        )
    )

    # SQLite requires batch mode for many schema changes.
    with op.batch_alter_table("post") as batch_op:
        batch_op.add_column(
            sa.Column(
                "external_id",
                sa.String(length=150),
                nullable=True,
            )
        )

        batch_op.add_column(
            sa.Column(
                "external_url",
                sa.String(length=500),
                nullable=True,
            )
        )

        batch_op.add_column(
            sa.Column(
                "thumbnail_url",
                sa.String(length=500),
                nullable=True,
            )
        )

        batch_op.add_column(
            sa.Column(
                "source",
                sa.String(length=30),
                server_default=sa.text("'manual'"),
                nullable=False,
            )
        )

        batch_op.alter_column(
            "caption",
            existing_type=sa.String(length=300),
            nullable=False,
        )

        batch_op.alter_column(
            "views",
            existing_type=sa.Integer(),
            nullable=False,
        )

        batch_op.alter_column(
            "likes",
            existing_type=sa.Integer(),
            nullable=False,
        )

        batch_op.alter_column(
            "comments",
            existing_type=sa.Integer(),
            nullable=False,
        )

        batch_op.alter_column(
            "shares",
            existing_type=sa.Integer(),
            nullable=False,
        )

        batch_op.alter_column(
            "saves",
            existing_type=sa.Integer(),
            nullable=False,
        )

        batch_op.create_unique_constraint(
            "uq_post_external_id",
            ["external_id"],
        )


def downgrade():
    with op.batch_alter_table("post") as batch_op:
        batch_op.drop_constraint(
            "uq_post_external_id",
            type_="unique",
        )

        batch_op.alter_column(
            "saves",
            existing_type=sa.Integer(),
            nullable=True,
        )

        batch_op.alter_column(
            "shares",
            existing_type=sa.Integer(),
            nullable=True,
        )

        batch_op.alter_column(
            "comments",
            existing_type=sa.Integer(),
            nullable=True,
        )

        batch_op.alter_column(
            "likes",
            existing_type=sa.Integer(),
            nullable=True,
        )

        batch_op.alter_column(
            "views",
            existing_type=sa.Integer(),
            nullable=True,
        )

        batch_op.alter_column(
            "caption",
            existing_type=sa.String(length=300),
            nullable=True,
        )

        batch_op.drop_column("source")
        batch_op.drop_column("thumbnail_url")
        batch_op.drop_column("external_url")
        batch_op.drop_column("external_id")
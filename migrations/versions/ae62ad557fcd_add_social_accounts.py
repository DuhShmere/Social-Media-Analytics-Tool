"""Add social accounts

Revision ID: ae62ad557fcd
Revises: 8af313919e9c
Create Date: 2026-09-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ae62ad557fcd"
down_revision: Union[str, Sequence[str], None] = "8af313919e9c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    # The first failed migration may have already created
    # this table, so only create it if it does not exist.
    if "social_account" not in inspector.get_table_names():
        op.create_table(
            "social_account",
            sa.Column(
                "id",
                sa.Integer(),
                nullable=False,
            ),
            sa.Column(
                "platform",
                sa.String(length=50),
                nullable=False,
            ),
            sa.Column(
                "account_name",
                sa.String(length=150),
                nullable=False,
            ),
            sa.Column(
                "account_handle",
                sa.String(length=150),
                nullable=True,
            ),
            sa.Column(
                "external_account_id",
                sa.String(length=150),
                nullable=False,
            ),
            sa.Column(
                "last_synced_at",
                sa.DateTime(),
                nullable=True,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "platform",
                "external_account_id",
                name=(
                    "uq_social_account_platform_"
                    "external_id"
                ),
            ),
        )

    # Refresh inspection after ensuring the table exists.
    inspector = sa.inspect(connection)

    post_columns = {
        column["name"]
        for column in inspector.get_columns("post")
    }

    post_unique_constraints = {
        constraint.get("name")
        for constraint in inspector.get_unique_constraints(
            "post"
        )
    }

    post_foreign_keys = {
        foreign_key.get("name")
        for foreign_key in inspector.get_foreign_keys("post")
    }

    with op.batch_alter_table(
        "post",
        schema=None,
    ) as batch_op:
        if "social_account_id" not in post_columns:
            batch_op.add_column(
                sa.Column(
                    "social_account_id",
                    sa.Integer(),
                    nullable=True,
                )
            )

        if "uq_post_external_id" in post_unique_constraints:
            batch_op.drop_constraint(
                "uq_post_external_id",
                type_="unique",
            )

        if (
            "uq_post_platform_external_id"
            not in post_unique_constraints
        ):
            batch_op.create_unique_constraint(
                "uq_post_platform_external_id",
                ["platform", "external_id"],
            )

        if (
            "fk_post_social_account_id"
            not in post_foreign_keys
        ):
            batch_op.create_foreign_key(
                "fk_post_social_account_id",
                "social_account",
                ["social_account_id"],
                ["id"],
            )


def downgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "post" in inspector.get_table_names():
        post_columns = {
            column["name"]
            for column in inspector.get_columns("post")
        }

        post_unique_constraints = {
            constraint.get("name")
            for constraint in inspector.get_unique_constraints(
                "post"
            )
        }

        post_foreign_keys = {
            foreign_key.get("name")
            for foreign_key in inspector.get_foreign_keys(
                "post"
            )
        }

        with op.batch_alter_table(
            "post",
            schema=None,
        ) as batch_op:
            if (
                "fk_post_social_account_id"
                in post_foreign_keys
            ):
                batch_op.drop_constraint(
                    "fk_post_social_account_id",
                    type_="foreignkey",
                )

            if (
                "uq_post_platform_external_id"
                in post_unique_constraints
            ):
                batch_op.drop_constraint(
                    "uq_post_platform_external_id",
                    type_="unique",
                )

            if (
                "uq_post_external_id"
                not in post_unique_constraints
            ):
                batch_op.create_unique_constraint(
                    "uq_post_external_id",
                    ["external_id"],
                )

            if "social_account_id" in post_columns:
                batch_op.drop_column(
                    "social_account_id"
                )

    inspector = sa.inspect(connection)

    if "social_account" in inspector.get_table_names():
        op.drop_table("social_account")
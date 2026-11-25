"""Add users; each social account and post belongs to one user

Revision ID: 7c1e5a9b2d40
Revises: 0ffbc3ec509a
Create Date: 2026-11-25

Existing accounts and posts are given to a new user named "owner" whose
password cannot be used yet. Set it with:

    flask --app app set-password owner

Uniqueness becomes per user, so two users can connect the same channel.
"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "7c1e5a9b2d40"
down_revision = "0ffbc3ec509a"
branch_labels = None
depends_on = None


OWNER_USERNAME = "owner"
UNUSABLE_PASSWORD = "!"  # Matches models.UNUSABLE_PASSWORD.


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=30), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username", name="uq_user_username"),
    )

    connection = op.get_bind()
    has_data = any(
        connection.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first()
        for table in ("social_account", "post")
    )

    owner_id = None

    if has_data:
        user_table = sa.table(
            "users",
            sa.column("username", sa.String),
            sa.column("password_hash", sa.String),
            sa.column("created_at", sa.DateTime),
        )
        connection.execute(
            user_table.insert().values(
                username=OWNER_USERNAME,
                password_hash=UNUSABLE_PASSWORD,
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        )
        owner_id = connection.execute(
            sa.text("SELECT id FROM users WHERE username = :name"),
            {"name": OWNER_USERNAME},
        ).scalar_one()

    # Add the column as nullable, fill it, then make it required.
    for table in ("social_account", "post"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))

        if owner_id is not None:
            connection.execute(
                sa.text(f"UPDATE {table} SET user_id = :owner"),
                {"owner": owner_id},
            )

    with op.batch_alter_table("social_account") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.Integer(), nullable=False)
        batch_op.drop_constraint(
            "uq_social_account_platform_external_id", type_="unique"
        )
        batch_op.create_unique_constraint(
            "uq_social_account_user_platform_external_id",
            ["user_id", "platform", "external_account_id"],
        )
        batch_op.create_foreign_key(
            "fk_social_account_user_id",
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index("ix_social_account_user_id", ["user_id"])

    with op.batch_alter_table("post") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.Integer(), nullable=False)
        batch_op.drop_constraint("uq_post_platform_external_id", type_="unique")
        batch_op.create_unique_constraint(
            "uq_post_user_platform_external_id",
            ["user_id", "platform", "external_id"],
        )
        batch_op.create_foreign_key(
            "fk_post_user_id",
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index("ix_post_user_id", ["user_id"])


def downgrade():
    # Rows from several users can collide under the old global
    # uniqueness, so only a single-user database can be downgraded.
    connection = op.get_bind()
    user_count = connection.execute(sa.text("SELECT COUNT(*) FROM users")).scalar_one()

    if user_count > 1:
        raise RuntimeError(
            "Cannot downgrade: the database has more than one user."
        )

    with op.batch_alter_table("post") as batch_op:
        batch_op.drop_index("ix_post_user_id")
        batch_op.drop_constraint("fk_post_user_id", type_="foreignkey")
        batch_op.drop_constraint("uq_post_user_platform_external_id", type_="unique")
        batch_op.create_unique_constraint(
            "uq_post_platform_external_id", ["platform", "external_id"]
        )
        batch_op.drop_column("user_id")

    with op.batch_alter_table("social_account") as batch_op:
        batch_op.drop_index("ix_social_account_user_id")
        batch_op.drop_constraint("fk_social_account_user_id", type_="foreignkey")
        batch_op.drop_constraint(
            "uq_social_account_user_platform_external_id", type_="unique"
        )
        batch_op.create_unique_constraint(
            "uq_social_account_platform_external_id",
            ["platform", "external_account_id"],
        )
        batch_op.drop_column("user_id")

    op.drop_table("users")

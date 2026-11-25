"""`flask create-user`, `flask set-password`, and the users migration."""

import logging
import sqlite3

import pytest
from flask_migrate import upgrade

import app_factory
from app_factory import create_app
from conftest import USERNAME
from extensions import db
from models import UNUSABLE_PASSWORD, User


# ---------------------------------------------------------
# Commands
# ---------------------------------------------------------

def run(app, *args):
    return app.test_cli_runner().invoke(args=list(args))


def test_create_user(app):
    result = run(app, "create-user", "Dana", "--password", "dana-password-123")

    assert result.exit_code == 0, result.output
    assert "Created user dana." in result.output

    with app.app_context():
        assert User.query.filter_by(username="dana").one().check_password("dana-password-123")


@pytest.mark.parametrize(
    ("username", "password", "message"),
    [
        (USERNAME, "a-long-password-1", "That username is taken."),
        ("dana", "short", "at least 12 characters"),
        ("no spaces", "a-long-password-1", "3 to 30 characters"),
    ],
)
def test_create_user_rejects_bad_input(app, username, password, message):
    result = run(app, "create-user", username, "--password", password)

    assert result.exit_code != 0
    assert message in result.output


def test_create_user_prompts_without_echoing(app):
    result = app.test_cli_runner().invoke(
        args=["create-user", "erin"],
        input="erin-password-123\nerin-password-123\n",
    )

    assert result.exit_code == 0, result.output
    assert "erin-password-123" not in result.output


def test_set_password_unlocks_the_migrated_owner(app):
    with app.app_context():
        db.session.add(User(username="owner", password_hash=UNUSABLE_PASSWORD))
        db.session.commit()

    result = run(app, "set-password", "OWNER", "--password", "owner-password-123")

    assert result.exit_code == 0, result.output

    with app.app_context():
        assert User.query.filter_by(username="owner").one().check_password("owner-password-123")


def test_set_password_rejects_unknown_users_and_short_passwords(app):
    assert "No user named 'ghost'" in run(app, "set-password", "ghost", "--password", "a-long-password-1").output
    assert "at least 12 characters" in run(app, "set-password", USERNAME, "--password", "short").output


# ---------------------------------------------------------
# Migration
# ---------------------------------------------------------

@pytest.fixture()
def restore_logging():
    # Alembic's env.py calls logging.config.fileConfig(), which disables
    # every existing logger and replaces the root handlers. Put them back
    # so later tests that check log output still see it.
    root = logging.getLogger()
    saved_root = (root.level, list(root.handlers))
    saved_disabled = {
        name: logger.disabled
        for name, logger in logging.root.manager.loggerDict.items()
        if isinstance(logger, logging.Logger)
    }

    yield

    root.setLevel(saved_root[0])
    root.handlers[:] = saved_root[1]

    for name, disabled in saved_disabled.items():
        logging.getLogger(name).disabled = disabled


@pytest.fixture()
def file_app(tmp_path, monkeypatch, restore_logging):
    """A development app on an empty SQLite file, schema not created."""
    monkeypatch.setattr(app_factory, "load_dotenv", lambda *args, **kwargs: None)
    path = tmp_path / "migrate.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("SECRET_KEY", "migration-test")
    return create_app("development"), path


def test_migration_gives_existing_data_to_a_locked_owner(file_app):
    app, path = file_app

    with app.app_context():
        upgrade(revision="0ffbc3ec509a")  # The last single-user schema.

    connection = sqlite3.connect(path)
    connection.executescript(
        """
        INSERT INTO social_account (id, platform, account_name, external_account_id)
        VALUES (1, 'YouTube', 'Channel', 'UC1');
        INSERT INTO post (id, platform, caption, content_type, posted_at, views, likes,
                          comments, shares, saves, source, external_id, social_account_id)
        VALUES (1, 'YouTube', 'Old post', 'Video', '2026-09-01', 1, 0, 0, 0, 0,
                'youtube_api', 'v1', 1);
        """
    )
    connection.commit()
    connection.close()

    with app.app_context():
        upgrade()

        owner = User.query.one()
        assert owner.username == "owner"
        assert owner.password_hash == UNUSABLE_PASSWORD
        assert not owner.check_password(UNUSABLE_PASSWORD)
        assert [account.user_id for account in owner.social_accounts] == [owner.id]
        assert [post.caption for post in owner.posts] == ["Old post"]


def test_migration_on_an_empty_database_creates_no_owner(file_app):
    app, _ = file_app

    with app.app_context():
        upgrade()

        assert User.query.count() == 0

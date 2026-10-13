import os

import pytest

import app_factory
from app_factory import create_app
from config import get_config
from extensions import db
from services.youtube import ApiKeyRedactionFilter


@pytest.fixture()
def no_dotenv(monkeypatch):
    # Keep the real .env out of these tests.
    monkeypatch.setattr(app_factory, "load_dotenv", lambda *args, **kwargs: None)


def test_testing_config_ignores_real_database_and_key(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///should-not-be-used.db")
    monkeypatch.setenv("YOUTUBE_API_KEY", "real-key-should-not-be-used")

    app = create_app("testing")

    assert app.testing is True
    assert app.config["SQLALCHEMY_DATABASE_URI"] == "sqlite:///:memory:"
    assert app.config["YOUTUBE_API_KEY"] is None


@pytest.mark.parametrize(
    ("name", "debug"),
    [("development", True), ("production", False)],
)
def test_configs_read_environment_variables(monkeypatch, name, debug):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user@localhost/analytics")
    monkeypatch.setenv("YOUTUBE_API_KEY", "fake-key")

    config = get_config(name)

    assert config.DEBUG is debug
    assert config.TESTING is False
    assert config.SQLALCHEMY_DATABASE_URI == "postgresql://user@localhost/analytics"
    assert config.YOUTUBE_API_KEY == "fake-key"


def test_unknown_environment_is_rejected():
    with pytest.raises(ValueError, match="Unknown APP_ENV 'staging'"):
        get_config("staging")


def test_default_database_is_next_to_the_code_from_any_folder(
    tmp_path,
    monkeypatch,
    no_dotenv,
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)

    app = create_app("development")

    with app.app_context():
        # Reading the URL does not open a connection to the database.
        database_path = db.engine.url.database

    assert database_path == os.path.join(
        app_factory.BASE_DIR,
        "instance",
        "tracker.db",
    )


def test_api_key_filter_is_added_only_once():
    create_app("testing")
    app = create_app("testing")

    filters = [
        log_filter
        for log_filter in app.logger.filters
        if isinstance(log_filter, ApiKeyRedactionFilter)
    ]

    assert len(filters) == 1

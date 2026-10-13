import pytest

from app_factory import create_app
from extensions import db


@pytest.fixture()
def app():
    # Each test gets a new app with its own in-memory database.
    # TestingConfig ignores DATABASE_URL and .env, so tests can never
    # reach the real tracker.db or the real YouTube API key.
    app = create_app("testing")

    with app.app_context():
        db.create_all()

    yield app

    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()

import os

import pytest


# This must be set before importing app.py.
# It prevents tests from accessing the real tracker.db database.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app import app as flask_app
from extensions import db


@pytest.fixture()
def app():
    flask_app.config.update(
        TESTING=True,
        # Tests that need a key set one explicitly, so the real key
        # from .env can never be used.
        YOUTUBE_API_KEY=None,
    )

    with flask_app.app_context():
        db.create_all()

    yield flask_app

    with flask_app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()
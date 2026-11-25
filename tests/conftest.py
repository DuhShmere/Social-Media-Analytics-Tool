import pytest
from werkzeug.security import generate_password_hash

from app_factory import create_app
from extensions import db
from models import User
from routes.auth import FINGERPRINT_KEY, USER_ID_KEY, _password_fingerprint


# Every test database starts with this user, and the `client` fixture is
# signed in as them, so tests that build rows use user_id=USER_ID.
USER_ID = 1
USERNAME = "tester"
PASSWORD = "tester-password-123"

# Password hashing is slow on purpose, so hash the default password
# once rather than in every test.
_PASSWORD_HASH = generate_password_hash(PASSWORD)


def add_user(username, password):
    user = User(username=username)

    if password == PASSWORD:
        user.password_hash = _PASSWORD_HASH
    else:
        user.set_password(password)

    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture()
def app():
    # Each test gets a new app with its own in-memory database.
    # TestingConfig ignores DATABASE_URL and .env, so tests can never
    # reach the real tracker.db or the real YouTube API key.
    app = create_app("testing")

    with app.app_context():
        db.create_all()
        assert add_user(USERNAME, PASSWORD).id == USER_ID

    yield app

    with app.app_context():
        db.session.remove()
        db.drop_all()


def sign_in_as(app, client, user_id):
    with app.app_context():
        user = db.session.get(User, user_id)
        fingerprint = _password_fingerprint(user)

    with client.session_transaction() as session:
        session[USER_ID_KEY] = user_id
        session[FINGERPRINT_KEY] = fingerprint


@pytest.fixture()
def client(app):
    """A browser signed in as the default user."""
    client = app.test_client()
    sign_in_as(app, client, USER_ID)
    return client


@pytest.fixture()
def anonymous_client(app):
    """A browser that has not signed in."""
    return app.test_client()

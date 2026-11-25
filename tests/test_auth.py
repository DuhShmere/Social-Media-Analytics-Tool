"""Signing up, signing in and out, and changing passwords."""

import re
from datetime import datetime, timedelta

import pytest

from conftest import PASSWORD, USER_ID, USERNAME, add_user, sign_in_as
from extensions import db
from models import UNUSABLE_PASSWORD, FailedLogin, User
from routes import auth
from routes.auth import FINGERPRINT_KEY, USER_ID_KEY


def sign_in(client, username=USERNAME, password=PASSWORD, next_page=None):
    data = {"username": username, "password": password}

    if next_page is not None:
        data["next"] = next_page

    return client.post("/login", data=data)


def sign_up(client, username="newuser", password="a-long-new-password", confirm=None):
    return client.post(
        "/signup",
        data={
            "username": username,
            "password": password,
            "confirm_password": password if confirm is None else confirm,
        },
    )


def is_signed_in(client):
    return client.get("/").status_code == 200


# ---------------------------------------------------------
# Signed out
# ---------------------------------------------------------

@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/accounts",
        "/add-post",
        "/import-csv",
        "/import-youtube",
        "/export-csv",
        "/account/password",
        "/instagram/connect",
        "/tiktok/connect",
    ],
)
def test_pages_send_signed_out_visitors_to_sign_in(anonymous_client, path):
    response = anonymous_client.get(path)

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?next=")


def test_sign_in_redirect_remembers_the_page_and_query(anonymous_client):
    response = anonymous_client.get("/accounts?tab=tiktok")

    assert response.headers["Location"] == "/login?next=/accounts?tab%3Dtiktok"


def test_oauth_callbacks_need_sign_in(anonymous_client):
    for platform in ("instagram", "tiktok"):
        response = anonymous_client.get(f"/{platform}/callback?code=abc&state=xyz")

        assert response.status_code == 302
        assert response.headers["Location"].startswith("/login")


def test_api_returns_json_401_when_signed_out(anonymous_client):
    response = anonymous_client.get("/api/analytics")

    assert response.status_code == 401
    assert response.get_json() == {"error": "Sign in required."}


def test_signed_out_form_posts_are_redirected(anonymous_client):
    response = anonymous_client.post("/add-post", data={"caption": "x"})

    assert response.status_code == 302
    assert response.headers["Location"] == "/login"


def test_unknown_pages_do_not_reveal_whether_they_exist(anonymous_client):
    response = anonymous_client.get("/does-not-exist")

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login")


@pytest.mark.parametrize(
    "path",
    ["/login", "/signup", "/health", "/terms", "/privacy", "/static/css/style.css"],
)
def test_public_pages_stay_public(anonymous_client, path):
    assert anonymous_client.get(path).status_code == 200


def test_tiktok_site_verification_stays_public(app, anonymous_client, monkeypatch):
    monkeypatch.setitem(app.config, "TIKTOK_SITE_VERIFICATION", "abc123")

    assert anonymous_client.get("/tiktokabc123.txt").status_code == 200


def test_signed_out_pages_hide_navigation_links(anonymous_client):
    html = anonymous_client.get("/terms").get_data(as_text=True)

    assert 'aria-label="Main"' in html
    assert "Dashboard" not in html
    assert "Sign Out" not in html


# ---------------------------------------------------------
# Signing in
# ---------------------------------------------------------

def test_correct_password_signs_in_and_returns_to_the_page(anonymous_client):
    response = sign_in(anonymous_client, next_page="/accounts?tab=tiktok")

    assert response.status_code == 302
    assert response.headers["Location"] == "/accounts?tab=tiktok"

    html = anonymous_client.get("/").get_data(as_text=True)
    assert "Sign Out" in html
    assert USERNAME in html


def test_usernames_are_not_case_sensitive(anonymous_client):
    assert sign_in(anonymous_client, username="  TeStEr ").status_code == 302
    assert is_signed_in(anonymous_client)


@pytest.mark.parametrize(
    ("username", "password"),
    [(USERNAME, "wrong-password-here"), ("nobody", PASSWORD), (USERNAME, ""), ("", "")],
    ids=["wrong-password", "unknown-user", "empty-password", "empty-form"],
)
def test_bad_credentials_get_one_message(anonymous_client, username, password):
    response = sign_in(anonymous_client, username=username, password=password)

    assert response.status_code == 401
    # The same message either way, so usernames cannot be discovered.
    assert "That username and password do not match." in response.get_data(as_text=True)
    assert not is_signed_in(anonymous_client)


def test_failed_sign_in_keeps_the_username(anonymous_client):
    html = sign_in(anonymous_client, password="wrong-password-here").get_data(as_text=True)

    assert f'value="{USERNAME}"' in html


def test_overlong_password_is_rejected_without_hashing(anonymous_client):
    assert sign_in(anonymous_client, password="x" * 10_000).status_code == 401


def test_sign_in_without_next_goes_to_dashboard(anonymous_client):
    assert sign_in(anonymous_client).headers["Location"] == "/"


@pytest.mark.parametrize(
    "next_page",
    [
        "https://evil.example.com/",
        "//evil.example.com/",
        "/\\evil.example.com",
        "javascript:alert(1)",
        "accounts",
    ],
)
def test_sign_in_never_redirects_to_another_site(anonymous_client, next_page):
    assert sign_in(anonymous_client, next_page=next_page).headers["Location"] == "/"


def test_sign_in_starts_a_new_session(anonymous_client):
    with anonymous_client.session_transaction() as session:
        session["left_over"] = "from before sign-in"

    sign_in(anonymous_client)

    with anonymous_client.session_transaction() as session:
        assert "left_over" not in session
        assert session[USER_ID_KEY] == USER_ID
        # The cookie holds a keyed hash, never the password or its hash.
        assert PASSWORD not in str(dict(session))
        assert "scrypt" not in str(dict(session))


def test_signed_in_visit_to_login_page_skips_the_form(client):
    response = client.get("/login?next=/accounts")

    assert response.status_code == 302
    assert response.headers["Location"] == "/accounts"


def test_sign_out_ends_the_session(client):
    response = client.post("/logout", follow_redirects=True)

    assert "You signed out." in response.get_data(as_text=True)
    assert not is_signed_in(client)


def test_a_forged_session_is_rejected(anonymous_client):
    with anonymous_client.session_transaction() as session:
        session[USER_ID_KEY] = USER_ID
        session[FINGERPRINT_KEY] = "0" * 64

    assert not is_signed_in(anonymous_client)


def test_a_deleted_users_session_is_rejected(app, client):
    with app.app_context():
        db.session.delete(db.session.get(User, USER_ID))
        db.session.commit()

    assert not is_signed_in(client)


def test_migrated_owner_cannot_sign_in_until_a_password_is_set(app, anonymous_client):
    with app.app_context():
        db.session.add(User(username="owner", password_hash=UNUSABLE_PASSWORD))
        db.session.commit()

    for password in (UNUSABLE_PASSWORD, "", "owner"):
        assert sign_in(anonymous_client, username="owner", password=password).status_code == 401


def test_failed_sign_in_is_logged_without_the_password(anonymous_client, caplog):
    sign_in(anonymous_client, password="guess-number-one")

    assert "Failed sign-in attempt" in caplog.text
    assert "guess-number-one" not in caplog.text


def test_sign_in_form_needs_a_csrf_token(app, anonymous_client, monkeypatch):
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)

    assert sign_in(anonymous_client).status_code == 400

    html = anonymous_client.get("/login").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
    response = anonymous_client.post(
        "/login",
        data={"username": USERNAME, "password": PASSWORD, "csrf_token": token},
    )

    assert response.status_code == 302
    assert is_signed_in(anonymous_client)


def test_sign_in_form_works_with_password_managers(anonymous_client):
    html = anonymous_client.get("/login").get_data(as_text=True)

    assert '<label for="username">Username</label>' in html
    assert 'autocomplete="username"' in html
    assert '<label for="password">Password</label>' in html
    assert 'autocomplete="current-password"' in html


# ---------------------------------------------------------
# Signing up
# ---------------------------------------------------------

def test_sign_up_creates_an_account_and_signs_in(app, anonymous_client):
    response = sign_up(anonymous_client, username="NewUser")

    assert response.status_code == 302
    assert response.headers["Location"] == "/"

    html = anonymous_client.get("/").get_data(as_text=True)
    assert "Welcome, newuser!" in html

    with app.app_context():
        user = User.query.filter_by(username="newuser").one()
        assert user.password_hash.startswith("scrypt:")
        assert "a-long-new-password" not in user.password_hash


@pytest.mark.parametrize(
    ("username", "password", "confirm", "message"),
    [
        (USERNAME, "a-long-new-password", None, "That username is taken."),
        ("TESTER", "a-long-new-password", None, "That username is taken."),
        ("ab", "a-long-new-password", None, "3 to 30 characters"),
        ("has space", "a-long-new-password", None, "3 to 30 characters"),
        ("x" * 31, "a-long-new-password", None, "3 to 30 characters"),
        ("newuser", "short", None, "at least 12 characters"),
        ("newuser", "x" * 129, None, "at most 128 characters"),
        ("newuser", "a-long-new-password", "different-password", "do not match"),
    ],
    ids=["taken", "taken-other-case", "too-short-name", "space", "too-long-name",
         "short-password", "long-password", "mismatch"],
)
def test_sign_up_rejects_bad_input(app, anonymous_client, username, password, confirm, message):
    response = sign_up(anonymous_client, username, password, confirm)

    assert response.status_code == 400
    assert message in response.get_data(as_text=True)
    assert not is_signed_in(anonymous_client)

    with app.app_context():
        assert User.query.count() == 1


def test_sign_up_can_be_closed(app, anonymous_client, monkeypatch):
    monkeypatch.setitem(app.config, "SIGNUP_ENABLED", False)

    assert anonymous_client.get("/signup").status_code == 403
    assert sign_up(anonymous_client).status_code == 403
    assert "Create an account" not in anonymous_client.get("/login").get_data(as_text=True)

    with app.app_context():
        assert User.query.count() == 1


def test_signed_in_visit_to_sign_up_goes_to_dashboard(client):
    assert client.get("/signup").headers["Location"] == "/"


# ---------------------------------------------------------
# Changing the password
# ---------------------------------------------------------

def change_password(client, current=PASSWORD, new="a-brand-new-password", confirm=None):
    return client.post(
        "/account/password",
        data={
            "current_password": current,
            "new_password": new,
            "confirm_password": new if confirm is None else confirm,
        },
    )


def test_change_password_page_is_labelled(client):
    html = client.get("/account/password").get_data(as_text=True)

    for field in ("current_password", "new_password", "confirm_password"):
        assert f'<label for="{field}">' in html


def test_changing_password_works_and_signs_out_other_devices(app, client):
    other_device = app.test_client()
    sign_in_as(app, other_device, USER_ID)

    response = change_password(client)

    assert response.headers["Location"] == "/"
    assert is_signed_in(client)
    assert not is_signed_in(other_device)

    fresh = app.test_client()
    assert sign_in(fresh, password=PASSWORD).status_code == 401
    assert sign_in(fresh, password="a-brand-new-password").status_code == 302


@pytest.mark.parametrize(
    ("current", "new", "confirm", "message"),
    [
        ("wrong-current-pass", "a-brand-new-password", None, "current password is not correct"),
        (PASSWORD, "short", None, "at least 12 characters"),
        (PASSWORD, "a-brand-new-password", "something-else-here", "do not match"),
    ],
    ids=["wrong-current", "short", "mismatch"],
)
def test_change_password_rejects_bad_input(app, client, current, new, confirm, message):
    response = change_password(client, current, new, confirm)

    assert response.status_code == 400
    assert message in response.get_data(as_text=True)
    assert sign_in(app.test_client(), password=PASSWORD).status_code == 302


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

def test_sign_up_is_open_unless_turned_off(monkeypatch):
    from config import get_config

    monkeypatch.setenv("SECRET_KEY", "fake-secret")
    monkeypatch.delenv("SIGNUP_ENABLED", raising=False)
    assert get_config("development").SIGNUP_ENABLED is True

    monkeypatch.setenv("SIGNUP_ENABLED", "false")
    assert get_config("development").SIGNUP_ENABLED is False


def test_add_user_helper_makes_distinct_users(app):
    with app.app_context():
        other = add_user("someone-else", "another-password-1")
        assert other.id != USER_ID


# ---------------------------------------------------------
# Lockout after 3 failed attempts
# ---------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 25, 12, 0)

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)


@pytest.fixture()
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(auth, "_now", clock)
    return clock


def fail(client, username=USERNAME, times=1):
    for _ in range(times):
        assert sign_in(client, username=username, password="wrong-password-here").status_code == 401


def test_three_failures_lock_the_username_even_for_the_right_password(anonymous_client, clock):
    fail(anonymous_client, times=3)

    response = sign_in(anonymous_client)

    assert response.status_code == 429
    assert "Too many failed sign-in attempts. Try again in 15 minutes." in response.get_data(as_text=True)
    assert not is_signed_in(anonymous_client)


def test_two_failures_do_not_lock(anonymous_client, clock):
    fail(anonymous_client, times=2)

    assert sign_in(anonymous_client).status_code == 302


def test_lock_counts_down_and_ends_15_minutes_after_the_last_failure(anonymous_client, clock):
    fail(anonymous_client)
    clock.advance(minutes=5)
    fail(anonymous_client, times=2)

    clock.advance(minutes=14)
    assert "Try again in 1 minute." in sign_in(anonymous_client).get_data(as_text=True)

    clock.advance(minutes=1)
    assert sign_in(anonymous_client).status_code == 302


def test_attempts_while_locked_do_not_extend_the_lock(anonymous_client, clock):
    fail(anonymous_client, times=3)

    for _ in range(5):
        clock.advance(minutes=2)
        assert sign_in(anonymous_client).status_code == 429

    clock.advance(minutes=5)
    assert sign_in(anonymous_client).status_code == 302


def test_locked_attempts_skip_password_hashing(app, anonymous_client, clock, monkeypatch):
    fail(anonymous_client, times=3)
    monkeypatch.setattr(User, "check_password", lambda self, password: pytest.fail("hashed while locked"))

    assert sign_in(anonymous_client).status_code == 429


def test_failures_spread_over_more_than_15_minutes_do_not_lock(anonymous_client, clock):
    for _ in range(3):
        fail(anonymous_client)
        clock.advance(minutes=8)

    assert sign_in(anonymous_client).status_code == 302


def test_successful_sign_in_resets_the_count(app, anonymous_client, clock):
    fail(anonymous_client, times=2)
    assert sign_in(anonymous_client).status_code == 302
    anonymous_client.post("/logout")

    fail(anonymous_client, times=2)
    assert sign_in(anonymous_client).status_code == 302


def test_lock_ignores_username_case_and_spaces(anonymous_client, clock):
    fail(anonymous_client, username="TESTER")
    fail(anonymous_client, username=" tester ")
    fail(anonymous_client, username="Tester")

    assert sign_in(anonymous_client).status_code == 429


def test_lock_applies_to_one_username_only(app, anonymous_client, clock):
    with app.app_context():
        add_user("other-user", "other-password-1")

    fail(anonymous_client, times=3)

    assert sign_in(app.test_client(), username="other-user", password="other-password-1").status_code == 302


def test_unknown_usernames_lock_the_same_way(anonymous_client, clock):
    # Otherwise a lockout would reveal which usernames exist.
    fail(anonymous_client, username="nobody", times=3)

    response = sign_in(anonymous_client, username="nobody", password="anything-at-all")

    assert response.status_code == 429
    assert "Too many failed sign-in attempts" in response.get_data(as_text=True)


def test_lock_holds_across_browsers(app, anonymous_client, clock):
    fail(anonymous_client, times=3)

    assert sign_in(app.test_client()).status_code == 429


def test_old_failures_are_cleaned_up(app, anonymous_client, clock):
    fail(anonymous_client, username="first", times=2)
    clock.advance(minutes=31)
    fail(anonymous_client, username="second")

    with app.app_context():
        assert [row.username for row in FailedLogin.query.all()] == ["second"]


def test_lock_settings_come_from_config(app, anonymous_client, clock, monkeypatch):
    monkeypatch.setitem(app.config, "LOGIN_MAX_ATTEMPTS", 5)

    fail(anonymous_client, times=4)
    assert sign_in(anonymous_client).status_code == 302

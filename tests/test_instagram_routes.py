"""Instagram connect, callback, sync, and disconnect routes.

Instagram's responses are simulated with `responses`, which fails any
request to a URL a test did not register, so nothing reaches Instagram.
"""

import re
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from extensions import db
from models import Post, SocialAccount
from routes.instagram import STATE_SESSION_KEY
from security import SECURITY_HEADERS
from services import instagram
from services.instagram import GRAPH_URL, SHORT_LIVED_TOKEN_URL, store_token
from token_vault import decrypt_token, generate_key


API = f"{GRAPH_URL}/{instagram.API_VERSION}"
REDIRECT_URI = "https://analytics.example.com/instagram/callback"


@pytest.fixture()
def configured(app, monkeypatch):
    key = generate_key()
    for name, value in {
        "INSTAGRAM_APP_ID": "123456",
        "INSTAGRAM_APP_SECRET": "app-secret",
        "INSTAGRAM_REDIRECT_URI": REDIRECT_URI,
        "TOKEN_ENCRYPTION_KEY": key,
    }.items():
        monkeypatch.setitem(app.config, name, value)
    return key


@pytest.fixture()
def http():
    with responses.RequestsMock() as mocked:
        yield mocked


def page_text(client, path="/accounts"):
    return client.get(path).get_data(as_text=True)


def mock_profile_and_posts(http, likes=120):
    http.get(f"{API}/me", json={"user_id": "17841400000", "username": "samir.creates"})
    http.get(
        f"{API}/me/media",
        json={
            "data": [
                {
                    "id": media_id,
                    "media_type": media_type,
                    "media_url": f"https://scontent.cdninstagram.com/{media_id}.jpg",
                    "permalink": f"https://www.instagram.com/p/{media_id}/",
                    "timestamp": "2026-10-12T18:05:00+0000",
                    "like_count": likes,
                    "comments_count": 8,
                }
                for media_id, media_type in (("1", "IMAGE"), ("2", "CAROUSEL_ALBUM"))
            ]
        },
    )
    for media_id in ("1", "2"):
        http.get(
            f"{API}/{media_id}/insights",
            json={"data": [{"name": "views", "values": [{"value": 900}]}]},
        )


def mock_token_exchange(http):
    http.post(
        SHORT_LIVED_TOKEN_URL,
        json={"data": [{"access_token": "short-token", "user_id": "17841400000"}]},
    )
    http.get(
        f"{GRAPH_URL}/access_token",
        json={"access_token": "long-lived-token", "token_type": "bearer", "expires_in": 5183944},
    )


def start_login(client, state="expected-state"):
    with client.session_transaction() as session:
        session[STATE_SESSION_KEY] = state


def connected_account(key, expires_at):
    account = SocialAccount(
        platform="Instagram",
        account_name="samir.creates",
        account_handle="@samir.creates",
        external_account_id="17841400000",
    )
    store_token(account, "stored-token", expires_at, key)
    db.session.add(account)
    db.session.commit()
    return account.id


# ---------------------------------------------------------
# Connect
# ---------------------------------------------------------

def test_connect_explains_missing_settings(client):
    response = client.get("/instagram/connect", follow_redirects=True)
    html = response.get_data(as_text=True)

    assert "Instagram is not set up yet" in html
    assert "INSTAGRAM_APP_ID" in html
    assert "TOKEN_ENCRYPTION_KEY" in html
    assert 'role="alert"' in html


def test_connect_redirects_to_instagram_with_a_state(client, configured):
    response = client.get("/instagram/connect")
    location = urlparse(response.headers["Location"])
    params = {k: v[0] for k, v in parse_qs(location.query).items()}

    assert response.status_code == 302
    assert location.netloc == "www.instagram.com"
    assert params["client_id"] == "123456"
    assert params["redirect_uri"] == REDIRECT_URI

    with client.session_transaction() as session:
        assert session[STATE_SESSION_KEY] == params["state"]
    assert len(params["state"]) >= 32


# ---------------------------------------------------------
# Callback
# ---------------------------------------------------------

def test_callback_connects_account_and_imports_posts(client, app, configured, http):
    mock_token_exchange(http)
    mock_profile_and_posts(http)
    start_login(client)

    response = client.get(
        "/instagram/callback?code=one-time-code%23_&state=expected-state",
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)

    assert "Connected @samir.creates. Imported 2 new posts and updated 0" in html
    assert 'role="status"' in html

    with app.app_context():
        account = SocialAccount.query.one()
        assert decrypt_token(account.encrypted_access_token, configured) == "long-lived-token"
        assert "long-lived-token" not in account.encrypted_access_token
        assert account.access_token_expires_at > datetime.now() + timedelta(days=59)
        assert Post.query.filter_by(source="instagram_api").count() == 2


@pytest.mark.parametrize(
    ("session_state", "query"),
    [
        ("expected-state", "code=c&state=forged-state"),
        (None, "code=c&state=anything"),
        ("expected-state", "code=c"),
    ],
    ids=["mismatched", "no-login-started", "missing-state"],
)
def test_callback_rejects_unverified_state(client, app, configured, http, session_state, query):
    if session_state:
        start_login(client, session_state)

    html = client.get(f"/instagram/callback?{query}", follow_redirects=True).get_data(as_text=True)

    assert "could not be verified" in html
    assert len(http.calls) == 0

    with app.app_context():
        assert SocialAccount.query.count() == 0


def test_state_cannot_be_reused(client, configured, http):
    mock_token_exchange(http)
    mock_profile_and_posts(http)
    start_login(client)
    client.get("/instagram/callback?code=c&state=expected-state")

    html = client.get(
        "/instagram/callback?code=c&state=expected-state",
        follow_redirects=True,
    ).get_data(as_text=True)

    assert "could not be verified" in html


def test_cancelled_login_saves_nothing(client, app, configured, http):
    start_login(client)

    html = client.get(
        "/instagram/callback?error=access_denied&error_reason=user_denied",
        follow_redirects=True,
    ).get_data(as_text=True)

    assert "Instagram connection was cancelled." in html

    with app.app_context():
        assert SocialAccount.query.count() == 0


def test_failed_code_exchange_saves_nothing(client, app, configured, http):
    http.post(
        SHORT_LIVED_TOKEN_URL,
        status=400,
        json={"error_type": "OAuthException", "code": 400, "error_message": "Invalid code"},
    )
    start_login(client)

    html = client.get("/instagram/callback?code=expired&state=expected-state", follow_redirects=True).get_data(as_text=True)

    assert "Instagram could not complete the request." in html

    with app.app_context():
        assert SocialAccount.query.count() == 0
        assert Post.query.count() == 0


# ---------------------------------------------------------
# Sync and disconnect
# ---------------------------------------------------------

def test_sync_updates_a_connected_account(client, app, configured, http):
    with app.app_context():
        account_id = connected_account(configured, datetime.now() + timedelta(days=50))

    mock_profile_and_posts(http, likes=250)

    html = client.post(f"/instagram/accounts/{account_id}/sync", follow_redirects=True).get_data(as_text=True)

    assert "Imported 2 new posts and updated 0 from @samir.creates." in html
    assert {"access_token": ["stored-token"]}.items() <= parse_qs(urlparse(http.calls[0].request.url).query).items()

    with app.app_context():
        assert {post.likes for post in Post.query.all()} == {250}


def test_sync_with_expired_token_asks_to_reconnect(client, app, configured, http):
    with app.app_context():
        account_id = connected_account(configured, datetime.now() - timedelta(days=1))

    html = client.post(f"/instagram/accounts/{account_id}/sync", follow_redirects=True).get_data(as_text=True)

    assert "Instagram access expired. Reconnect the account." in html
    assert len(http.calls) == 0


def test_sync_rejects_non_instagram_accounts(client, app, configured):
    with app.app_context():
        account = SocialAccount(platform="YouTube", account_name="x", external_account_id="UC1")
        db.session.add(account)
        db.session.commit()
        account_id = account.id

    assert client.post(f"/instagram/accounts/{account_id}/sync").status_code == 404
    assert client.post("/instagram/accounts/9999/sync").status_code == 404


def test_disconnect_forgets_token_but_keeps_posts(client, app, configured):
    with app.app_context():
        account_id = connected_account(configured, datetime.now() + timedelta(days=50))
        db.session.add(
            Post(
                platform="Instagram",
                caption="Kept post",
                content_type="Image",
                posted_at=datetime(2026, 10, 1),
                source="instagram_api",
                external_id="1",
                social_account_id=account_id,
            )
        )
        db.session.commit()

    html = client.post(f"/instagram/accounts/{account_id}/disconnect", follow_redirects=True).get_data(as_text=True)

    assert "Disconnected @samir.creates. Imported posts were kept." in html
    assert "Reconnect" in html

    with app.app_context():
        account = db.session.get(SocialAccount, account_id)
        assert account.encrypted_access_token is None
        assert Post.query.count() == 1


# ---------------------------------------------------------
# Pages
# ---------------------------------------------------------

def test_accounts_page_shows_connect_and_instagram_actions(client, app, configured):
    with app.app_context():
        account_id = connected_account(configured, datetime.now() + timedelta(days=50))

    html = page_text(client)

    assert "Connect Instagram" in html
    assert f"/instagram/accounts/{account_id}/sync" in html
    assert f"/instagram/accounts/{account_id}/disconnect" in html
    assert "account-platform-instagram" in html

    for form in re.findall(r'<form\b[^>]*method="POST".*?</form>', html, re.S):
        assert 'name="csrf_token"' in form


def test_dashboard_labels_instagram_posts(client, app):
    with app.app_context():
        db.session.add(
            Post(
                platform="Instagram",
                caption="Instagram Reel · Oct 12, 2026",
                content_type="Reel",
                posted_at=datetime(2026, 10, 12),
                source="instagram_api",
                external_id="1",
            )
        )
        db.session.commit()

    assert '<span class="badge badge-instagram"> Instagram API </span>' in page_text(client, "/")


def test_csp_allows_instagram_images():
    policy = SECURITY_HEADERS["Content-Security-Policy"]

    assert "https://*.cdninstagram.com" in policy
    assert "https://*.fbcdn.net" in policy


@pytest.mark.parametrize("path", ["/instagram/callback", "/instagram/callback/"])
def test_callback_accepts_both_slash_forms(client, configured, path):
    # Meta may save the redirect URI with a trailing slash.
    response = client.get(f"{path}?error=access_denied")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/accounts")

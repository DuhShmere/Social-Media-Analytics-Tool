"""TikTok connect, callback, sync, and disconnect routes.

TikTok's responses are simulated with `responses`, which fails any
request to a URL a test did not register, so nothing reaches TikTok.
"""

import re
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from extensions import db
from models import Post, SocialAccount
from routes.tiktok import STATE_SESSION_KEY
from security import SECURITY_HEADERS
from services.tiktok import (
    REVOKE_URL,
    TOKEN_URL,
    USER_INFO_URL,
    VIDEO_LIST_URL,
    TikTokTokens,
    store_tokens,
)
from token_vault import decrypt_token, generate_key


REDIRECT_URI = "https://analytics.example.com/tiktok/callback"
OK = {"code": "ok", "message": "", "log_id": "l"}


@pytest.fixture()
def configured(app, monkeypatch):
    key = generate_key()
    for name, value in {
        "TIKTOK_CLIENT_KEY": "aw-client-key",
        "TIKTOK_CLIENT_SECRET": "client-secret",
        "TIKTOK_REDIRECT_URI": REDIRECT_URI,
        "TOKEN_ENCRYPTION_KEY": key,
    }.items():
        monkeypatch.setitem(app.config, name, value)
    return key


@pytest.fixture()
def http():
    with responses.RequestsMock() as mocked:
        yield mocked


def mock_token(http, access="act.first", refresh="rft.first"):
    http.post(
        TOKEN_URL,
        json={
            "access_token": access,
            "expires_in": 86400,
            "open_id": "open-123",
            "refresh_expires_in": 31536000,
            "refresh_token": refresh,
            "scope": "user.info.basic,user.info.profile,video.list",
            "token_type": "Bearer",
        },
    )


def mock_profile_and_videos(http, views=5400):
    http.get(USER_INFO_URL, json={"data": {"user": {"open_id": "open-123", "display_name": "Samir", "username": "samir.makes"}}, "error": OK})
    http.post(
        VIDEO_LIST_URL,
        json={
            "data": {
                "videos": [
                    {
                        "id": video_id,
                        "title": f"Video {video_id}",
                        "create_time": 1761580800,
                        "cover_image_url": f"https://p16-sign-va.tiktokcdn.com/{video_id}.jpeg",
                        "share_url": f"https://www.tiktok.com/@samir.makes/video/{video_id}",
                        "view_count": views,
                        "like_count": 410,
                        "comment_count": 22,
                        "share_count": 15,
                    }
                    for video_id in ("1", "2")
                ],
                "has_more": False,
            },
            "error": OK,
        },
    )


def start_login(client, state="expected-state"):
    with client.session_transaction() as session:
        session[STATE_SESSION_KEY] = state


def connected_account(key, access_expires_at, refresh_expires_at=None):
    account = SocialAccount(
        platform="TikTok",
        account_name="Samir",
        account_handle="@samir.makes",
        external_account_id="open-123",
    )
    store_tokens(
        account,
        TikTokTokens(
            "act.stored",
            access_expires_at,
            "rft.stored",
            refresh_expires_at or datetime.now() + timedelta(days=300),
            "open-123",
        ),
        key,
    )
    db.session.add(account)
    db.session.commit()
    return account.id


def html_after(response):
    return response.get_data(as_text=True)


# ---------------------------------------------------------
# Connect and callback
# ---------------------------------------------------------

def test_connect_explains_missing_settings(client):
    html = html_after(client.get("/tiktok/connect", follow_redirects=True))

    assert "TikTok is not set up yet" in html
    assert "TIKTOK_CLIENT_KEY" in html


def test_connect_redirects_to_tiktok_with_a_state(client, configured):
    response = client.get("/tiktok/connect")
    location = urlparse(response.headers["Location"])
    params = {k: v[0] for k, v in parse_qs(location.query).items()}

    assert (location.netloc, location.path) == ("www.tiktok.com", "/v2/auth/authorize/")
    assert params["client_key"] == "aw-client-key"
    assert params["redirect_uri"] == REDIRECT_URI

    with client.session_transaction() as session:
        assert session[STATE_SESSION_KEY] == params["state"]


def test_callback_connects_account_and_imports_videos(client, app, configured, http):
    mock_token(http)
    mock_profile_and_videos(http)
    start_login(client)

    html = html_after(client.get("/tiktok/callback?code=code-1&scopes=video.list&state=expected-state", follow_redirects=True))

    assert "Connected @samir.makes. Imported 2 new videos and updated 0" in html

    with app.app_context():
        account = SocialAccount.query.one()
        assert decrypt_token(account.encrypted_access_token, configured) == "act.first"
        assert decrypt_token(account.encrypted_refresh_token, configured) == "rft.first"
        assert "rft.first" not in account.encrypted_refresh_token
        assert account.refresh_token_expires_at > datetime.now() + timedelta(days=364)
        assert Post.query.filter_by(source="tiktok_api").count() == 2


@pytest.mark.parametrize(
    ("session_state", "query"),
    [("expected-state", "code=c&state=forged"), (None, "code=c&state=x"), ("expected-state", "code=c")],
    ids=["mismatched", "no-login-started", "missing-state"],
)
def test_callback_rejects_unverified_state(client, app, configured, http, session_state, query):
    if session_state:
        start_login(client, session_state)

    html = html_after(client.get(f"/tiktok/callback?{query}", follow_redirects=True))

    assert "could not be verified" in html
    assert len(http.calls) == 0

    with app.app_context():
        assert SocialAccount.query.count() == 0


def test_state_cannot_be_reused(client, configured, http):
    mock_token(http)
    mock_profile_and_videos(http)
    start_login(client)
    client.get("/tiktok/callback?code=c&state=expected-state")

    html = html_after(client.get("/tiktok/callback?code=c&state=expected-state", follow_redirects=True))

    assert "could not be verified" in html


def test_cancelled_login_saves_nothing(client, app, configured, http):
    start_login(client)

    html = html_after(client.get("/tiktok/callback?error=access_denied&error_description=User+cancelled", follow_redirects=True))

    assert "TikTok connection was cancelled." in html

    with app.app_context():
        assert SocialAccount.query.count() == 0


def test_failed_code_exchange_saves_nothing(client, app, configured, http):
    http.post(TOKEN_URL, json={"error": "invalid_grant", "error_description": "Code expired", "log_id": "x"})
    start_login(client)

    html = html_after(client.get("/tiktok/callback?code=old&state=expected-state", follow_redirects=True))

    assert "TikTok access expired or was revoked" in html

    with app.app_context():
        assert SocialAccount.query.count() == 0


@pytest.mark.parametrize("path", ["/tiktok/callback", "/tiktok/callback/"])
def test_callback_accepts_both_slash_forms(client, configured, path):
    response = client.get(f"{path}?error=access_denied")

    assert response.status_code == 302


# ---------------------------------------------------------
# Sync and disconnect
# ---------------------------------------------------------

def test_sync_with_valid_access_token_skips_refresh(client, app, configured, http):
    with app.app_context():
        account_id = connected_account(configured, datetime.now() + timedelta(hours=12))

    mock_profile_and_videos(http, views=9000)

    html = html_after(client.post(f"/tiktok/accounts/{account_id}/sync", follow_redirects=True))

    assert "Imported 2 new videos and updated 0 from @samir.makes." in html
    assert http.calls[0].request.headers["Authorization"] == "Bearer act.stored"
    assert not any(call.request.url.startswith(TOKEN_URL) for call in http.calls)


def test_sync_refreshes_an_expired_access_token_first(client, app, configured, http):
    with app.app_context():
        account_id = connected_account(configured, datetime.now() - timedelta(hours=1))

    mock_token(http, access="act.refreshed", refresh="rft.rotated")
    mock_profile_and_videos(http)

    html = html_after(client.post(f"/tiktok/accounts/{account_id}/sync", follow_redirects=True))

    assert "Imported 2 new videos" in html
    assert http.calls[1].request.headers["Authorization"] == "Bearer act.refreshed"

    with app.app_context():
        account = db.session.get(SocialAccount, account_id)
        assert decrypt_token(account.encrypted_refresh_token, configured) == "rft.rotated"


def test_sync_with_expired_refresh_token_asks_to_reconnect(client, app, configured, http):
    with app.app_context():
        account_id = connected_account(
            configured,
            datetime.now() - timedelta(hours=1),
            refresh_expires_at=datetime.now() - timedelta(days=1),
        )

    html = html_after(client.post(f"/tiktok/accounts/{account_id}/sync", follow_redirects=True))

    assert "TikTok access expired. Reconnect the account." in html
    assert len(http.calls) == 0


def test_sync_rejects_other_platforms(client, app, configured):
    with app.app_context():
        account = SocialAccount(platform="Instagram", account_name="x", external_account_id="1")
        db.session.add(account)
        db.session.commit()
        account_id = account.id

    assert client.post(f"/tiktok/accounts/{account_id}/sync").status_code == 404


def test_disconnect_revokes_and_forgets_tokens_but_keeps_videos(client, app, configured, http):
    with app.app_context():
        account_id = connected_account(configured, datetime.now() + timedelta(hours=12))

    http.post(REVOKE_URL, body="")

    html = html_after(client.post(f"/tiktok/accounts/{account_id}/disconnect", follow_redirects=True))

    assert "Disconnected @samir.makes. Imported videos were kept." in html
    assert "could not be reached" not in html
    assert parse_qs(http.calls[0].request.body)["token"] == ["act.stored"]

    with app.app_context():
        account = db.session.get(SocialAccount, account_id)
        assert account.encrypted_access_token is None
        assert account.encrypted_refresh_token is None


def test_disconnect_still_forgets_tokens_when_revoke_fails(client, app, configured, http):
    with app.app_context():
        account_id = connected_account(configured, datetime.now() + timedelta(hours=12))

    http.post(REVOKE_URL, status=500, json={})

    html = html_after(client.post(f"/tiktok/accounts/{account_id}/disconnect", follow_redirects=True))

    assert "TikTok could not be reached to revoke access" in html

    with app.app_context():
        assert db.session.get(SocialAccount, account_id).encrypted_refresh_token is None


# ---------------------------------------------------------
# Pages
# ---------------------------------------------------------

def test_accounts_page_shows_tiktok_connect_and_actions(client, app, configured):
    with app.app_context():
        account_id = connected_account(configured, datetime.now() + timedelta(hours=12))

    html = html_after(client.get("/accounts"))

    assert "Connect TikTok" in html
    assert f"/tiktok/accounts/{account_id}/sync" in html
    assert "account-platform-tiktok" in html

    for form in re.findall(r'<form\b[^>]*method="POST".*?</form>', html, re.S):
        assert 'name="csrf_token"' in form


def test_disconnected_account_offers_reconnect(client, app):
    with app.app_context():
        db.session.add(SocialAccount(platform="TikTok", account_name="Samir", external_account_id="open-123"))
        db.session.commit()

    html = html_after(client.get("/accounts"))

    assert "Not connected. Imported videos are kept." in html
    assert "/tiktok/connect" in html


def test_dashboard_labels_tiktok_posts(client, app):
    with app.app_context():
        db.session.add(Post(platform="TikTok", caption="Video", content_type="Video", posted_at=datetime(2026, 10, 1), source="tiktok_api", external_id="1"))
        db.session.commit()

    assert '<span class="badge badge-tiktok"> TikTok API </span>' in html_after(client.get("/"))


def test_csp_allows_tiktok_images():
    assert "https://*.tiktokcdn.com" in SECURITY_HEADERS["Content-Security-Policy"]


# ---------------------------------------------------------
# Site verification file
# ---------------------------------------------------------

def test_site_verification_file_matches_tiktoks_exactly(client, app, monkeypatch):
    monkeypatch.setitem(app.config, "TIKTOK_SITE_VERIFICATION", "abc123TOKEN")

    response = client.get("/tiktokabc123TOKEN.txt")

    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    assert response.data == b"tiktok-developers-site-verification=abc123TOKEN"


@pytest.mark.parametrize("configured_token", [None, "abc123TOKEN"])
def test_other_verification_files_are_not_found(client, app, monkeypatch, configured_token):
    monkeypatch.setitem(app.config, "TIKTOK_SITE_VERIFICATION", configured_token)

    assert client.get("/tiktokSOMETHINGELSE.txt").status_code == 404

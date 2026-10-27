"""TikTok service tests.

Responses are shaped like the examples in TikTok's developer docs. The
`responses` library fails any request to a URL a test did not register,
so these tests can never reach the real TikTok API.
"""

import json
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
import requests
import responses

from extensions import db
from models import MetricSnapshot, Post, SocialAccount
from services import tiktok
from services.http import MAX_ATTEMPTS
from services.tiktok import (
    REVOKE_URL,
    TOKEN_URL,
    USER_INFO_URL,
    VIDEO_LIST_URL,
    TikTokClient,
    TikTokError,
    TikTokTokens,
    build_authorization_url,
    exchange_code_for_tokens,
    make_title,
    revoke_token,
    store_tokens,
    sync_tiktok_account,
    usable_token,
)
from token_vault import decrypt_token, generate_key


NOW = datetime(2026, 10, 27, 12, 0)
OK = {"code": "ok", "message": "", "log_id": "log-1"}


@pytest.fixture()
def http():
    with responses.RequestsMock() as mocked:
        yield mocked


def no_sleep(seconds):
    pass


def client(token="act.test-token"):
    return TikTokClient(token, session=requests.Session(), sleep=no_sleep)


def token_body(access="act.new", refresh="rft.new"):
    return {
        "access_token": access,
        "expires_in": 86400,
        "open_id": "open-123",
        "refresh_expires_in": 31536000,
        "refresh_token": refresh,
        "scope": "user.info.basic,user.info.profile,video.list",
        "token_type": "Bearer",
    }


def video(video_id, **overrides):
    item = {
        "id": video_id,
        "title": "",
        "video_description": "Making a 3-ingredient pasta #cooking",
        "create_time": 1761580800,  # 2025-10-27 16:00 UTC
        "cover_image_url": f"https://p16-sign-va.tiktokcdn.com/{video_id}.jpeg?x-expires=1",
        "share_url": f"https://www.tiktok.com/@samir/video/{video_id}",
        "view_count": 5400,
        "like_count": 410,
        "comment_count": 22,
        "share_count": 15,
    }
    item.update(overrides)
    return item


def form(call):
    return {k: v[0] for k, v in parse_qs(call.request.body).items()}


# ---------------------------------------------------------
# OAuth
# ---------------------------------------------------------

def test_authorization_url_requests_profile_and_video_scopes():
    url = build_authorization_url("aw-key", "https://example.com/tiktok/callback", "state-1")
    parts = urlparse(url)

    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://www.tiktok.com/v2/auth/authorize/"
    assert {k: v[0] for k, v in parse_qs(parts.query).items()} == {
        "client_key": "aw-key",
        "scope": "user.info.basic,user.info.profile,video.list",
        "response_type": "code",
        "redirect_uri": "https://example.com/tiktok/callback",
        "state": "state-1",
    }


def test_code_exchange_sends_documented_fields_and_returns_both_tokens(http):
    http.post(TOKEN_URL, json=token_body())

    tokens = exchange_code_for_tokens(requests.Session(), "aw-key", "secret", "https://e.com/cb", "code-1", NOW)

    assert tokens == TikTokTokens(
        access_token="act.new",
        access_expires_at=NOW + timedelta(hours=24),
        refresh_token="rft.new",
        refresh_expires_at=NOW + timedelta(days=365),
        open_id="open-123",
    )
    assert form(http.calls[0]) == {
        "client_key": "aw-key",
        "client_secret": "secret",
        "code": "code-1",
        "grant_type": "authorization_code",
        "redirect_uri": "https://e.com/cb",
    }


def test_oauth_error_sent_with_http_200_is_detected(http):
    # TikTok can report OAuth errors in a 200 response.
    http.post(TOKEN_URL, json={"error": "invalid_grant", "error_description": "Code expired", "log_id": "x"})

    with pytest.raises(TikTokError, match="expired or was revoked") as raised:
        exchange_code_for_tokens(requests.Session(), "k", "s", "https://e.com", "c", NOW)

    assert raised.value.needs_reconnect


@pytest.mark.parametrize("url_kind", ["code", "refresh"])
def test_token_requests_are_never_retried(http, url_kind):
    http.post(TOKEN_URL, status=503, json={})

    with pytest.raises(TikTokError, match="temporarily unavailable"):
        if url_kind == "code":
            exchange_code_for_tokens(requests.Session(), "k", "s", "https://e.com", "c", NOW)
        else:
            tiktok.refresh_tokens(requests.Session(), "k", "s", "rft.old", NOW)

    assert len(http.calls) == 1


def test_revoke_sends_documented_fields(http):
    http.post(REVOKE_URL, body="")

    revoke_token(requests.Session(), "aw-key", "secret", "act.token")

    assert form(http.calls[0]) == {"client_key": "aw-key", "client_secret": "secret", "token": "act.token"}


# ---------------------------------------------------------
# Stored tokens
# ---------------------------------------------------------

def account_with(key, access_expires_at, refresh_expires_at=NOW + timedelta(days=300)):
    account = SocialAccount(platform="TikTok", account_name="x", external_account_id="open-123")
    store_tokens(
        account,
        TikTokTokens("act.old", access_expires_at, "rft.old", refresh_expires_at, "open-123"),
        key,
    )
    return account


def test_valid_access_token_is_used_without_refreshing(http):
    key = generate_key()
    account = account_with(key, NOW + timedelta(hours=10))

    assert usable_token(requests.Session(), account, key, "k", "s", NOW) == "act.old"
    assert len(http.calls) == 0


def test_expired_access_token_is_refreshed_and_new_refresh_token_saved(http):
    key = generate_key()
    account = account_with(key, NOW - timedelta(hours=1))
    http.post(TOKEN_URL, json=token_body(access="act.fresh", refresh="rft.rotated"))

    token = usable_token(requests.Session(), account, key, "aw-key", "secret", NOW)

    assert token == "act.fresh"
    assert decrypt_token(account.encrypted_access_token, key) == "act.fresh"
    assert decrypt_token(account.encrypted_refresh_token, key) == "rft.rotated"
    assert account.access_token_expires_at == NOW + timedelta(hours=24)
    assert form(http.calls[0]) == {
        "client_key": "aw-key",
        "client_secret": "secret",
        "grant_type": "refresh_token",
        "refresh_token": "rft.old",
    }


def test_access_token_about_to_expire_is_refreshed(http):
    key = generate_key()
    account = account_with(key, NOW + timedelta(minutes=2))
    http.post(TOKEN_URL, json=token_body())

    assert usable_token(requests.Session(), account, key, "k", "s", NOW) == "act.new"


def test_refresh_without_a_new_refresh_token_keeps_the_old_one(http):
    key = generate_key()
    account = account_with(key, NOW - timedelta(hours=1))
    body = token_body()
    del body["refresh_token"]
    http.post(TOKEN_URL, json=body)

    usable_token(requests.Session(), account, key, "k", "s", NOW)

    assert decrypt_token(account.encrypted_refresh_token, key) == "rft.old"


@pytest.mark.parametrize("case", ["refresh-expired", "never-connected"])
def test_unusable_tokens_require_reconnect(http, case):
    key = generate_key()
    account = (
        account_with(key, NOW - timedelta(hours=1), refresh_expires_at=NOW - timedelta(days=1))
        if case == "refresh-expired"
        else SocialAccount(platform="TikTok", account_name="x", external_account_id="1")
    )

    with pytest.raises(TikTokError) as raised:
        usable_token(requests.Session(), account, key, "k", "s", NOW)

    assert raised.value.needs_reconnect
    assert len(http.calls) == 0


# ---------------------------------------------------------
# Reading data
# ---------------------------------------------------------

def test_fetch_profile_uses_bearer_token(http):
    http.get(USER_INFO_URL, json={"data": {"user": {"open_id": "open-123", "display_name": "Samir", "username": "samir.makes"}}, "error": OK})

    profile = client("act.abc").fetch_profile()

    assert (profile.open_id, profile.display_name, profile.username) == ("open-123", "Samir", "samir.makes")
    request = http.calls[0].request
    assert request.headers["Authorization"] == "Bearer act.abc"
    assert parse_qs(urlparse(request.url).query)["fields"] == ["open_id,display_name,username"]


def test_videos_are_paged_with_the_cursor_until_the_limit(http):
    http.post(VIDEO_LIST_URL, json={"data": {"videos": [video(str(i)) for i in range(20)], "cursor": 1700000000000, "has_more": True}, "error": OK})
    http.post(VIDEO_LIST_URL, json={"data": {"videos": [video(str(i)) for i in range(20, 40)], "cursor": 1600000000000, "has_more": True}, "error": OK})
    http.post(VIDEO_LIST_URL, json={"data": {"videos": [video(str(i)) for i in range(40, 50)], "cursor": 1500000000000, "has_more": True}, "error": OK})

    videos = client().fetch_recent_videos()

    assert len(videos) == 50
    bodies = [json.loads(call.request.body) for call in http.calls]
    assert bodies == [
        {"max_count": 20},
        {"max_count": 20, "cursor": 1700000000000},
        {"max_count": 10, "cursor": 1600000000000},
    ]
    assert "view_count" in parse_qs(urlparse(http.calls[0].request.url).query)["fields"][0]


def test_paging_stops_when_there_are_no_more_videos(http):
    http.post(VIDEO_LIST_URL, json={"data": {"videos": [video("1"), video("2")], "cursor": 0, "has_more": False}, "error": OK})

    assert [v.video_id for v in client().fetch_recent_videos()] == ["1", "2"]
    assert len(http.calls) == 1


def test_video_is_parsed_with_metrics_and_utc_time(http):
    http.post(VIDEO_LIST_URL, json={"data": {"videos": [video("7")], "has_more": False}, "error": OK})

    [parsed] = client().fetch_recent_videos()

    assert parsed.posted_at == datetime(2025, 10, 27, 16, 0)
    assert (parsed.views, parsed.likes, parsed.comments, parsed.shares) == (5400, 410, 22, 15)
    assert parsed.title == "Making a 3-ingredient pasta #cooking"


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"title": "My title", "video_description": "desc"}, "My title"),
        ({"title": "", "video_description": "line one\n\nline   two"}, "line one line two"),
        ({"title": "", "video_description": ""}, "TikTok video · Oct 27, 2025"),
        ({"title": "x" * 400}, "x" * 299 + "…"),
    ],
    ids=["title", "description-fallback", "empty", "too-long"],
)
def test_make_title(item, expected):
    assert make_title(item, datetime(2025, 10, 27)) == expected


# ---------------------------------------------------------
# Errors and retries
# ---------------------------------------------------------

def test_reading_videos_retries_temporary_failures(http):
    # The video list is a POST, but it only reads, so it is retried.
    http.post(VIDEO_LIST_URL, status=503, json={})
    http.post(VIDEO_LIST_URL, json={"data": {"videos": [video("1")], "has_more": False}, "error": OK})

    assert len(client().fetch_recent_videos()) == 1
    assert len(http.calls) == 2


def test_retries_stop_after_the_limit(http):
    for _ in range(MAX_ATTEMPTS):
        http.get(USER_INFO_URL, status=500, json={})

    with pytest.raises(TikTokError, match="temporarily unavailable"):
        client().fetch_profile()

    assert len(http.calls) == MAX_ATTEMPTS


@pytest.mark.parametrize(
    ("status", "code", "message", "reconnect"),
    [
        (401, "access_token_invalid", "expired or was revoked", True),
        (401, "scope_not_authorized", "did not grant the permissions", True),
        (400, "scope_permission_missed", "did not grant the permissions", True),
        (429, "rate_limit_exceeded", "rate limit", False),
        (400, "invalid_params", "could not complete the request", False),
    ],
)
def test_api_error_messages(http, status, code, message, reconnect):
    http.get(USER_INFO_URL, status=status, json={"data": {}, "error": {"code": code, "message": "x", "log_id": "y"}})

    with pytest.raises(TikTokError, match=message) as raised:
        client().fetch_profile()

    assert raised.value.needs_reconnect is reconnect


def test_api_error_sent_with_http_200_is_detected(http):
    http.get(USER_INFO_URL, json={"data": {}, "error": {"code": "access_token_invalid", "message": "x", "log_id": "y"}})

    with pytest.raises(TikTokError) as raised:
        client().fetch_profile()

    assert raised.value.needs_reconnect


# ---------------------------------------------------------
# Sync
# ---------------------------------------------------------

def mock_account(http, videos):
    http.get(USER_INFO_URL, json={"data": {"user": {"open_id": "open-123", "display_name": "Samir", "username": "samir.makes"}}, "error": OK})
    http.post(VIDEO_LIST_URL, json={"data": {"videos": videos, "has_more": False}, "error": OK})


def test_sync_creates_account_posts_and_snapshots(app, http):
    mock_account(http, [video("1"), video("2", title="Second video", view_count=900)])

    with app.app_context():
        result = sync_tiktok_account(client(), db.session, NOW)
        db.session.commit()

        account = SocialAccount.query.one()
        posts = Post.query.order_by(Post.external_id).all()

        assert (result.imported, result.updated, result.account_name) == (2, 0, "@samir.makes")
        assert (account.platform, account.account_name, account.account_handle) == ("TikTok", "Samir", "@samir.makes")
        assert [(p.caption, p.views, p.likes, p.comments, p.shares, p.saves) for p in posts] == [
            ("Making a 3-ingredient pasta #cooking", 5400, 410, 22, 15, 0),
            ("Second video", 900, 410, 22, 15, 0),
        ]
        assert all(p.source == "tiktok_api" and p.content_type == "Video" and not p.is_editable for p in posts)
        assert MetricSnapshot.query.count() == 2


def test_resync_updates_without_duplicates(app, http):
    mock_account(http, [video("1")])

    with app.app_context():
        sync_tiktok_account(client(), db.session, NOW)
        db.session.commit()

    http.reset()
    mock_account(http, [video("1", view_count=8000)])

    with app.app_context():
        result = sync_tiktok_account(client(), db.session, NOW + timedelta(days=1))
        db.session.commit()

        post = Post.query.one()
        assert (result.imported, result.updated, post.views) == (0, 1, 8000)
        assert SocialAccount.query.count() == 1
        assert MetricSnapshot.query.filter_by(post_id=post.id).count() == 2


def test_failed_fetch_writes_nothing(app, http):
    http.get(USER_INFO_URL, json={"data": {"user": {"open_id": "1", "display_name": "x"}}, "error": OK})
    http.post(VIDEO_LIST_URL, status=401, json={"data": {}, "error": {"code": "access_token_invalid"}})

    with app.app_context():
        with pytest.raises(TikTokError):
            sync_tiktok_account(client(), db.session, NOW)

        assert not db.session.new
        assert SocialAccount.query.count() == 0

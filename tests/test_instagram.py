"""Instagram service tests.

Responses are shaped like the examples in Meta's Instagram Platform docs.
The `responses` library fails any request to a URL a test did not
register, so these tests can never reach the real Instagram API.
"""

from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
import requests
import responses

from extensions import db
from models import MetricSnapshot, Post, SocialAccount
from services import instagram
from services.instagram import (
    GRAPH_URL,
    SHORT_LIVED_TOKEN_URL,
    InstagramClient,
    InstagramError,
    build_authorization_url,
    clean_authorization_code,
    exchange_code_for_token,
    exchange_for_long_lived_token,
    store_token,
    sync_instagram_account,
    usable_token,
)
from token_vault import decrypt_token, generate_key


NOW = datetime(2026, 10, 26, 12, 0)
API = f"{GRAPH_URL}/{instagram.API_VERSION}"


@pytest.fixture()
def http():
    with responses.RequestsMock() as mocked:
        yield mocked


def no_sleep(seconds):
    pass


def client(token="IGAA-test-token"):
    return InstagramClient(token, session=requests.Session(), sleep=no_sleep)


def media_item(media_id, media_type="IMAGE", **overrides):
    item = {
        "id": media_id,
        "media_type": media_type,
        "media_url": f"https://scontent.cdninstagram.com/{media_id}.jpg",
        "permalink": f"https://www.instagram.com/p/{media_id}/",
        "timestamp": "2026-10-12T18:05:00+0000",
        "like_count": 120,
        "comments_count": 8,
    }
    item.update(overrides)
    return item


def insights_body(views=1500, shares=12, saved=30):
    return {
        "data": [
            {"name": name, "period": "lifetime", "values": [{"value": value}]}
            for name, value in (("views", views), ("shares", shares), ("saved", saved))
        ]
    }


def query(call):
    return {key: values[0] for key, values in parse_qs(urlparse(call.request.url).query).items()}


# ---------------------------------------------------------
# OAuth
# ---------------------------------------------------------

def test_authorization_url_requests_insights_permission():
    url = build_authorization_url("123", "https://example.com/instagram/callback", "state-abc")
    parts = urlparse(url)
    params = {key: values[0] for key, values in parse_qs(parts.query).items()}

    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://www.instagram.com/oauth/authorize"
    assert params == {
        "client_id": "123",
        "redirect_uri": "https://example.com/instagram/callback",
        "response_type": "code",
        "scope": "instagram_business_basic,instagram_business_manage_insights",
        "state": "state-abc",
    }


def test_trailing_hash_is_removed_from_the_code():
    assert clean_authorization_code("AQBx-code#_") == "AQBx-code"
    assert clean_authorization_code("AQBx-code") == "AQBx-code"


@pytest.mark.parametrize(
    "body",
    [
        {"data": [{"access_token": "short-token", "user_id": 17841400000, "permissions": "instagram_business_basic"}]},
        {"access_token": "short-token", "user_id": 17841400000},
    ],
    ids=["documented-shape", "older-flat-shape"],
)
def test_code_exchange_sends_documented_fields(http, body):
    http.post(SHORT_LIVED_TOKEN_URL, json=body)

    token, user_id = exchange_code_for_token(
        requests.Session(), "123", "app-secret", "https://example.com/cb", "one-time-code#_"
    )

    assert (token, user_id) == ("short-token", "17841400000")
    sent = parse_qs(http.calls[0].request.body)
    assert {key: values[0] for key, values in sent.items()} == {
        "client_id": "123",
        "client_secret": "app-secret",
        "grant_type": "authorization_code",
        "redirect_uri": "https://example.com/cb",
        "code": "one-time-code",
    }


def test_code_exchange_is_never_retried(http):
    # The code is single-use, so a retry could never succeed.
    http.post(SHORT_LIVED_TOKEN_URL, status=503, json={})

    with pytest.raises(InstagramError, match="temporarily unavailable"):
        exchange_code_for_token(requests.Session(), "1", "s", "https://e.com", "c")

    assert len(http.calls) == 1


def test_long_lived_exchange_returns_token_and_expiry(http):
    http.get(
        f"{GRAPH_URL}/access_token",
        json={"access_token": "long-token", "token_type": "bearer", "expires_in": 5183944},
    )

    token, expires_at = exchange_for_long_lived_token(requests.Session(), "app-secret", "short-token", NOW)

    assert token == "long-token"
    assert expires_at == NOW + timedelta(seconds=5183944)
    assert query(http.calls[0]) == {
        "grant_type": "ig_exchange_token",
        "client_secret": "app-secret",
        "access_token": "short-token",
    }


# ---------------------------------------------------------
# Reading data
# ---------------------------------------------------------

def test_fetch_profile(http):
    http.get(f"{API}/me", json={"user_id": "17841400000", "username": "samir.creates", "id": "999"})

    profile = client().fetch_profile()

    assert (profile.user_id, profile.username) == ("17841400000", "samir.creates")
    assert query(http.calls[0])["fields"] == "user_id,username"


def test_posts_are_parsed_by_media_type(http):
    http.get(
        f"{API}/me/media",
        json={
            "data": [
                media_item("1", "IMAGE"),
                media_item("2", "VIDEO", thumbnail_url="https://scontent.cdninstagram.com/2-thumb.jpg"),
                media_item("3", "CAROUSEL_ALBUM"),
            ]
        },
    )

    image, reel, carousel = client().fetch_recent_posts()

    assert (image.content_type, reel.content_type, carousel.content_type) == ("Image", "Reel", "Carousel")
    assert image.title == "Instagram Image · Oct 12, 2026"
    assert image.posted_at == datetime(2026, 10, 12, 18, 5)
    assert image.thumbnail_url.endswith("/1.jpg")
    # A video's media_url is the video file, so its thumbnail is used.
    assert reel.thumbnail_url.endswith("/2-thumb.jpg")
    assert (image.likes, image.comments) == (120, 8)


def test_hidden_like_counts_become_zero(http):
    item = media_item("1")
    del item["like_count"]
    http.get(f"{API}/me/media", json={"data": [item]})

    [post] = client().fetch_recent_posts()

    assert post.likes == 0


def test_pagination_stops_at_the_limit(http):
    next_url = f"{API}/me/media?after=cursor1&access_token=IGAA-test-token"
    http.get(f"{API}/me/media", json={"data": [media_item(str(i)) for i in range(3)], "paging": {"next": next_url}})
    http.get(next_url, json={"data": [media_item(str(i)) for i in range(3, 6)], "paging": {"next": f"{API}/me/media?after=cursor2"}})

    posts = client().fetch_recent_posts(limit=5)

    assert [post.media_id for post in posts] == ["0", "1", "2", "3", "4"]
    assert len(http.calls) == 2


def test_insights_are_parsed(http):
    http.get(f"{API}/111/insights", json=insights_body(views=1500, shares=12, saved=30))

    assert client().fetch_insights("111") == {"views": 1500, "shares": 12, "saves": 30}
    assert query(http.calls[0])["metric"] == "views,shares,saved"


def test_unavailable_insights_do_not_stop_the_sync(http):
    http.get(f"{API}/111/insights", status=400, json={"error": {"code": 100, "message": "Unsupported"}})

    assert client().fetch_insights("111") is None


def test_revoked_token_during_insights_requires_reconnect(http):
    http.get(f"{API}/111/insights", status=400, json={"error": {"code": 190, "message": "Session expired"}})

    with pytest.raises(InstagramError) as raised:
        client().fetch_insights("111")

    assert raised.value.needs_reconnect


# ---------------------------------------------------------
# Errors and retries
# ---------------------------------------------------------

def test_temporary_failures_are_retried_then_succeed(http):
    http.get(f"{API}/me", status=503, json={})
    http.get(f"{API}/me", json={"user_id": "1", "username": "x"})
    waits = []

    profile = InstagramClient("t", session=requests.Session(), sleep=waits.append).fetch_profile()

    assert profile.username == "x"
    assert waits == [1]


def test_retries_stop_after_the_limit(http):
    for _ in range(instagram.MAX_ATTEMPTS):
        http.get(f"{API}/me", status=500, json={})

    with pytest.raises(InstagramError, match="temporarily unavailable"):
        client().fetch_profile()

    assert len(http.calls) == instagram.MAX_ATTEMPTS


def test_dropped_connections_are_retried_then_reported(http):
    for _ in range(instagram.MAX_ATTEMPTS):
        http.get(f"{API}/me", body=requests.ConnectionError("reset"))

    with pytest.raises(InstagramError, match="Could not reach Instagram"):
        client().fetch_profile()

    assert len(http.calls) == instagram.MAX_ATTEMPTS


@pytest.mark.parametrize(
    ("status", "code", "message", "reconnect"),
    [
        (400, 190, "expired or was revoked", True),
        (403, 10, "did not grant the permissions", True),
        (400, 4, "rate limit", False),
        (400, 100, "could not complete the request", False),
    ],
    ids=["expired-token", "missing-permission", "rate-limit", "other"],
)
def test_error_messages(http, status, code, message, reconnect):
    http.get(f"{API}/me", status=status, json={"error": {"code": code, "message": "x"}})

    with pytest.raises(InstagramError, match=message) as raised:
        client().fetch_profile()

    assert raised.value.needs_reconnect is reconnect
    assert len(http.calls) == 1


# ---------------------------------------------------------
# Stored tokens
# ---------------------------------------------------------

def account_with_token(key, expires_at, token="old-token"):
    account = SocialAccount(platform="Instagram", account_name="x", external_account_id="1")
    store_token(account, token, expires_at, key)
    return account


def test_fresh_token_is_used_without_refreshing(http):
    key = generate_key()
    account = account_with_token(key, NOW + timedelta(days=50))

    assert usable_token(requests.Session(), account, key, NOW) == "old-token"
    assert len(http.calls) == 0


def test_token_close_to_expiry_is_refreshed_and_saved(http):
    key = generate_key()
    account = account_with_token(key, NOW + timedelta(days=10))
    http.get(f"{GRAPH_URL}/refresh_access_token", json={"access_token": "new-token", "expires_in": 5184000})

    token = usable_token(requests.Session(), account, key, NOW)

    assert token == "new-token"
    assert decrypt_token(account.encrypted_access_token, key) == "new-token"
    assert account.access_token_expires_at == NOW + timedelta(seconds=5184000)
    assert query(http.calls[0]) == {"grant_type": "ig_refresh_token", "access_token": "old-token"}


def test_failed_refresh_keeps_a_still_valid_token(http):
    key = generate_key()
    account = account_with_token(key, NOW + timedelta(days=10))
    http.get(f"{GRAPH_URL}/refresh_access_token", status=400, json={"error": {"code": 4}})

    assert usable_token(requests.Session(), account, key, NOW) == "old-token"


@pytest.mark.parametrize(
    "expires_at",
    [NOW - timedelta(minutes=1), None],
    ids=["expired", "never-connected"],
)
def test_unusable_tokens_require_reconnect(expires_at):
    key = generate_key()
    account = (
        account_with_token(key, expires_at)
        if expires_at
        else SocialAccount(platform="Instagram", account_name="x", external_account_id="1")
    )

    with pytest.raises(InstagramError) as raised:
        usable_token(requests.Session(), account, key, NOW)

    assert raised.value.needs_reconnect


# ---------------------------------------------------------
# Sync
# ---------------------------------------------------------

def mock_account(http, posts, insights_status=200):
    http.get(f"{API}/me", json={"user_id": "17841400000", "username": "samir.creates"})
    http.get(f"{API}/me/media", json={"data": posts})

    for post in posts:
        if insights_status == 200:
            http.get(f"{API}/{post['id']}/insights", json=insights_body())
        else:
            http.get(f"{API}/{post['id']}/insights", status=insights_status, json={"error": {"code": 100}})


def test_sync_creates_account_posts_and_snapshots(app, http):
    mock_account(http, [media_item("1", "IMAGE"), media_item("2", "VIDEO")])

    with app.app_context():
        result = sync_instagram_account(client(), db.session, NOW)
        db.session.commit()

        account = SocialAccount.query.one()
        posts = Post.query.order_by(Post.external_id).all()

        assert (result.imported, result.updated, result.missing_insights) == (2, 0, 0)
        assert (account.platform, account.account_handle) == ("Instagram", "@samir.creates")
        assert account.last_synced_at == NOW
        assert [(p.content_type, p.views, p.likes, p.comments, p.shares, p.saves) for p in posts] == [
            ("Image", 1500, 120, 8, 12, 30),
            ("Reel", 1500, 120, 8, 12, 30),
        ]
        assert all(p.source == "instagram_api" and not p.is_editable for p in posts)
        assert MetricSnapshot.query.count() == 2


def test_resync_updates_posts_without_duplicates(app, http):
    mock_account(http, [media_item("1")])

    with app.app_context():
        sync_instagram_account(client(), db.session, NOW)
        db.session.commit()

    http.reset()
    mock_account(http, [media_item("1", like_count=200)])

    with app.app_context():
        result = sync_instagram_account(client(), db.session, NOW + timedelta(days=1))
        db.session.commit()

        post = Post.query.one()
        assert (result.imported, result.updated) == (0, 1)
        assert post.likes == 200
        assert SocialAccount.query.count() == 1
        assert MetricSnapshot.query.filter_by(post_id=post.id).count() == 2


def test_posts_without_insights_are_still_saved(app, http):
    mock_account(http, [media_item("1")], insights_status=400)

    with app.app_context():
        result = sync_instagram_account(client(), db.session, NOW)

        post = Post.query.one()
        assert result.missing_insights == 1
        assert (post.views, post.likes, post.comments) == (0, 120, 8)


def test_failed_fetch_writes_nothing(app, http):
    http.get(f"{API}/me", json={"user_id": "1", "username": "x"})
    http.get(f"{API}/me/media", status=400, json={"error": {"code": 190}})

    with app.app_context():
        with pytest.raises(InstagramError):
            sync_instagram_account(client(), db.session, NOW)

        assert not db.session.new
        assert SocialAccount.query.count() == 0

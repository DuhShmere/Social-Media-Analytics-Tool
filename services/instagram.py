"""Instagram API with Instagram Login.

Three parts:

- OAuth helpers turn the code from Instagram's login page into a
  long-lived (60-day) access token, and refresh it before it expires.
- InstagramClient only talks to Instagram and returns plain data.
- sync_instagram_account() saves that data. Like the YouTube sync, it
  fetches everything first and only then writes, and it adds to the
  database session without committing; the caller owns the transaction.

Docs: https://developers.facebook.com/docs/instagram-platform
"""

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests

from models import Post, SocialAccount, record_metric_snapshot
from token_vault import decrypt_token, encrypt_token


PLATFORM = "Instagram"
SOURCE = "instagram_api"

AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
SHORT_LIVED_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
GRAPH_URL = "https://graph.instagram.com"
API_VERSION = "v26.0"

# instagram_business_manage_insights is needed for views, shares, and saves.
SCOPES = ("instagram_business_basic", "instagram_business_manage_insights")

# caption and media_product_type are documented as Facebook Login only,
# so titles are built from the media type and date instead.
MEDIA_FIELDS = (
    "id,media_type,media_url,thumbnail_url,permalink,timestamp,"
    "like_count,comments_count"
)
INSIGHT_METRICS = ("views", "shares", "saved")

MAX_POSTS = 50
PAGE_SIZE = 25
REQUEST_TIMEOUT_SECONDS = 15

# Only GET requests are retried, and only for temporary failures. The
# token exchange is a POST with a one-time code, so it is never retried.
MAX_ATTEMPTS = 3
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}

# Long-lived tokens last 60 days. Refreshing is allowed once a token is
# at least a day old, so refresh whenever fewer than 30 days remain.
REFRESH_WHEN_DAYS_LEFT = 30

CONTENT_TYPES = {
    "IMAGE": "Image",
    "VIDEO": "Reel",  # Instagram publishes new videos as Reels.
    "CAROUSEL_ALBUM": "Carousel",
}

# Graph API error codes, from Meta's error-handling guide.
EXPIRED_TOKEN_CODE = 190
PERMISSION_CODES = {10, 200}
RATE_LIMIT_CODES = {4, 17, 32, 613}


class InstagramError(Exception):
    """An Instagram problem with a message that is safe to show the user.

    needs_reconnect is True when the stored token can no longer be used
    and the user must log in with Instagram again.
    """

    def __init__(self, message, needs_reconnect=False):
        super().__init__(message)
        self.needs_reconnect = needs_reconnect


@dataclass(frozen=True)
class InstagramProfile:
    user_id: str
    username: str


@dataclass(frozen=True)
class InstagramPost:
    media_id: str
    title: str
    content_type: str
    posted_at: datetime
    likes: int
    comments: int
    permalink: str | None
    thumbnail_url: str | None


@dataclass(frozen=True)
class InstagramSyncResult:
    account_name: str
    imported: int
    updated: int
    missing_insights: int


# ---------------------------------------------------------
# HTTP
# ---------------------------------------------------------

def describe_error(status_code, body):
    """Turn an Instagram error response into an InstagramError."""
    error = body.get("error") if isinstance(body, dict) else None
    error = error if isinstance(error, dict) else {}
    code = error.get("code", body.get("code") if isinstance(body, dict) else None)

    if code == EXPIRED_TOKEN_CODE or status_code == 401:
        return InstagramError(
            "Instagram access expired or was revoked. Reconnect the account.",
            needs_reconnect=True,
        )

    if code in PERMISSION_CODES:
        return InstagramError(
            "Instagram did not grant the permissions this app needs. "
            "Reconnect the account and approve every permission.",
            needs_reconnect=True,
        )

    if code in RATE_LIMIT_CODES or status_code == 429:
        return InstagramError(
            "Instagram's rate limit was reached. Try again in a few minutes."
        )

    if status_code >= 500:
        return InstagramError(
            "Instagram is temporarily unavailable. Try again in a few minutes."
        )

    return InstagramError("Instagram could not complete the request.")


def _parse_json(response):
    try:
        return response.json()
    except ValueError:
        return {}


def request_json(session, method, url, sleep=time.sleep, **kwargs):
    attempts = MAX_ATTEMPTS if method == "GET" else 1

    for attempt in range(1, attempts + 1):
        try:
            response = session.request(
                method,
                url,
                timeout=REQUEST_TIMEOUT_SECONDS,
                **kwargs,
            )
        except (requests.ConnectionError, requests.Timeout):
            if attempt < attempts:
                sleep(2 ** (attempt - 1))
                continue

            raise InstagramError(
                "Could not reach Instagram. Check your internet connection "
                "and try again."
            ) from None

        if response.status_code in RETRY_STATUS_CODES and attempt < attempts:
            sleep(2 ** (attempt - 1))
            continue

        body = _parse_json(response)

        if not response.ok:
            raise describe_error(response.status_code, body)

        return body

    raise InstagramError("Instagram could not complete the request.")


# ---------------------------------------------------------
# OAuth
# ---------------------------------------------------------

def build_authorization_url(app_id, redirect_uri, state):
    query = urlencode(
        {
            "client_id": app_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": ",".join(SCOPES),
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def clean_authorization_code(code):
    # Instagram appends "#_" to the redirect; it is not part of the code.
    return (code or "").strip().removesuffix("#_")


def _expiry(body, now):
    return now + timedelta(seconds=int(body.get("expires_in", 0)))


def exchange_code_for_token(session, app_id, app_secret, redirect_uri, code):
    """Trade the one-time login code for a short-lived (1 hour) token."""
    body = request_json(
        session,
        "POST",
        SHORT_LIVED_TOKEN_URL,
        data={
            "client_id": app_id,
            "client_secret": app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code": clean_authorization_code(code),
        },
    )

    # Documented as {"data": [{...}]}; older responses were flat.
    if isinstance(body.get("data"), list) and body["data"]:
        body = body["data"][0]

    if not body.get("access_token"):
        raise InstagramError("Instagram did not return an access token.")

    return body["access_token"], str(body.get("user_id", ""))


def exchange_for_long_lived_token(session, app_secret, short_lived_token, now):
    body = request_json(
        session,
        "GET",
        f"{GRAPH_URL}/access_token",
        params={
            "grant_type": "ig_exchange_token",
            "client_secret": app_secret,
            "access_token": short_lived_token,
        },
    )
    return body["access_token"], _expiry(body, now)


def refresh_long_lived_token(session, token, now):
    body = request_json(
        session,
        "GET",
        f"{GRAPH_URL}/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": token},
    )
    return body["access_token"], _expiry(body, now)


# ---------------------------------------------------------
# Stored tokens
# ---------------------------------------------------------

def store_token(account, token, expires_at, encryption_key):
    account.encrypted_access_token = encrypt_token(token, encryption_key)
    account.access_token_expires_at = expires_at


def usable_token(session, account, encryption_key, now):
    """Return a working token, refreshing it when it is close to expiring.

    A failed refresh is not fatal while the current token still works.
    """
    if not account.encrypted_access_token:
        raise InstagramError(
            "This Instagram account is not connected. Connect it again.",
            needs_reconnect=True,
        )

    token = decrypt_token(account.encrypted_access_token, encryption_key)
    expires_at = account.access_token_expires_at

    if expires_at is not None and expires_at <= now:
        raise InstagramError(
            "Instagram access expired. Reconnect the account.",
            needs_reconnect=True,
        )

    if expires_at is not None and expires_at - now < timedelta(
        days=REFRESH_WHEN_DAYS_LEFT
    ):
        try:
            token, new_expiry = refresh_long_lived_token(session, token, now)
        except InstagramError as error:
            if error.needs_reconnect:
                raise
        else:
            store_token(account, token, new_expiry, encryption_key)

    return token


# ---------------------------------------------------------
# Reading data
# ---------------------------------------------------------

def parse_timestamp(value):
    # Instagram uses "2026-09-26T22:36:43+0000"; store naive UTC.
    return (
        datetime.fromisoformat(value)
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )


def parse_media(item):
    media_type = item.get("media_type", "")
    content_type = CONTENT_TYPES.get(media_type, "Post")
    posted_at = parse_timestamp(item["timestamp"])

    # media_url is the video file for VIDEO, so use the thumbnail there.
    thumbnail = (
        item.get("thumbnail_url")
        if media_type == "VIDEO"
        else item.get("media_url")
    )

    return InstagramPost(
        media_id=str(item["id"]),
        title=f"Instagram {content_type} · {posted_at.strftime('%b %d, %Y')}",
        content_type=content_type,
        posted_at=posted_at,
        # Hidden like counts are omitted rather than returned as zero.
        likes=int(item.get("like_count", 0)),
        comments=int(item.get("comments_count", 0)),
        permalink=item.get("permalink"),
        thumbnail_url=thumbnail,
    )


def parse_insights(body):
    values = {}

    for metric in body.get("data", []):
        metric_values = metric.get("values") or [{}]
        values[metric.get("name")] = int(metric_values[0].get("value", 0))

    return {
        "views": values.get("views", 0),
        "shares": values.get("shares", 0),
        "saves": values.get("saved", 0),
    }


class InstagramClient:
    def __init__(self, access_token, session=None, sleep=time.sleep):
        self._token = access_token
        self._session = session or requests.Session()
        self._sleep = sleep

    def _get(self, path_or_url, params=None):
        url = (
            path_or_url
            if path_or_url.startswith("https://")
            else f"{GRAPH_URL}/{API_VERSION}/{path_or_url}"
        )
        params = dict(params or {})

        # Pagination "next" links already carry the token.
        if "access_token=" not in url:
            params["access_token"] = self._token

        return request_json(
            self._session,
            "GET",
            url,
            sleep=self._sleep,
            params=params,
        )

    def fetch_profile(self):
        body = self._get("me", {"fields": "user_id,username"})
        return InstagramProfile(
            user_id=str(body.get("user_id") or body.get("id")),
            username=body.get("username", ""),
        )

    def fetch_recent_posts(self, limit=MAX_POSTS):
        posts = []
        body = self._get(
            "me/media",
            {"fields": MEDIA_FIELDS, "limit": PAGE_SIZE},
        )

        while True:
            posts.extend(parse_media(item) for item in body.get("data", []))
            next_url = (body.get("paging") or {}).get("next")

            if len(posts) >= limit or not next_url:
                return posts[:limit]

            body = self._get(next_url)

    def fetch_insights(self, media_id):
        """Return views, shares, and saves, or None if unavailable.

        Some media (for example very new or very old posts) have no
        insights; that should not stop the rest of the sync.
        """
        try:
            body = self._get(
                f"{media_id}/insights",
                {"metric": ",".join(INSIGHT_METRICS)},
            )
        except InstagramError as error:
            if error.needs_reconnect:
                raise
            return None

        return parse_insights(body)


# ---------------------------------------------------------
# Saving data
# ---------------------------------------------------------

def sync_instagram_account(client, session, synced_at, account=None):
    """Fetch the profile, recent posts, and their insights, then save.

    Pass account to sync an existing connection. Without it, the account
    is found by its Instagram user ID or created.
    """
    profile = client.fetch_profile()
    posts = client.fetch_recent_posts()
    insights = {post.media_id: client.fetch_insights(post.media_id) for post in posts}

    if account is None:
        account = SocialAccount.query.filter_by(
            platform=PLATFORM,
            external_account_id=profile.user_id,
        ).first()

    if account is None:
        account = SocialAccount(
            platform=PLATFORM,
            external_account_id=profile.user_id,
        )
        session.add(account)

    account.account_name = profile.username
    account.account_handle = f"@{profile.username}"
    account.last_synced_at = synced_at

    imported = updated = missing = 0

    for item in posts:
        post = Post.query.filter_by(
            platform=PLATFORM,
            external_id=item.media_id,
        ).first()

        if post is None:
            post = Post(platform=PLATFORM, external_id=item.media_id)
            session.add(post)
            imported += 1
        else:
            updated += 1

        metrics = insights[item.media_id]

        if metrics is None:
            missing += 1
            metrics = {"views": 0, "shares": 0, "saves": 0}

        post.caption = item.title
        post.content_type = item.content_type
        post.posted_at = item.posted_at
        post.views = metrics["views"]
        post.likes = item.likes
        post.comments = item.comments
        post.shares = metrics["shares"]
        post.saves = metrics["saves"]
        post.external_url = item.permalink
        post.thumbnail_url = item.thumbnail_url
        post.source = SOURCE
        post.social_account = account

        record_metric_snapshot(post, captured_at=synced_at)

    return InstagramSyncResult(
        account_name=profile.username,
        imported=imported,
        updated=updated,
        missing_insights=missing,
    )

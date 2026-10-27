"""TikTok Login Kit and Display API.

Three parts, mirroring services/instagram.py:

- OAuth helpers turn the code from TikTok's login page into tokens. The
  access token lasts 24 hours and the refresh token 365 days, so both
  are stored (encrypted) and the access token is refreshed as needed.
- TikTokClient only talks to TikTok and returns plain data.
- sync_tiktok_account() fetches everything first and only then writes,
  adding to the database session without committing.

Docs: https://developers.tiktok.com/doc/login-kit-web
"""

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests

from models import Post, SocialAccount, record_metric_snapshot
from services import http
from token_vault import decrypt_token, encrypt_token


PLATFORM = "TikTok"
SOURCE = "tiktok_api"

AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
REVOKE_URL = "https://open.tiktokapis.com/v2/oauth/revoke/"
USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"
VIDEO_LIST_URL = "https://open.tiktokapis.com/v2/video/list/"

# user.info.profile is needed for the @username.
SCOPES = ("user.info.basic", "user.info.profile", "video.list")
USER_FIELDS = "open_id,display_name,username"
VIDEO_FIELDS = (
    "id,title,video_description,create_time,cover_image_url,share_url,"
    "view_count,like_count,comment_count,share_count"
)

MAX_VIDEOS = 50
PAGE_SIZE = 20  # The most TikTok returns per page.
CAPTION_LIMIT = 300  # Post.caption column length.

# Refresh the 24-hour access token if it has less than this left.
ACCESS_TOKEN_MARGIN = timedelta(minutes=5)

# Error codes from TikTok's API error-handling guide.
TOKEN_INVALID_CODES = {"access_token_invalid"}
REAUTHORIZE_CODES = {"scope_not_authorized", "scope_permission_missed"}
OAUTH_EXPIRED_CODES = {"invalid_grant"}
RATE_LIMIT_CODES = {"rate_limit_exceeded"}


class TikTokError(Exception):
    """A TikTok problem with a message that is safe to show the user.

    needs_reconnect is True when the user must log in with TikTok again.
    """

    def __init__(self, message, needs_reconnect=False):
        super().__init__(message)
        self.needs_reconnect = needs_reconnect


@dataclass(frozen=True)
class TikTokTokens:
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime
    open_id: str


@dataclass(frozen=True)
class TikTokProfile:
    open_id: str
    display_name: str
    username: str


@dataclass(frozen=True)
class TikTokVideo:
    video_id: str
    title: str
    posted_at: datetime
    views: int
    likes: int
    comments: int
    shares: int
    share_url: str | None
    thumbnail_url: str | None


@dataclass(frozen=True)
class TikTokSyncResult:
    account: SocialAccount
    account_name: str
    imported: int
    updated: int


# ---------------------------------------------------------
# HTTP
# ---------------------------------------------------------

def _error_code(body):
    if not isinstance(body, dict):
        return None

    error = body.get("error")

    # Data endpoints: {"error": {"code": "...", "message": "..."}}.
    if isinstance(error, dict):
        return error.get("code")

    # OAuth endpoints: {"error": "invalid_grant", "error_description": ...}.
    return error or None


def describe_error(status_code, body):
    code = _error_code(body)

    if code in REAUTHORIZE_CODES:
        return TikTokError(
            "TikTok did not grant the permissions this app needs. "
            "Reconnect the account and approve every permission.",
            needs_reconnect=True,
        )

    if code in TOKEN_INVALID_CODES or code in OAUTH_EXPIRED_CODES or status_code == 401:
        return TikTokError(
            "TikTok access expired or was revoked. Reconnect the account.",
            needs_reconnect=True,
        )

    if code in RATE_LIMIT_CODES or status_code == 429:
        return TikTokError(
            "TikTok's rate limit was reached. Try again in a few minutes."
        )

    if status_code >= 500 or code == "internal_error":
        return TikTokError(
            "TikTok is temporarily unavailable. Try again in a few minutes."
        )

    return TikTokError("TikTok could not complete the request.")


def _network_error():
    return TikTokError(
        "Could not reach TikTok. Check your internet connection and try again."
    )


def request_json(session, method, url, *, retry, sleep=time.sleep, **kwargs):
    body = http.request_json(
        session,
        method,
        url,
        describe_error=describe_error,
        network_error=_network_error,
        retry=retry,
        sleep=sleep,
        **kwargs,
    )

    # TikTok can report errors with HTTP 200; success has code "ok".
    code = _error_code(body)

    if code not in (None, "ok"):
        raise describe_error(200, body)

    return body


# ---------------------------------------------------------
# OAuth
# ---------------------------------------------------------

def build_authorization_url(client_key, redirect_uri, state):
    query = urlencode(
        {
            "client_key": client_key,
            "scope": ",".join(SCOPES),
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def _tokens_from(body, now, previous_refresh_token=None):
    if not body.get("access_token"):
        raise TikTokError("TikTok did not return an access token.")

    return TikTokTokens(
        access_token=body["access_token"],
        access_expires_at=now + timedelta(seconds=int(body.get("expires_in", 0))),
        # TikTok may issue a new refresh token; always keep the newest.
        refresh_token=body.get("refresh_token") or previous_refresh_token,
        refresh_expires_at=now
        + timedelta(seconds=int(body.get("refresh_expires_in", 0))),
        open_id=str(body.get("open_id", "")),
    )


def exchange_code_for_tokens(session, client_key, client_secret, redirect_uri, code, now):
    # Never retried: the code is single-use.
    body = request_json(
        session,
        "POST",
        TOKEN_URL,
        retry=False,
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
    )
    return _tokens_from(body, now)


def refresh_tokens(session, client_key, client_secret, refresh_token, now):
    # Never retried: if TikTok rotated the refresh token but the reply
    # was lost, retrying with the old one could fail the account.
    body = request_json(
        session,
        "POST",
        TOKEN_URL,
        retry=False,
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
    )
    return _tokens_from(body, now, previous_refresh_token=refresh_token)


def revoke_token(session, client_key, client_secret, access_token):
    request_json(
        session,
        "POST",
        REVOKE_URL,
        retry=False,
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "token": access_token,
        },
    )


# ---------------------------------------------------------
# Stored tokens
# ---------------------------------------------------------

def store_tokens(account, tokens, encryption_key):
    account.encrypted_access_token = encrypt_token(tokens.access_token, encryption_key)
    account.access_token_expires_at = tokens.access_expires_at
    account.encrypted_refresh_token = encrypt_token(tokens.refresh_token, encryption_key)
    account.refresh_token_expires_at = tokens.refresh_expires_at


def clear_tokens(account):
    account.encrypted_access_token = None
    account.access_token_expires_at = None
    account.encrypted_refresh_token = None
    account.refresh_token_expires_at = None


def usable_token(session, account, encryption_key, client_key, client_secret, now):
    """Return a working access token, refreshing it if needed."""
    if not account.encrypted_refresh_token:
        raise TikTokError(
            "This TikTok account is not connected. Connect it again.",
            needs_reconnect=True,
        )

    if (
        account.encrypted_access_token
        and account.access_token_expires_at is not None
        and account.access_token_expires_at - now > ACCESS_TOKEN_MARGIN
    ):
        return decrypt_token(account.encrypted_access_token, encryption_key)

    if account.refresh_token_expires_at is not None and account.refresh_token_expires_at <= now:
        raise TikTokError(
            "TikTok access expired. Reconnect the account.",
            needs_reconnect=True,
        )

    tokens = refresh_tokens(
        session,
        client_key,
        client_secret,
        decrypt_token(account.encrypted_refresh_token, encryption_key),
        now,
    )
    store_tokens(account, tokens, encryption_key)
    return tokens.access_token


# ---------------------------------------------------------
# Reading data
# ---------------------------------------------------------

def make_title(item, posted_at):
    text = item.get("title") or item.get("video_description") or ""
    text = " ".join(text.split())  # Descriptions can contain line breaks.

    if not text:
        return f"TikTok video · {posted_at.strftime('%b %d, %Y')}"

    if len(text) > CAPTION_LIMIT:
        return text[: CAPTION_LIMIT - 1] + "…"

    return text


def parse_video(item):
    posted_at = datetime.fromtimestamp(
        int(item["create_time"]), timezone.utc
    ).replace(tzinfo=None)

    return TikTokVideo(
        video_id=str(item["id"]),
        title=make_title(item, posted_at),
        posted_at=posted_at,
        views=int(item.get("view_count") or 0),
        likes=int(item.get("like_count") or 0),
        comments=int(item.get("comment_count") or 0),
        shares=int(item.get("share_count") or 0),
        share_url=item.get("share_url"),
        thumbnail_url=item.get("cover_image_url"),
    )


class TikTokClient:
    def __init__(self, access_token, session=None, sleep=time.sleep):
        self._session = session or requests.Session()
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._sleep = sleep

    def fetch_profile(self):
        body = request_json(
            self._session,
            "GET",
            USER_INFO_URL,
            retry=True,
            sleep=self._sleep,
            headers=self._headers,
            params={"fields": USER_FIELDS},
        )
        user = (body.get("data") or {}).get("user") or {}

        return TikTokProfile(
            open_id=str(user.get("open_id", "")),
            display_name=user.get("display_name") or "",
            username=user.get("username") or "",
        )

    def fetch_recent_videos(self, limit=MAX_VIDEOS):
        videos = []
        cursor = None

        while len(videos) < limit:
            payload = {"max_count": min(PAGE_SIZE, limit - len(videos))}

            if cursor is not None:
                payload["cursor"] = cursor

            # A POST, but it only reads data, so it is safe to retry.
            body = request_json(
                self._session,
                "POST",
                VIDEO_LIST_URL,
                retry=True,
                sleep=self._sleep,
                headers=self._headers,
                params={"fields": VIDEO_FIELDS},
                json=payload,
            )
            data = body.get("data") or {}
            page = data.get("videos") or []
            videos.extend(parse_video(item) for item in page)

            if not data.get("has_more") or not page:
                break

            cursor = data.get("cursor")

        return videos[:limit]


# ---------------------------------------------------------
# Saving data
# ---------------------------------------------------------

def sync_tiktok_account(client, session, synced_at, account=None):
    profile = client.fetch_profile()
    videos = client.fetch_recent_videos()

    if account is None:
        account = SocialAccount.query.filter_by(
            platform=PLATFORM,
            external_account_id=profile.open_id,
        ).first()

    if account is None:
        account = SocialAccount(
            platform=PLATFORM,
            external_account_id=profile.open_id,
        )
        session.add(account)

    name = profile.display_name or profile.username or "TikTok account"
    account.account_name = name
    account.account_handle = f"@{profile.username}" if profile.username else None
    account.last_synced_at = synced_at

    imported = updated = 0

    for video in videos:
        post = Post.query.filter_by(
            platform=PLATFORM,
            external_id=video.video_id,
        ).first()

        if post is None:
            post = Post(platform=PLATFORM, external_id=video.video_id)
            session.add(post)
            imported += 1
        else:
            updated += 1

        post.caption = video.title
        post.content_type = "Video"
        post.posted_at = video.posted_at
        post.views = video.views
        post.likes = video.likes
        post.comments = video.comments
        post.shares = video.shares
        post.saves = 0  # Not provided by the Display API.
        post.external_url = video.share_url
        post.thumbnail_url = video.thumbnail_url
        post.source = SOURCE
        post.social_account = account

        record_metric_snapshot(post, captured_at=synced_at)

    return TikTokSyncResult(
        account=account,
        account_name=account.account_handle or name,
        imported=imported,
        updated=updated,
    )

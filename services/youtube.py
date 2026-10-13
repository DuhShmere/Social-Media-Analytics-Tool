"""YouTube Data API integration.

YouTubeClient only talks to YouTube and returns plain data objects.
sync_channel() saves that data. It adds to the database session but
does not commit; the caller owns the transaction. All API calls
finish before anything is written, so a failed request never leaves
partial data behind.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httplib2
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from models import Post, SocialAccount, record_metric_snapshot


PLATFORM = "YouTube"
MAX_VIDEOS = 50
REQUEST_TIMEOUT_SECONDS = 15

# The client library retries only transient failures (5xx, 429,
# rate-limit 403s, timeouts, and dropped connections) with randomized
# exponential backoff. Quota, invalid-key, and not-found errors fail
# immediately because retrying them cannot succeed.
NUM_RETRIES = 2

HANDLE_PATTERN = re.compile(r"^[\w.-]{3,30}$")
CHANNEL_URL_PATTERN = re.compile(
    r"^(?:https?://)?(?:www\.|m\.)?youtube\.com/@([^/?#]+)",
    re.IGNORECASE,
)

API_KEY_PATTERN = re.compile(r"([?&]key=)[^&\s'\"]+")


def redact_api_key(text):
    return API_KEY_PATTERN.sub(r"\1REDACTED", text)


class ApiKeyRedactionFilter(logging.Filter):
    """Remove API keys from log messages and tracebacks.

    The Google client puts the full request URL, which contains the
    API key, in retry warnings and in HttpError messages.
    """

    def filter(self, record):
        record.msg = redact_api_key(record.getMessage())
        record.args = None

        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(
                record.exc_info
            )

        if record.exc_text:
            record.exc_text = redact_api_key(record.exc_text)

        return True


# Logger filters only apply to records created by that exact logger,
# so each Google client logger that can log a URL gets the filter.
for logger_name in (
    "googleapiclient.discovery",
    "googleapiclient.http",
    "googleapiclient.model",
):
    logging.getLogger(logger_name).addFilter(ApiKeyRedactionFilter())


class YouTubeError(Exception):
    """A YouTube problem with a message that is safe to show the user."""


@dataclass(frozen=True)
class ChannelData:
    channel_id: str
    title: str
    uploads_playlist_id: str


@dataclass(frozen=True)
class VideoData:
    video_id: str
    title: str
    published_at: datetime
    views: int
    likes: int
    comments: int
    thumbnail_url: str | None


@dataclass(frozen=True)
class SyncResult:
    channel_name: str
    imported: int
    updated: int


def normalize_handle(value):
    """Accept "@name", "name", or a youtube.com/@name link."""
    value = (value or "").strip()

    url_match = CHANNEL_URL_PATTERN.match(value)

    if url_match:
        value = url_match.group(1)

    handle = value.lstrip("@")

    if not handle:
        raise YouTubeError("Enter a YouTube channel handle.")

    if not HANDLE_PATTERN.match(handle):
        raise YouTubeError(
            "Enter a valid channel handle, such as @channelname."
        )

    return handle


def get_error_reason(error):
    details = getattr(error, "error_details", None)

    if isinstance(details, list) and details and isinstance(
        details[0], dict
    ):
        return details[0].get("reason")

    return None


def describe_http_error(error):
    status_code = getattr(error.resp, "status", None)
    reason = get_error_reason(error)

    if reason == "quotaExceeded":
        return (
            "The daily YouTube API quota has been used up. "
            "Try again after midnight Pacific Time."
        )

    if reason == "keyInvalid":
        return (
            "The YouTube API key is not valid. Check "
            "YOUTUBE_API_KEY in your .env file."
        )

    if status_code == 400:
        return (
            "YouTube rejected the request. Check that the "
            "channel handle is correct."
        )

    if status_code == 403:
        return (
            "YouTube denied the request. Check your API key, "
            "API restrictions, and remaining quota."
        )

    if status_code == 404:
        return "The requested YouTube channel was not found."

    if status_code == 429 or (status_code or 0) >= 500:
        return (
            "YouTube is temporarily unavailable. "
            "Please try again in a few minutes."
        )

    return (
        "YouTube could not complete the request. "
        "Please try again."
    )


def get_thumbnail_url(thumbnails):
    for size in ("high", "medium", "default"):
        thumbnail = thumbnails.get(size)

        if thumbnail and thumbnail.get("url"):
            return thumbnail["url"]

    return None


def parse_video(item):
    snippet = item.get("snippet", {})
    statistics = item.get("statistics", {})

    published_at = datetime.fromisoformat(
        snippet["publishedAt"].replace("Z", "+00:00")
    ).astimezone(timezone.utc).replace(tzinfo=None)

    return VideoData(
        video_id=item["id"],
        title=snippet.get("title", "Untitled YouTube video"),
        published_at=published_at,
        views=int(statistics.get("viewCount", 0)),
        likes=int(statistics.get("likeCount", 0)),
        comments=int(statistics.get("commentCount", 0)),
        thumbnail_url=get_thumbnail_url(
            snippet.get("thumbnails", {})
        ),
    )


class YouTubeClient:
    def __init__(self, api_key, http=None):
        self._service = build(
            "youtube",
            "v3",
            developerKey=api_key,
            http=http or httplib2.Http(
                timeout=REQUEST_TIMEOUT_SECONDS
            ),
            cache_discovery=False,
        )

    def _execute(self, request, allow_not_found=False):
        try:
            return request.execute(num_retries=NUM_RETRIES)

        except HttpError as error:
            if allow_not_found and error.resp.status == 404:
                return None

            raise YouTubeError(describe_http_error(error)) from error

        except (OSError, httplib2.HttpLib2Error) as error:
            raise YouTubeError(
                "Could not reach YouTube. Check your internet "
                "connection and try again."
            ) from error

    def fetch_channel(self, handle):
        response = self._execute(
            self._service.channels().list(
                part="snippet,contentDetails,statistics",
                forHandle=handle,
                maxResults=1,
            )
        )

        channels = response.get("items", [])

        if not channels:
            return None

        channel = channels[0]

        return ChannelData(
            channel_id=channel["id"],
            title=channel["snippet"]["title"],
            uploads_playlist_id=channel["contentDetails"][
                "relatedPlaylists"
            ]["uploads"],
        )

    def fetch_recent_videos(self, uploads_playlist_id):
        # YouTube returns 404 for the uploads playlist of a channel
        # that has never uploaded, which simply means "no videos".
        playlist_response = self._execute(
            self._service.playlistItems().list(
                part="contentDetails",
                playlistId=uploads_playlist_id,
                maxResults=MAX_VIDEOS,
            ),
            allow_not_found=True,
        )

        video_ids = [
            item["contentDetails"]["videoId"]
            for item in (playlist_response or {}).get("items", [])
        ]

        if not video_ids:
            return []

        video_response = self._execute(
            self._service.videos().list(
                part="snippet,statistics",
                id=",".join(video_ids),
            )
        )

        return [
            parse_video(item)
            for item in video_response.get("items", [])
        ]


def sync_channel(client, handle, session, synced_at):
    channel = client.fetch_channel(handle)

    if channel is None:
        raise YouTubeError(
            "YouTube channel not found. Check the channel "
            "handle and try again."
        )

    videos = client.fetch_recent_videos(channel.uploads_playlist_id)

    if not videos:
        raise YouTubeError(
            "No public videos were found for this YouTube channel."
        )

    social_account = SocialAccount.query.filter_by(
        platform=PLATFORM,
        external_account_id=channel.channel_id,
    ).first()

    if social_account is None:
        social_account = SocialAccount(
            platform=PLATFORM,
            external_account_id=channel.channel_id,
        )
        session.add(social_account)

    social_account.account_name = channel.title
    social_account.account_handle = f"@{handle}"
    social_account.last_synced_at = synced_at

    imported_count = 0
    updated_count = 0

    for video in videos:
        post = Post.query.filter_by(
            platform=PLATFORM,
            external_id=video.video_id,
        ).first()

        if post is None:
            post = Post(platform=PLATFORM, external_id=video.video_id)
            session.add(post)
            imported_count += 1
        else:
            updated_count += 1

        post.caption = video.title
        post.content_type = "Video"
        post.posted_at = video.published_at
        post.views = video.views
        post.likes = video.likes
        post.comments = video.comments
        post.shares = 0
        post.saves = 0
        post.external_url = (
            f"https://www.youtube.com/watch?v={video.video_id}"
        )
        post.thumbnail_url = video.thumbnail_url
        post.source = "youtube_api"
        post.social_account = social_account

        record_metric_snapshot(post, captured_at=synced_at)

    return SyncResult(
        channel_name=channel.title,
        imported=imported_count,
        updated=updated_count,
    )

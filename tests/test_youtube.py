import json
import logging
from datetime import datetime

import httplib2
import pytest
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpMockSequence

import app as app_module
import services.youtube as youtube_service
from services.youtube import (
    NUM_RETRIES,
    YouTubeClient,
    YouTubeError,
    describe_http_error,
    normalize_handle,
    parse_video,
)


# These tests run the real google-api-python-client request and retry
# code against canned HTTP responses. No real network requests are made.

CHANNEL_BODY = {
    "items": [
        {
            "id": "UC-test-channel",
            "snippet": {"title": "Test Channel"},
            "contentDetails": {
                "relatedPlaylists": {"uploads": "UU-test-uploads"},
            },
        }
    ]
}


def error_body(status, reason=None):
    body = {"error": {"code": status, "message": "Request failed."}}

    if reason:
        body["error"]["errors"] = [
            {"reason": reason, "domain": "youtube", "message": "Failed."}
        ]

    return body


def response(status, body):
    return ({"status": str(status)}, json.dumps(body))


class RecordingHttp(HttpMockSequence):
    """Returns canned responses in order and records each request."""

    def __init__(self, responses):
        super().__init__(responses)
        self.uris = []

    def request(self, uri, *args, **kwargs):
        self.uris.append(uri)
        return super().request(uri, *args, **kwargs)


class FailingHttp:
    """An HTTP object whose every request raises the given error."""

    def __init__(self, error):
        self.error = error
        self.attempts = 0

    def request(self, *args, **kwargs):
        self.attempts += 1
        raise self.error


@pytest.fixture(autouse=True)
def skip_retry_sleep(monkeypatch):
    # The library sleeps between retries; skip that to keep tests fast.
    monkeypatch.setattr("googleapiclient.http.time.sleep", lambda seconds: None)


# ---------------------------------------------------------
# Channel handles
# ---------------------------------------------------------

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("@penguinz0", "penguinz0"),
        ("IShowSpeed", "IShowSpeed"),
        ("  https://www.youtube.com/@Some.Channel/videos ", "Some.Channel"),
        ("youtube.com/@my-channel_1", "my-channel_1"),
    ],
)
def test_normalize_handle_accepts_handles_and_links(value, expected):
    assert normalize_handle(value) == expected


@pytest.mark.parametrize(
    ("value", "expected_message"),
    [
        ("", "Enter a YouTube channel handle."),
        ("@", "Enter a YouTube channel handle."),
        ("ab", "Enter a valid channel handle"),
        ("has space", "Enter a valid channel handle"),
        ("https://example.com/@channel", "Enter a valid channel handle"),
    ],
)
def test_normalize_handle_rejects_invalid_values(value, expected_message):
    with pytest.raises(YouTubeError, match=expected_message):
        normalize_handle(value)


def test_invalid_handle_is_rejected_before_calling_youtube(client, monkeypatch):
    def fail_build(*args, **kwargs):
        raise AssertionError("YouTube client should not be built.")

    monkeypatch.setattr(app_module, "YOUTUBE_API_KEY", "test-api-key")
    monkeypatch.setattr(youtube_service, "build", fail_build)

    result = client.post(
        "/import-youtube",
        data={"channel_handle": "not a handle"},
    )

    assert result.status_code == 200
    assert b"Enter a valid channel handle" in result.data


# ---------------------------------------------------------
# Error messages
# ---------------------------------------------------------

@pytest.mark.parametrize(
    ("status", "reason", "expected_message"),
    [
        (403, "quotaExceeded", "daily YouTube API quota has been used up"),
        (400, "keyInvalid", "YouTube API key is not valid"),
        (400, None, "Check that the channel handle is correct"),
        (403, "forbidden", "YouTube denied the request"),
        (404, None, "channel was not found"),
        (429, None, "temporarily unavailable"),
        (503, None, "temporarily unavailable"),
        (418, None, "could not complete the request"),
    ],
)
def test_describe_http_error(status, reason, expected_message):
    error = HttpError(
        httplib2.Response({"status": status}),
        json.dumps(error_body(status, reason)).encode("utf-8"),
    )

    assert expected_message in describe_http_error(error)


# ---------------------------------------------------------
# Retries and timeouts
# ---------------------------------------------------------

def test_transient_error_is_retried_then_succeeds():
    http = RecordingHttp(
        [
            response(503, error_body(503, "backendError")),
            response(200, CHANNEL_BODY),
        ]
    )

    channel = YouTubeClient("test-api-key", http=http).fetch_channel(
        "testchannel"
    )

    assert channel.title == "Test Channel"
    assert channel.uploads_playlist_id == "UU-test-uploads"
    assert len(http.uris) == 2
    assert "forHandle=testchannel" in http.uris[0]


def test_quota_error_is_not_retried():
    http = RecordingHttp(
        [
            response(403, error_body(403, "quotaExceeded")),
            response(200, CHANNEL_BODY),
        ]
    )

    with pytest.raises(YouTubeError, match="quota has been used up"):
        YouTubeClient("test-api-key", http=http).fetch_channel("testchannel")

    assert len(http.uris) == 1


def test_retries_stop_after_the_limit():
    http = RecordingHttp(
        [response(503, error_body(503))] * (NUM_RETRIES + 2)
    )

    with pytest.raises(YouTubeError, match="temporarily unavailable"):
        YouTubeClient("test-api-key", http=http).fetch_channel("testchannel")

    assert len(http.uris) == NUM_RETRIES + 1


@pytest.mark.parametrize(
    "error",
    [
        ConnectionError("Connection reset"),
        httplib2.ServerNotFoundError("Unable to find host"),
    ],
    ids=["dropped-connection", "unknown-host"],
)
def test_network_failures_are_retried_then_reported(error):
    http = FailingHttp(error)

    with pytest.raises(YouTubeError, match="Could not reach YouTube"):
        YouTubeClient("test-api-key", http=http).fetch_channel("testchannel")

    assert http.attempts == NUM_RETRIES + 1


def test_default_http_client_has_a_timeout(monkeypatch):
    captured = {}

    def fake_build(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(youtube_service, "build", fake_build)

    YouTubeClient("test-api-key")

    assert captured["developerKey"] == "test-api-key"
    assert captured["http"].timeout == youtube_service.REQUEST_TIMEOUT_SECONDS


# ---------------------------------------------------------
# API key redaction
# ---------------------------------------------------------

SECRET_KEY = "AIzaTEST-secret-key-123"


def test_api_key_is_redacted_from_retry_logs(caplog):
    http = RecordingHttp(
        [response(503, error_body(503)), response(200, CHANNEL_BODY)]
    )

    with caplog.at_level(logging.WARNING):
        YouTubeClient(SECRET_KEY, http=http).fetch_channel("testchannel")

    assert "Sleeping" in caplog.text
    assert "key=REDACTED" in caplog.text
    assert SECRET_KEY not in caplog.text


def test_api_key_is_redacted_from_sync_error_logs(client, monkeypatch, caplog):
    http = RecordingHttp([response(403, error_body(403, "quotaExceeded"))])

    monkeypatch.setattr(app_module, "YOUTUBE_API_KEY", SECRET_KEY)
    monkeypatch.setattr(httplib2, "Http", lambda timeout: http)

    with caplog.at_level(logging.WARNING):
        result = client.post(
            "/import-youtube",
            data={"channel_handle": "@testchannel"},
        )

    assert b"quota has been used up" in result.data
    assert "YouTube sync failed" in caplog.text
    assert "key=REDACTED" in caplog.text
    assert SECRET_KEY not in caplog.text


def test_api_key_is_redacted_from_tracebacks(caplog):
    error = HttpError(
        httplib2.Response({"status": 500}),
        b"{}",
        uri=f"https://youtube.googleapis.com/v3/channels?key={SECRET_KEY}&alt=json",
    )

    with caplog.at_level(logging.ERROR):
        try:
            raise error
        except HttpError:
            app_module.app.logger.exception("Unexpected YouTube import error.")

    assert "Traceback" in caplog.text
    assert SECRET_KEY not in caplog.text


# ---------------------------------------------------------
# Parsing
# ---------------------------------------------------------

def test_missing_uploads_playlist_means_no_videos():
    http = RecordingHttp(
        [response(404, error_body(404, "playlistNotFound"))]
    )

    videos = YouTubeClient("test-api-key", http=http).fetch_recent_videos(
        "UU-empty"
    )

    assert videos == []
    assert len(http.uris) == 1


def test_parse_video_handles_hidden_counts_and_offsets():
    video = parse_video(
        {
            "id": "vid-1",
            "snippet": {
                "title": "Hidden likes",
                "publishedAt": "2026-09-10T08:00:00-07:00",
                "thumbnails": {"default": {"url": "https://i.ytimg.com/d.jpg"}},
            },
            # Channels can hide like counts, so likeCount may be absent.
            "statistics": {"viewCount": "1200", "commentCount": "4"},
        }
    )

    assert video.published_at == datetime(2026, 9, 10, 15, 0)
    assert video.views == 1200
    assert video.likes == 0
    assert video.comments == 4
    assert video.thumbnail_url == "https://i.ytimg.com/d.jpg"

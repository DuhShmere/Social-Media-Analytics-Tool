from datetime import datetime
from io import BytesIO
from unittest.mock import MagicMock

import httplib2
import pytest
from googleapiclient.errors import HttpError

import app as app_module
import services.youtube as youtube_service
from app import (
    MetricSnapshot,
    Post,
    SocialAccount,
    calculate_metric_change,
    db,
    record_metric_snapshot,
)
from validators import parse_external_url


def create_post(
    platform="YouTube",
    caption="Test video",
    content_type="Video",
    views=1000,
    likes=100,
    comments=20,
    shares=10,
    saves=5,
    external_id=None,
    source="manual",
):
    post = Post(
        platform=platform,
        caption=caption,
        content_type=content_type,
        posted_at=datetime(2026, 9, 15, 14, 30),
        views=views,
        likes=likes,
        comments=comments,
        shares=shares,
        saves=saves,
        external_id=external_id,
        source=source,
    )

    db.session.add(post)
    db.session.commit()

    return post


def test_dashboard_loads(client):
    response = client.get("/")

    assert response.status_code == 200
    assert b"Social Media" in response.data


def test_add_manual_post(client, app):
    response = client.post(
        "/add-post",
        data={
            "platform": "Instagram",
            "caption": "Testing a new analytics post",
            "content_type": "Reel",
            "posted_at": "2026-09-15T14:30",
            "views": "1000",
            "likes": "120",
            "comments": "25",
            "shares": "15",
            "saves": "30",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        post = Post.query.one()

        assert post.platform == "Instagram"
        assert post.caption == "Testing a new analytics post"
        assert post.content_type == "Reel"
        assert post.views == 1000
        assert post.likes == 120
        assert post.source == "manual"


def test_engagement_rate(app):
    with app.app_context():
        post = create_post(
            views=1000,
            likes=100,
            comments=20,
            shares=10,
            saves=5,
        )

        assert post.engagement_rate == 13.5


def test_zero_views_produces_zero_engagement(app):
    with app.app_context():
        post = create_post(
            views=0,
            likes=100,
            comments=20,
            shares=10,
            saves=5,
        )

        assert post.engagement_rate == 0


def test_analytics_api_returns_json(client, app):
    with app.app_context():
        create_post()

    response = client.get("/api/analytics")

    assert response.status_code == 200
    assert response.is_json

    data = response.get_json()

    assert isinstance(data, dict)
    assert len(data) > 0


def test_analytics_filters_by_platform(client, app):
    with app.app_context():
        create_post(
            platform="YouTube",
            caption="YouTube test",
            external_id="youtube-test-1",
        )
        create_post(
            platform="Instagram",
            caption="Instagram test",
            external_id="instagram-test-1",
        )

    response = client.get("/api/analytics?platform=YouTube")

    assert response.status_code == 200
    assert response.is_json


def test_manual_post_can_be_deleted(client, app):
    with app.app_context():
        post = create_post()
        post_id = post.id

    response = client.post(
        f"/posts/{post_id}/delete",
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        assert db.session.get(Post, post_id) is None


def test_sample_csv_download(client):
    response = client.get("/sample-csv")

    assert response.status_code == 200
    assert b"platform" in response.data
    assert b"caption" in response.data
    assert b"posted_at" in response.data


def test_csv_import(client, app):
    csv_content = (
        "platform,caption,content_type,posted_at,views,likes,"
        "comments,shares,saves,external_id,external_url\n"
        "Instagram,Imported reel,Reel,2026-09-15T14:30,"
        "2500,300,40,25,60,csv-test-1,"
        "https://www.instagram.com/p/example/\n"
    )

    response = client.post(
        "/import-csv",
        data={
            "csv_file": (
                BytesIO(csv_content.encode("utf-8")),
                "posts.csv",
            )
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert response.status_code == 200

    with app.app_context():
        post = Post.query.filter_by(external_id="csv-test-1").first()

        assert post is not None
        assert post.platform == "Instagram"
        assert post.caption == "Imported reel"
        assert post.views == 2500
        assert post.source == "csv_import"


def test_csv_import_skips_duplicate_posts(client, app):
    csv_content = (
        "platform,caption,content_type,posted_at,views,likes,"
        "comments,shares,saves,external_id,external_url\n"
        "YouTube,Duplicate video,Video,2026-09-15T14:30,"
        "1000,100,20,10,5,duplicate-video-1,"
        "https://www.youtube.com/watch?v=example\n"
    )

    def upload_csv():
        return client.post(
            "/import-csv",
            data={
                "csv_file": (
                    BytesIO(csv_content.encode("utf-8")),
                    "posts.csv",
                )
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )

    first_response = upload_csv()
    second_response = upload_csv()

    assert first_response.status_code == 200
    assert second_response.status_code == 200

    with app.app_context():
        matching_posts = Post.query.filter_by(
            platform="YouTube",
            external_id="duplicate-video-1",
        ).all()

        assert len(matching_posts) == 1


def test_export_csv(client, app):
    with app.app_context():
        create_post(
            platform="YouTube",
            caption="Export test video",
            external_id="export-test-1",
        )

    response = client.get("/export-csv")

    assert response.status_code == 200
    assert b"Export test video" in response.data
    assert b"export-test-1" in response.data

    content_disposition = response.headers.get("Content-Disposition", "")
    assert "attachment" in content_disposition


def test_manual_post_creates_metric_snapshot(client, app):
    response = client.post(
        "/add-post",
        data={
            "platform": "Instagram",
            "caption": "Snapshot test",
            "content_type": "Reel",
            "posted_at": "2026-09-20T10:00",
            "views": "500",
            "likes": "50",
            "comments": "10",
            "shares": "5",
            "saves": "20",
        },
    )

    assert response.status_code == 302

    with app.app_context():
        post = Post.query.filter_by(caption="Snapshot test").one()
        snapshot = MetricSnapshot.query.filter_by(post_id=post.id).one()

        assert snapshot.views == 500
        assert snapshot.total_interactions == 85
        assert snapshot.engagement_rate == 17.0


def test_editing_post_creates_second_snapshot(client, app):
    with app.app_context():
        post = create_post()
        record_metric_snapshot(post)
        db.session.commit()
        post_id = post.id

    response = client.post(
        f"/posts/{post_id}/edit",
        data={
            "platform": "YouTube",
            "caption": "Updated video",
            "content_type": "Video",
            "posted_at": "2026-09-15T14:30",
            "views": "1500",
            "likes": "150",
            "comments": "30",
            "shares": "20",
            "saves": "10",
        },
    )

    assert response.status_code == 302

    with app.app_context():
        snapshots = MetricSnapshot.query.filter_by(
            post_id=post_id
        ).order_by(MetricSnapshot.id).all()

        assert len(snapshots) == 2
        assert snapshots[0].views == 1000
        assert snapshots[1].views == 1500


def test_post_detail_page_loads(client, app):
    with app.app_context():
        post = create_post()
        record_metric_snapshot(post)
        db.session.commit()
        post_id = post.id

    response = client.get(f"/posts/{post_id}")

    assert response.status_code == 200
    assert b"Performance History" in response.data
    assert b"Test video" in response.data


def test_post_history_api_returns_snapshots(client, app):
    with app.app_context():
        post = create_post()
        record_metric_snapshot(post)

        post.views = 1500
        post.likes = 150
        record_metric_snapshot(post)

        db.session.commit()
        post_id = post.id

    response = client.get(f"/api/posts/{post_id}/history")

    assert response.status_code == 200
    assert response.is_json

    data = response.get_json()

    assert data["post"]["caption"] == "Test video"
    assert len(data["snapshots"]) == 2
    assert data["snapshots"][0]["views"] == 1000
    assert data["snapshots"][1]["views"] == 1500


def test_deleting_post_deletes_snapshots(client, app):
    with app.app_context():
        post = create_post()
        record_metric_snapshot(post)
        db.session.commit()
        post_id = post.id

    response = client.post(f"/posts/{post_id}/delete")

    assert response.status_code == 302

    with app.app_context():
        assert db.session.get(Post, post_id) is None
        assert MetricSnapshot.query.filter_by(post_id=post_id).count() == 0


def test_metric_change_rounds_engagement_point_difference():
    # 13.6 - 13.5 is 0.09999999999999964 in floating-point math.
    change = calculate_metric_change(13.6, 13.5)

    assert change["difference"] == 0.1
    assert change["percentage"] == 0.74


def test_metric_change_handles_zero_and_missing_previous_values():
    assert calculate_metric_change(0, 0) == {
        "difference": 0,
        "percentage": 0,
    }

    # Growth from zero has no meaningful percentage.
    assert calculate_metric_change(500, 0) == {
        "difference": 500,
        "percentage": None,
    }

    assert calculate_metric_change(250, None) == {
        "difference": 250,
        "percentage": None,
    }

    assert calculate_metric_change(800, 1000) == {
        "difference": -200,
        "percentage": -20.0,
    }


def test_post_detail_displays_rounded_engagement_change(client, app):
    with app.app_context():
        # 135 / 1000 interactions = 13.5% engagement.
        post = create_post(
            views=1000,
            likes=100,
            comments=20,
            shares=10,
            saves=5,
        )
        record_metric_snapshot(
            post,
            captured_at=datetime(2026, 9, 20, 9, 0),
        )

        # 136 / 1000 interactions = 13.6% engagement.
        post.likes = 101
        record_metric_snapshot(
            post,
            captured_at=datetime(2026, 9, 21, 9, 0),
        )

        db.session.commit()
        post_id = post.id

    response = client.get(f"/posts/{post_id}")

    assert response.status_code == 200
    assert b"+0.1" in response.data
    assert b"0.0999" not in response.data


def test_parse_external_url_accepts_only_http_links():
    assert parse_external_url("") is None
    assert parse_external_url("   ") is None
    assert parse_external_url(None) is None
    assert parse_external_url(
        " https://www.instagram.com/p/example/ "
    ) == "https://www.instagram.com/p/example/"
    assert parse_external_url(
        "HTTP://example.com/post"
    ) == "HTTP://example.com/post"

    for unsafe_url in (
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "ftp://example.com/file",
        "www.example.com/no-scheme",
        "https://",
    ):
        with pytest.raises(ValueError):
            parse_external_url(unsafe_url)

    with pytest.raises(ValueError):
        parse_external_url("https://example.com/" + "a" * 500)


def test_csv_import_rejects_unsafe_external_url(client, app):
    csv_content = (
        "platform,caption,content_type,posted_at,views,likes,"
        "comments,shares,saves,external_id,external_url\n"
        "Instagram,Unsafe link,Reel,2026-09-15T14:30,"
        "100,10,1,1,1,unsafe-1,javascript:alert(1)\n"
        "Instagram,Safe link,Reel,2026-09-16T14:30,"
        "100,10,1,1,1,safe-1,https://www.instagram.com/p/safe/\n"
    )

    response = client.post(
        "/import-csv",
        data={
            "csv_file": (
                BytesIO(csv_content.encode("utf-8")),
                "posts.csv",
            )
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert b"Row 2: external_url must be" in response.data

    with app.app_context():
        assert Post.query.filter_by(external_id="unsafe-1").first() is None

        safe_post = Post.query.filter_by(external_id="safe-1").one()
        assert safe_post.external_url == "https://www.instagram.com/p/safe/"


def test_unsafe_stored_url_is_never_rendered_as_link(client, app):
    with app.app_context():
        post = create_post(caption="Legacy unsafe link")
        post.external_url = "javascript:alert(1)"
        record_metric_snapshot(post)
        db.session.commit()
        post_id = post.id

        assert post.safe_external_url is None

    dashboard_response = client.get("/")
    detail_response = client.get(f"/posts/{post_id}")

    assert dashboard_response.status_code == 200
    assert detail_response.status_code == 200
    assert b"javascript:" not in dashboard_response.data
    assert b"javascript:" not in detail_response.data


# ---------------------------------------------------------
# YouTube synchronization (mocked, no real API requests)
# ---------------------------------------------------------

def youtube_video(video_id, title, views, likes, comments):
    return {
        "id": video_id,
        "snippet": {
            "title": title,
            "publishedAt": "2026-09-10T15:00:00Z",
            "thumbnails": {
                "high": {"url": f"https://i.ytimg.com/{video_id}.jpg"},
            },
        },
        "statistics": {
            "viewCount": str(views),
            "likeCount": str(likes),
            "commentCount": str(comments),
        },
    }


def fake_youtube_client(videos, channel_found=True, video_error=None):
    youtube = MagicMock()

    channel_items = []

    if channel_found:
        channel_items.append(
            {
                "id": "UC-test-channel",
                "snippet": {"title": "Test Channel"},
                "contentDetails": {
                    "relatedPlaylists": {"uploads": "UU-test-uploads"},
                },
            }
        )

    youtube.channels.return_value.list.return_value.execute.return_value = {
        "items": channel_items,
    }

    youtube.playlistItems.return_value.list.return_value.execute.return_value = {
        "items": [
            {"contentDetails": {"videoId": video["id"]}}
            for video in videos
        ],
    }

    videos_request = youtube.videos.return_value.list.return_value

    if video_error is not None:
        videos_request.execute.side_effect = video_error
    else:
        videos_request.execute.return_value = {"items": videos}

    return youtube


@pytest.fixture()
def mock_youtube(monkeypatch):
    """Replace the YouTube client builder with a fake client."""
    state = {"client": None}

    def fake_build(*args, **kwargs):
        return state["client"]

    monkeypatch.setattr(app_module, "YOUTUBE_API_KEY", "test-api-key")
    monkeypatch.setattr(youtube_service, "build", fake_build)

    def use(client):
        state["client"] = client
        return client

    return use


def sync_channel(client, handle="@testchannel"):
    return client.post(
        "/import-youtube",
        data={"channel_handle": handle},
    )


def test_youtube_sync_imports_videos_and_snapshots(
    client,
    app,
    mock_youtube,
):
    youtube = mock_youtube(
        fake_youtube_client(
            [
                youtube_video("vid-1", "First video", 1000, 100, 10),
                youtube_video("vid-2", "Second video", 2000, 150, 20),
            ]
        )
    )

    response = sync_channel(client)

    assert response.status_code == 200
    assert b"Imported 2 new videos and updated 0" in response.data

    youtube.channels.return_value.list.assert_called_once_with(
        part="snippet,contentDetails,statistics",
        forHandle="testchannel",
        maxResults=1,
    )

    with app.app_context():
        account = SocialAccount.query.one()
        assert account.account_name == "Test Channel"
        assert account.account_handle == "@testchannel"
        assert account.last_synced_at is not None

        posts = Post.query.order_by(Post.external_id).all()
        assert [post.external_id for post in posts] == ["vid-1", "vid-2"]

        first = posts[0]
        assert first.source == "youtube_api"
        assert first.views == 1000
        assert first.likes == 100
        assert first.comments == 10
        assert first.social_account_id == account.id
        assert first.external_url == (
            "https://www.youtube.com/watch?v=vid-1"
        )

        for post in posts:
            assert len(post.metric_snapshots) == 1


def test_youtube_resync_updates_videos_without_duplicates(
    client,
    app,
    mock_youtube,
):
    mock_youtube(
        fake_youtube_client(
            [youtube_video("vid-1", "Original title", 1000, 100, 10)]
        )
    )
    sync_channel(client)

    mock_youtube(
        fake_youtube_client(
            [youtube_video("vid-1", "Renamed title", 1500, 130, 12)]
        )
    )
    response = sync_channel(client)

    assert response.status_code == 200
    assert b"Imported 0 new videos and updated 1" in response.data

    with app.app_context():
        assert SocialAccount.query.count() == 1

        post = Post.query.one()
        assert post.caption == "Renamed title"
        assert post.views == 1500
        assert post.likes == 130

        snapshots = MetricSnapshot.query.filter_by(
            post_id=post.id
        ).order_by(MetricSnapshot.id).all()

        assert [snapshot.views for snapshot in snapshots] == [1000, 1500]


def test_youtube_api_error_shows_message_and_saves_nothing(
    client,
    app,
    mock_youtube,
):
    quota_error = HttpError(
        httplib2.Response({"status": 403}),
        b'{"error": {"message": "quotaExceeded"}}',
    )

    mock_youtube(
        fake_youtube_client(
            [youtube_video("vid-1", "First video", 1000, 100, 10)],
            video_error=quota_error,
        )
    )

    response = sync_channel(client)

    assert response.status_code == 200
    assert b"YouTube denied the request" in response.data

    # The account was flushed before the failure; it must be rolled back.
    with app.app_context():
        assert SocialAccount.query.count() == 0
        assert Post.query.count() == 0
        assert MetricSnapshot.query.count() == 0


def test_youtube_channel_not_found_shows_message(
    client,
    app,
    mock_youtube,
):
    mock_youtube(fake_youtube_client([], channel_found=False))

    response = sync_channel(client, handle="@missing")

    assert response.status_code == 200
    assert b"YouTube channel not found" in response.data

    with app.app_context():
        assert SocialAccount.query.count() == 0


def test_youtube_import_requires_api_key(client, monkeypatch):
    def fail_build(*args, **kwargs):
        raise AssertionError("YouTube client should not be built.")

    monkeypatch.setattr(app_module, "YOUTUBE_API_KEY", None)
    monkeypatch.setattr(youtube_service, "build", fail_build)

    response = sync_channel(client)

    assert response.status_code == 200
    assert b"The YouTube API key is missing" in response.data


def test_youtube_post_cannot_be_edited_or_deleted(client, app):
    with app.app_context():
        post = create_post(
            caption="Synced video",
            external_id="vid-protected",
            source="youtube_api",
        )
        post_id = post.id

    edit_response = client.post(
        f"/posts/{post_id}/edit",
        data={
            "platform": "YouTube",
            "caption": "Tampered caption",
            "content_type": "Video",
            "posted_at": "2026-09-15T14:30",
            "views": "1",
            "likes": "0",
            "comments": "0",
            "shares": "0",
            "saves": "0",
        },
    )
    delete_response = client.post(f"/posts/{post_id}/delete")

    assert edit_response.status_code == 302
    assert delete_response.status_code == 302

    with app.app_context():
        post = db.session.get(Post, post_id)
        assert post is not None
        assert post.caption == "Synced video"
        assert post.views == 1000


# ---------------------------------------------------------
# Form validation and error pages
# ---------------------------------------------------------

def valid_post_form(**overrides):
    form = {
        "platform": "Instagram",
        "caption": "Validation test",
        "content_type": "Reel",
        "posted_at": "2026-09-15T14:30",
        "views": "1000",
        "likes": "100",
        "comments": "10",
        "shares": "5",
        "saves": "5",
    }
    form.update(overrides)
    return form


@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        ({"likes": "-1"}, b"Likes must be a whole number of 0 or more."),
        ({"views": "abc"}, b"Views must be a whole number of 0 or more."),
        ({"shares": "2.5"}, b"Shares must be a whole number of 0 or more."),
        ({"caption": "x" * 301}, b"Caption cannot exceed 300 characters."),
        ({"platform": "  "}, b"Platform, caption, and content type are required."),
        ({"posted_at": "not-a-date"}, b"Enter a valid date and time"),
    ],
    ids=[
        "negative-metric",
        "non-numeric-metric",
        "decimal-metric",
        "caption-too-long",
        "blank-platform",
        "invalid-date",
    ],
)
def test_add_post_rejects_invalid_input(
    client,
    app,
    overrides,
    expected_message,
):
    response = client.post(
        "/add-post",
        data=valid_post_form(**overrides),
    )

    assert response.status_code == 200
    assert expected_message in response.data

    with app.app_context():
        assert Post.query.count() == 0
        assert MetricSnapshot.query.count() == 0


def test_invalid_edit_leaves_post_unchanged(client, app):
    with app.app_context():
        post = create_post(caption="Original caption")
        post_id = post.id

    response = client.post(
        f"/posts/{post_id}/edit",
        data=valid_post_form(caption="Changed caption", views="-5"),
    )

    assert response.status_code == 200
    assert b"Views must be a whole number of 0 or more." in response.data

    with app.app_context():
        post = db.session.get(Post, post_id)
        assert post.caption == "Original caption"
        assert post.views == 1000
        assert MetricSnapshot.query.filter_by(post_id=post_id).count() == 0


@pytest.mark.parametrize(
    "path",
    ["/posts/9999", "/posts/9999/edit", "/does-not-exist"],
)
def test_missing_page_returns_404_page(client, path):
    response = client.get(path)

    assert response.status_code == 404
    assert b"Page not found" in response.data
    assert b"Back to Dashboard" in response.data


def test_missing_post_api_returns_json_404(client):
    response = client.get("/api/posts/9999/history")

    assert response.status_code == 404
    assert response.get_json() == {"error": "Not found."}


def test_unexpected_error_returns_500_page(client, app, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("Simulated failure")

    # TESTING mode re-raises errors; disable that to see the 500 page.
    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)
    monkeypatch.setattr(app_module, "calculate_best_posting_time", broken)
    monkeypatch.setattr(app_module, "calculate_metric_change", broken)

    with app.app_context():
        post = create_post()
        record_metric_snapshot(post)
        record_metric_snapshot(post)
        db.session.commit()
        post_id = post.id

    page_response = client.get(f"/posts/{post_id}")
    api_response = client.get("/api/analytics")

    assert page_response.status_code == 500
    assert b"Something went wrong" in page_response.data
    assert b"Simulated failure" not in page_response.data

    assert api_response.status_code == 500
    assert api_response.get_json() == {"error": "Internal server error."}

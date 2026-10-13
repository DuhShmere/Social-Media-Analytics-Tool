from datetime import datetime
from io import BytesIO

import pytest

from app import (
    MetricSnapshot,
    Post,
    calculate_metric_change,
    db,
    parse_external_url,
    record_metric_snapshot,
)


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

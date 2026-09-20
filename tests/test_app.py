from datetime import datetime
from io import BytesIO

from app import Post, db


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
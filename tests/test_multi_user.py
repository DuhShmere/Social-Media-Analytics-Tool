"""Each user sees and changes only their own accounts and posts.

The default `client` is signed in as the default user (USER_ID). These
tests give a second user, Bob, some data and check that every route
hides it from the default user, answering 404 rather than 403 so IDs
cannot be probed.
"""

import io
from datetime import datetime, timedelta

import pytest
import requests
import responses

from conftest import USER_ID, add_user, sign_in_as
from extensions import db
from models import MetricSnapshot, Post, SocialAccount, record_metric_snapshot
from services.csv_service import import_posts_from_csv
from services.tiktok import TOKEN_URL, TikTokTokens, store_tokens
from services.youtube import ChannelData, VideoData, sync_channel
from test_tiktok_routes import configured, http, mock_profile_and_videos, mock_token, start_login  # noqa: F401
from token_vault import generate_key


@pytest.fixture()
def bob(app):
    """A second user with an account, a manual post, and a synced post."""
    with app.app_context():
        user = add_user("bob", "bob-password-123")

        account = SocialAccount(
            user_id=user.id,
            platform="TikTok",
            account_name="Bob",
            account_handle="@bob",
            external_account_id="bob-open-id",
        )
        key = generate_key()
        store_tokens(
            account,
            TikTokTokens("act.bob", datetime.now() + timedelta(hours=10), "rft.bob",
                         datetime.now() + timedelta(days=300), "bob-open-id"),
            key,
        )
        db.session.add(account)

        manual = Post(
            user_id=user.id,
            platform="YouTube",
            caption="Bob's private plans",
            content_type="Video",
            posted_at=datetime(2026, 9, 15, 14, 30),
            views=777,
            source="manual",
        )
        synced = Post(
            user_id=user.id,
            platform="TikTok",
            caption="Bob's synced video",
            content_type="Video",
            posted_at=datetime(2026, 9, 16, 9, 0),
            source="tiktok_api",
            external_id="bob-video-1",
            social_account=account,
        )

        for post in (manual, synced):
            db.session.add(post)
            record_metric_snapshot(post)

        db.session.commit()

        return {
            "id": user.id,
            "account_id": account.id,
            "post_id": manual.id,
            "synced_post_id": synced.id,
        }


# ---------------------------------------------------------
# Another user's data is invisible
# ---------------------------------------------------------

def test_dashboard_and_accounts_show_only_your_data(client, bob):
    for path in ("/", "/accounts"):
        html = client.get(path).get_data(as_text=True)

        assert "Bob&#39;s private plans" not in html
        assert "@bob" not in html


def test_analytics_api_counts_only_your_posts(client, bob):
    data = client.get("/api/analytics").get_json()

    assert data["summary"]["total_posts"] == 0
    assert data["summary"]["total_views"] == 0


def test_csv_export_contains_only_your_posts(client, bob):
    csv_text = client.get("/export-csv").get_data(as_text=True)

    assert "Bob's private plans" not in csv_text


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/posts/{post_id}"),
        ("get", "/posts/{post_id}/edit"),
        ("post", "/posts/{post_id}/edit"),
        ("post", "/posts/{post_id}/delete"),
        ("get", "/api/posts/{post_id}/history"),
        ("get", "/posts/{synced_post_id}"),
        ("post", "/tiktok/accounts/{account_id}/sync"),
        ("post", "/tiktok/accounts/{account_id}/disconnect"),
        ("post", "/instagram/accounts/{account_id}/sync"),
        ("post", "/instagram/accounts/{account_id}/disconnect"),
    ],
)
def test_other_users_rows_answer_404(app, client, bob, method, path):
    response = getattr(client, method)(
        path.format(**bob),
        data={"caption": "Hijacked", "platform": "YouTube", "content_type": "Video",
              "posted_at": "2026-09-15T14:30", "views": "1"},
    )

    assert response.status_code == 404

    with app.app_context():
        post = db.session.get(Post, bob["post_id"])
        account = db.session.get(SocialAccount, bob["account_id"])

        assert post is not None
        assert post.caption == "Bob's private plans"
        assert account.encrypted_refresh_token is not None


def test_404_for_another_users_post_looks_like_a_missing_post(client, bob):
    other = client.get(f"/posts/{bob['post_id']}")
    missing = client.get("/posts/999999")

    assert other.status_code == missing.status_code == 404
    assert other.get_data() == missing.get_data()


def test_owner_can_still_reach_their_own_rows(app, bob):
    bobs_browser = app.test_client()
    sign_in_as(app, bobs_browser, bob["id"])

    assert bobs_browser.get(f"/posts/{bob['post_id']}").status_code == 200
    assert "Bob&#39;s private plans" in bobs_browser.get("/").get_data(as_text=True)
    assert bobs_browser.post(f"/posts/{bob['post_id']}/delete").status_code == 302

    with app.app_context():
        assert db.session.get(Post, bob["post_id"]) is None


# ---------------------------------------------------------
# New data belongs to whoever created it
# ---------------------------------------------------------

def test_manual_posts_belong_to_their_creator(app, client, bob):
    client.post(
        "/add-post",
        data={"platform": "YouTube", "caption": "Mine", "content_type": "Video",
              "posted_at": "2026-09-15T14:30", "views": "5"},
    )

    with app.app_context():
        assert Post.query.filter_by(caption="Mine").one().user_id == USER_ID


CSV = (
    "platform,caption,content_type,posted_at,views,likes,comments,shares,saves\n"
    "YouTube,Bob's private plans,Video,2026-09-15 14:30,777,0,0,0,0\n"
)


def test_csv_import_does_not_treat_another_users_post_as_a_duplicate(app, client, bob):
    response = client.post(
        "/import-csv",
        data={"csv_file": (io.BytesIO(CSV.encode()), "posts.csv")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200

    with app.app_context():
        mine = Post.query.filter_by(user_id=USER_ID).one()
        assert mine.caption == "Bob's private plans"
        assert mine.source == "csv_import"
        assert Post.query.count() == 3


def test_csv_import_still_skips_your_own_duplicates(app, bob):
    with app.app_context():
        result = import_posts_from_csv(io.BytesIO(CSV.encode()), db.session, user_id=bob["id"])

        assert result.imported == 0
        assert result.skipped == 1


class FakeYouTube:
    def fetch_channel(self, handle):
        return ChannelData("UC-shared", "Shared Channel", "UU-shared")

    def fetch_recent_videos(self, playlist_id):
        return [VideoData("vid-1", "Shared video", datetime(2026, 9, 1), 100, 10, 1, None)]


def test_two_users_can_import_the_same_youtube_channel(app, bob):
    with app.app_context():
        for user_id in (USER_ID, bob["id"]):
            result = sync_channel(FakeYouTube(), "shared", db.session, datetime(2026, 9, 2), user_id=user_id)
            db.session.commit()
            assert result.imported == 1

        accounts = SocialAccount.query.filter_by(platform="YouTube").all()
        assert sorted(account.user_id for account in accounts) == sorted([USER_ID, bob["id"]])
        assert Post.query.filter_by(external_id="vid-1").count() == 2

        # Re-syncing for one user updates only their copy.
        result = sync_channel(FakeYouTube(), "shared", db.session, datetime(2026, 9, 3), user_id=USER_ID)
        db.session.commit()
        assert (result.imported, result.updated) == (0, 1)


def test_two_users_can_connect_the_same_tiktok_account(app, configured, http):  # noqa: F811
    users = [USER_ID]

    with app.app_context():
        users.append(add_user("carol", "carol-password-1").id)

    for user_id in users:
        browser = app.test_client()
        sign_in_as(app, browser, user_id)
        mock_token(http)
        mock_profile_and_videos(http)
        start_login(browser)

        response = browser.get("/tiktok/callback?code=c&state=expected-state")
        assert response.status_code == 302

    with app.app_context():
        accounts = SocialAccount.query.filter_by(platform="TikTok", external_account_id="open-123").all()
        assert sorted(account.user_id for account in accounts) == sorted(users)

        for account in accounts:
            assert Post.query.filter_by(user_id=account.user_id, platform="TikTok").count() == 2


def test_snapshots_follow_their_post(app, bob):
    with app.app_context():
        snapshot = MetricSnapshot.query.filter_by(post_id=bob["post_id"]).one()
        assert snapshot.post.user_id == bob["id"]


def test_sync_refuses_an_account_from_another_user(app, bob):
    from services.tiktok import sync_tiktok_account

    class NoCalls:
        def fetch_profile(self):
            return None

        def fetch_recent_videos(self):
            return []

    with app.app_context():
        account = db.session.get(SocialAccount, bob["account_id"])

        with pytest.raises(ValueError, match="different user"):
            sync_tiktok_account(NoCalls(), db.session, datetime.now(), user_id=USER_ID, account=account)

from datetime import date, datetime

import pytest

from app import Post, SocialAccount, db
from services.analytics import (
    PostFilters,
    apply_post_filters,
    calculate_best_posting_time,
    summarize_posts,
)


def add_post(
    posted_at,
    platform="YouTube",
    caption="Analytics post",
    content_type="Video",
    views=1000,
    likes=100,
    comments=0,
    shares=0,
    saves=0,
    social_account=None,
):
    post = Post(
        platform=platform,
        caption=caption,
        content_type=content_type,
        posted_at=posted_at,
        views=views,
        likes=likes,
        comments=comments,
        shares=shares,
        saves=saves,
        social_account=social_account,
    )

    db.session.add(post)
    return post


def filtered_captions(filters):
    posts = apply_post_filters(Post.query, filters).order_by(Post.id)
    return [post.caption for post in posts]


# ---------------------------------------------------------
# Filter parsing
# ---------------------------------------------------------

def test_post_filters_from_empty_args():
    assert PostFilters.from_args({}) == PostFilters()


def test_post_filters_parse_valid_args():
    filters = PostFilters.from_args(
        {
            "account_id": "3",
            "search": "  launch ",
            "platform": "YouTube",
            "content_type": "Video",
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
        }
    )

    assert filters == PostFilters(
        account_id=3,
        search="launch",
        platform="YouTube",
        content_type="Video",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 30),
    )


@pytest.mark.parametrize(
    ("args", "expected_message"),
    [
        ({"start_date": "09/01/2026"}, "Invalid date filter"),
        ({"end_date": "2026-02-30"}, "Invalid date filter"),
        (
            {"start_date": "2026-09-10", "end_date": "2026-09-01"},
            "Start date must be on or before end date.",
        ),
        ({"account_id": "abc"}, "Invalid account filter."),
    ],
    ids=["wrong-format", "impossible-date", "reversed-range", "bad-account"],
)
def test_post_filters_reject_invalid_args(args, expected_message):
    with pytest.raises(ValueError, match=expected_message):
        PostFilters.from_args(args)


# ---------------------------------------------------------
# Filter results
# ---------------------------------------------------------

def test_date_range_includes_whole_end_day(app):
    with app.app_context():
        add_post(datetime(2026, 9, 9, 23, 59), caption="day before")
        add_post(datetime(2026, 9, 10, 0, 0), caption="start of range")
        add_post(datetime(2026, 9, 15, 23, 30), caption="late on end day")
        add_post(datetime(2026, 9, 16, 0, 0), caption="day after")
        db.session.commit()

        filters = PostFilters(
            start_date=date(2026, 9, 10),
            end_date=date(2026, 9, 15),
        )

        assert filtered_captions(filters) == [
            "start of range",
            "late on end day",
        ]


def test_filters_combine_and_search_ignores_case(app):
    with app.app_context():
        account = SocialAccount(
            platform="YouTube",
            account_name="Main channel",
            external_account_id="UC-main",
        )

        posted_at = datetime(2026, 9, 15, 12, 0)
        add_post(posted_at, caption="Product LAUNCH video", social_account=account)
        add_post(posted_at, caption="Launch recap", content_type="Short", social_account=account)
        add_post(posted_at, caption="Launch teaser", social_account=None)
        add_post(posted_at, caption="Launch reel", platform="Instagram", content_type="Reel")
        add_post(posted_at, caption="Unrelated video", social_account=account)
        db.session.commit()

        filters = PostFilters(
            account_id=account.id,
            search="launch",
            platform="YouTube",
            content_type="Video",
        )

        assert filtered_captions(filters) == ["Product LAUNCH video"]


# ---------------------------------------------------------
# Summary and recommendations
# ---------------------------------------------------------

def test_summarize_posts_totals_and_engagement():
    posts = [
        Post(views=1000, likes=100, comments=20, shares=10, saves=5),
        Post(views=3000, likes=200, comments=0, shares=0, saves=0),
    ]

    summary = summarize_posts(posts)

    assert summary == {
        "total_posts": 2,
        "total_views": 4000,
        "total_interactions": 335,
        "engagement_rate": 8.38,
        "likes": 300,
        "comments": 20,
        "shares": 10,
        "saves": 5,
    }


def test_summarize_posts_with_no_views_has_zero_engagement():
    assert summarize_posts([])["engagement_rate"] == 0
    assert summarize_posts(
        [Post(views=0, likes=5, comments=0, shares=0, saves=0)]
    )["engagement_rate"] == 0


def test_best_posting_time_needs_minimum_sample_size():
    posts = [
        Post(posted_at=datetime(2026, 9, 14, 13, 0), views=100, likes=50,
             comments=0, shares=0, saves=0),
        Post(posted_at=datetime(2026, 9, 14, 14, 0), views=100, likes=50,
             comments=0, shares=0, saves=0),
    ]

    assert calculate_best_posting_time(posts) == {
        "available": False,
        "minimum_sample_size": 3,
        "largest_group_size": 2,
    }


def test_best_posting_time_picks_highest_engagement_window():
    def post_at(day, hour, likes):
        return Post(
            posted_at=datetime(2026, 9, day, hour, 15),
            views=100,
            likes=likes,
            comments=0,
            shares=0,
            saves=0,
        )

    posts = (
        # Monday 12:00-2:59 PM: 10% engagement.
        [post_at(14, 13, 10) for _ in range(3)]
        # Tuesday 9:00-11:59 AM: 20% engagement.
        + [post_at(15, 10, 20) for _ in range(3)]
        # Wednesday has 50% engagement but too few posts to count.
        + [post_at(16, 10, 50) for _ in range(2)]
    )

    result = calculate_best_posting_time(posts)

    assert result == {
        "available": True,
        "day": "Tuesday",
        "time_window": "9:00 AM – 11:00 AM",
        "engagement_rate": 20.0,
        "sample_size": 3,
    }


# ---------------------------------------------------------
# HTTP behavior
# ---------------------------------------------------------

def test_analytics_api_summary_respects_filters(client, app):
    with app.app_context():
        add_post(datetime(2026, 9, 10, 12, 0), views=1000, likes=100)
        add_post(datetime(2026, 9, 11, 12, 0), views=500, likes=25)
        add_post(
            datetime(2026, 9, 11, 12, 0),
            platform="Instagram",
            content_type="Reel",
            views=9000,
            likes=900,
        )
        db.session.commit()

    response = client.get(
        "/api/analytics?platform=YouTube&start_date=2026-09-11"
    )

    assert response.status_code == 200
    assert response.get_json()["summary"] == {
        "total_posts": 1,
        "total_views": 500,
        "total_interactions": 25,
        "engagement_rate": 5.0,
    }


@pytest.mark.parametrize(
    "query",
    [
        "start_date=not-a-date",
        "start_date=2026-09-10&end_date=2026-09-01",
    ],
    ids=["invalid-date", "reversed-range"],
)
def test_invalid_filters_return_400(client, query):
    api_response = client.get(f"/api/analytics?{query}")
    export_response = client.get(f"/export-csv?{query}")

    assert api_response.status_code == 400
    assert "error" in api_response.get_json()

    assert export_response.status_code == 400
    assert export_response.mimetype == "text/plain"

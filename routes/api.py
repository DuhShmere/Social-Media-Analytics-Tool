"""JSON endpoints used by the dashboard and post detail charts."""

from flask import Blueprint, jsonify, request

from extensions import db
from models import MetricSnapshot, Post
from services.analytics import (
    PostFilters,
    apply_post_filters,
    calculate_best_posting_time,
    shorten_title,
    summarize_posts,
)


bp = Blueprint("api", __name__, url_prefix="/api")


@bp.route("/analytics")
def analytics_api():
    try:
        filters = PostFilters.from_args(request.args)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    posts = apply_post_filters(Post.query, filters).all()

    top_posts = sorted(
        posts,
        key=lambda post: post.views or 0,
        reverse=True,
    )[:10]

    chronological_posts = sorted(
        posts,
        key=lambda post: post.posted_at,
    )

    summary = summarize_posts(posts)

    best_time = calculate_best_posting_time(posts)

    return jsonify(
        {
            "summary": {
                "total_posts": summary["total_posts"],
                "total_views": summary["total_views"],
                "total_interactions": summary["total_interactions"],
                "engagement_rate": summary["engagement_rate"],
            },
            "best_time": best_time,
            "top_posts": [
                {
                    "title": post.caption,
                    "short_title": shorten_title(
                        post.caption
                    ),
                    "views": post.views or 0,
                }
                for post in top_posts
            ],
            "engagement_over_time": [
                {
                    "title": post.caption,
                    "date": post.posted_at.strftime(
                        "%b %d, %Y"
                    ),
                    "engagement_rate": post.engagement_rate,
                }
                for post in chronological_posts
            ],
            "interactions": {
                "likes": summary["likes"],
                "comments": summary["comments"],
                "shares": summary["shares"],
                "saves": summary["saves"],
            },
        }
    )


@bp.get("/posts/<int:post_id>/history")
def post_history_api(post_id):
    post = db.get_or_404(Post, post_id)

    snapshots = MetricSnapshot.query.filter_by(
        post_id=post.id
    ).order_by(
        MetricSnapshot.captured_at.asc(),
        MetricSnapshot.id.asc(),
    ).all()

    return jsonify(
        {
            "post": {
                "id": post.id,
                "caption": post.caption,
                "platform": post.platform,
            },
            "snapshots": [
                {
                    "captured_at": snapshot.captured_at.isoformat(),
                    "label": snapshot.captured_at.strftime(
                        "%b %d, %Y %I:%M %p"
                    ),
                    "views": snapshot.views,
                    "likes": snapshot.likes,
                    "comments": snapshot.comments,
                    "shares": snapshot.shares,
                    "saves": snapshot.saves,
                    "total_interactions": (
                        snapshot.total_interactions
                    ),
                    "engagement_rate": (
                        snapshot.engagement_rate
                    ),
                }
                for snapshot in snapshots
            ],
        }
    )

"""Dashboard, connected accounts, and post detail pages."""

from flask import Blueprint, render_template

from extensions import db
from models import MetricSnapshot, Post, SocialAccount
from services.analytics import calculate_metric_change, summarize_posts


bp = Blueprint("main", __name__)


@bp.route("/")
def dashboard():
    posts = Post.query.order_by(
        Post.posted_at.desc()
    ).all()

    social_accounts = SocialAccount.query.order_by(
        SocialAccount.platform,
        SocialAccount.account_name,
    ).all()

    platforms = sorted(
        {
            post.platform
            for post in posts
            if post.platform
        }
    )

    content_types = sorted(
        {
            post.content_type
            for post in posts
            if post.content_type
        }
    )

    summary = summarize_posts(posts)

    return render_template(
        "dashboard.html",
        posts=posts,
        social_accounts=social_accounts,
        platforms=platforms,
        content_types=content_types,
        total_posts=summary["total_posts"],
        total_views=summary["total_views"],
        total_interactions=summary["total_interactions"],
        average_engagement_rate=summary["engagement_rate"],
    )


@bp.route("/accounts")
def accounts():
    social_accounts = SocialAccount.query.order_by(
        SocialAccount.platform,
        SocialAccount.account_name,
    ).all()

    return render_template(
        "accounts.html",
        social_accounts=social_accounts,
    )


@bp.route("/posts/<int:post_id>")
def post_detail(post_id):
    post = db.get_or_404(Post, post_id)

    snapshots = MetricSnapshot.query.filter_by(
        post_id=post.id
    ).order_by(
        MetricSnapshot.captured_at.asc(),
        MetricSnapshot.id.asc(),
    ).all()

    latest_snapshot = snapshots[-1] if snapshots else None
    previous_snapshot = (
        snapshots[-2]
        if len(snapshots) >= 2
        else None
    )

    changes = None

    if latest_snapshot and previous_snapshot:
        changes = {
            "views": calculate_metric_change(
                latest_snapshot.views,
                previous_snapshot.views,
            ),
            "interactions": calculate_metric_change(
                latest_snapshot.total_interactions,
                previous_snapshot.total_interactions,
            ),
            "engagement": calculate_metric_change(
                latest_snapshot.engagement_rate,
                previous_snapshot.engagement_rate,
            ),
        }

    return render_template(
        "post_detail.html",
        post=post,
        snapshots=snapshots,
        latest_snapshot=latest_snapshot,
        previous_snapshot=previous_snapshot,
        changes=changes,
    )


@bp.get("/terms")
def terms():
    return render_template("legal/terms.html")


@bp.get("/privacy")
def privacy():
    return render_template("legal/privacy.html")

import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from extensions import db, migrate
from models import (
    MetricSnapshot,
    Post,
    SocialAccount,
    record_metric_snapshot,
)
from services.analytics import (
    PostFilters,
    apply_post_filters,
    calculate_best_posting_time,
    calculate_metric_change,
    shorten_title,
    summarize_posts,
)
from services.csv_service import (
    export_posts_csv,
    import_posts_from_csv,
    sample_csv_content,
)
from services.youtube import (
    ApiKeyRedactionFilter,
    YouTubeClient,
    YouTubeError,
    normalize_handle,
    sync_channel,
)
from validators import parse_post_form


# ---------------------------------------------------------
# Application setup
# ---------------------------------------------------------

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")

load_dotenv(ENV_PATH)

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL",
    "sqlite:///tracker.db",
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

# Errors from the YouTube client can include the API key in a URL.
app.logger.addFilter(ApiKeyRedactionFilter())

db.init_app(app)
migrate.init_app(app, db)

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")


# ---------------------------------------------------------
# General helper functions
# ---------------------------------------------------------

# ---------------------------------------------------------
# Dashboard
# ---------------------------------------------------------

@app.route("/")
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


# ---------------------------------------------------------
# Analytics API
# ---------------------------------------------------------

@app.route("/api/analytics")
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


# ---------------------------------------------------------
# Post performance history
# ---------------------------------------------------------

@app.route("/posts/<int:post_id>")
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


@app.get("/api/posts/<int:post_id>/history")
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


# ---------------------------------------------------------
# Connected accounts
# ---------------------------------------------------------

@app.route("/accounts")
def accounts():
    social_accounts = SocialAccount.query.order_by(
        SocialAccount.platform,
        SocialAccount.account_name,
    ).all()

    return render_template(
        "accounts.html",
        social_accounts=social_accounts,
    )


# ---------------------------------------------------------
# Add manual post
# ---------------------------------------------------------

@app.route("/add-post", methods=["GET", "POST"])
def add_post():
    error = None

    if request.method == "POST":
        try:
            post = Post(
                **parse_post_form(request.form),
                source="manual",
            )

            db.session.add(post)
            record_metric_snapshot(post)
            db.session.commit()

            return redirect(url_for("dashboard"))

        except ValueError as validation_error:
            db.session.rollback()
            error = str(validation_error)

    return render_template(
        "add_post.html",
        error=error,
    )


# ---------------------------------------------------------
# Edit manual or CSV post
# ---------------------------------------------------------

@app.route(
    "/posts/<int:post_id>/edit",
    methods=["GET", "POST"],
)
def edit_post(post_id):
    post = db.get_or_404(Post, post_id)

    if not post.is_editable:
        return redirect(url_for("dashboard"))

    error = None

    if request.method == "POST":
        try:
            # Validate everything before changing the post so a
            # bad submission never leaves it half-updated.
            values = parse_post_form(request.form)

            for field, value in values.items():
                setattr(post, field, value)

            record_metric_snapshot(post)

            db.session.commit()

            return redirect(url_for("dashboard"))

        except ValueError as validation_error:
            db.session.rollback()
            error = str(validation_error)

    return render_template(
        "edit_post.html",
        post=post,
        error=error,
    )


# ---------------------------------------------------------
# Delete manual or CSV post
# ---------------------------------------------------------

@app.post("/posts/<int:post_id>/delete")
def delete_post(post_id):
    post = db.get_or_404(Post, post_id)

    if not post.is_editable:
        return redirect(url_for("dashboard"))

    db.session.delete(post)
    db.session.commit()

    return redirect(url_for("dashboard"))


# ---------------------------------------------------------
# CSV import
# ---------------------------------------------------------

@app.route("/import-csv", methods=["GET", "POST"])
def import_csv():
    if request.method == "GET":
        return render_template("import_csv.html")

    uploaded_file = request.files.get("csv_file")

    if not uploaded_file or not uploaded_file.filename:
        return render_template(
            "import_csv.html",
            error="Select a CSV file to upload.",
        )

    if not uploaded_file.filename.lower().endswith(".csv"):
        return render_template(
            "import_csv.html",
            error="The uploaded file must be a CSV file.",
        )

    try:
        result = import_posts_from_csv(
            uploaded_file.stream,
            db.session,
        )
        db.session.commit()

    except ValueError as error:
        db.session.rollback()

        return render_template(
            "import_csv.html",
            error=str(error),
        )

    except Exception:
        db.session.rollback()

        app.logger.exception(
            "Unexpected CSV import error."
        )

        return render_template(
            "import_csv.html",
            error=(
                "The CSV could not be imported. "
                "Check the file and try again."
            ),
        )

    app.logger.info(
        "CSV import finished: %s imported, %s skipped, %s invalid.",
        result.imported,
        result.skipped,
        len(result.errors),
    )

    return render_template(
        "import_csv.html",
        result=result.as_template_data(),
    )


# ---------------------------------------------------------
# CSV export
# ---------------------------------------------------------

@app.route("/export-csv")
def export_csv():
    try:
        filters = PostFilters.from_args(request.args)
    except ValueError as error:
        return Response(
            str(error),
            status=400,
            mimetype="text/plain",
        )

    posts = apply_post_filters(Post.query, filters).order_by(
        Post.posted_at.desc()
    ).all()

    filename = (
        "social-media-analytics-"
        f"{datetime.now().strftime('%Y-%m-%d')}.csv"
    )

    return Response(
        export_posts_csv(posts),
        mimetype="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename}"'
            )
        },
    )


# ---------------------------------------------------------
# Sample CSV
# ---------------------------------------------------------

@app.route("/sample-csv")
def sample_csv():
    return Response(
        sample_csv_content(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": (
                'attachment; filename="sample-posts.csv"'
            )
        },
    )


# ---------------------------------------------------------
# YouTube import and synchronization
# ---------------------------------------------------------

@app.route("/import-youtube", methods=["GET", "POST"])
def import_youtube():
    if request.method == "GET":
        return render_template("import_youtube.html")

    if not YOUTUBE_API_KEY:
        return render_template(
            "import_youtube.html",
            error=(
                "The YouTube API key is missing. Add "
                "YOUTUBE_API_KEY to your .env file."
            ),
        )

    try:
        handle = normalize_handle(
            request.form.get("channel_handle")
        )

        result = sync_channel(
            YouTubeClient(YOUTUBE_API_KEY),
            handle,
            db.session,
            synced_at=datetime.now(timezone.utc).replace(
                tzinfo=None
            ),
        )

        db.session.commit()

    except YouTubeError as error:
        db.session.rollback()

        # Expected failures (quota, bad handle, network) are logged
        # without a traceback. The cause holds the original error.
        app.logger.warning(
            "YouTube sync failed: %s (cause: %r)",
            error,
            error.__cause__,
        )

        return render_template(
            "import_youtube.html",
            error=str(error),
        )

    except Exception:
        db.session.rollback()

        app.logger.exception(
            "Unexpected YouTube import error."
        )

        return render_template(
            "import_youtube.html",
            error=(
                "An unexpected error occurred while importing "
                "the channel. Check the terminal for details."
            ),
        )

    app.logger.info(
        "YouTube sync finished for %s: %s imported, %s updated.",
        result.channel_name,
        result.imported,
        result.updated,
    )

    return render_template(
        "import_youtube.html",
        success=(
            f"Imported {result.imported} new videos and "
            f"updated {result.updated} existing videos "
            f"from {result.channel_name}."
        ),
    )


# ---------------------------------------------------------
# Error pages
# ---------------------------------------------------------

def wants_json_response():
    return request.path.startswith("/api/")


@app.errorhandler(404)
def page_not_found(error):
    if wants_json_response():
        return jsonify({"error": "Not found."}), 404

    return render_template("errors/404.html"), 404


@app.errorhandler(500)
def internal_server_error(error):
    # Flask has already logged the exception. Roll back so a failed
    # transaction is never left open on the session.
    db.session.rollback()

    if wants_json_response():
        return jsonify({"error": "Internal server error."}), 500

    return render_template("errors/500.html"), 500


# ---------------------------------------------------------
# Start application
# ---------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, port=5001)

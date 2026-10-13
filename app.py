import csv
import hashlib
import io
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
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from extensions import db, migrate
from models import MetricSnapshot, Post, SocialAccount
from services.analytics import (
    PostFilters,
    apply_post_filters,
    calculate_best_posting_time,
    calculate_metric_change,
    shorten_title,
    summarize_posts,
)
from validators import is_safe_external_url


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

db.init_app(app)
migrate.init_app(app, db)

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")


# ---------------------------------------------------------
# General helper functions
# ---------------------------------------------------------

def record_metric_snapshot(post, captured_at=None):
    snapshot = MetricSnapshot(
        captured_at=(
            captured_at
            or datetime.now(timezone.utc).replace(tzinfo=None)
        ),
        views=post.views or 0,
        likes=post.likes or 0,
        comments=post.comments or 0,
        shares=post.shares or 0,
        saves=post.saves or 0,
    )

    post.metric_snapshots.append(snapshot)
    return snapshot


def parse_non_negative_integer(value):
    if value is None or str(value).strip() == "":
        return 0

    number = int(str(value).strip())

    if number < 0:
        raise ValueError("Metrics cannot be negative.")

    return number


def parse_csv_datetime(value):
    value = value.strip()

    if not value:
        raise ValueError("posted_at is required.")

    parsed = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(
            timezone.utc
        ).replace(tzinfo=None)

    return parsed


def generate_csv_external_id(
    platform,
    caption,
    content_type,
    posted_at,
):
    identifying_value = "|".join(
        [
            platform.lower(),
            caption.lower(),
            content_type.lower(),
            posted_at.isoformat(),
        ]
    )

    digest = hashlib.sha256(
        identifying_value.encode("utf-8")
    ).hexdigest()

    return f"csv-{digest[:32]}"


def validate_text_length(value, field_name, limit):
    if len(value) > limit:
        raise ValueError(
            f"{field_name} cannot exceed {limit} characters."
        )


METRIC_FIELDS = ("views", "likes", "comments", "shares", "saves")


def parse_post_form(form):
    """Validate the add/edit post form and return model field values.

    Raises ValueError with a message that is safe to show the user.
    """
    platform = form.get("platform", "").strip()
    caption = form.get("caption", "").strip()
    content_type = form.get("content_type", "").strip()

    if not platform or not caption or not content_type:
        raise ValueError(
            "Platform, caption, and content type are required."
        )

    validate_text_length(platform, "Platform", 50)
    validate_text_length(caption, "Caption", 300)
    validate_text_length(content_type, "Content type", 50)

    try:
        posted_at = datetime.strptime(
            form.get("posted_at", ""),
            "%Y-%m-%dT%H:%M",
        )
    except ValueError:
        raise ValueError(
            "Enter a valid date and time for when the post "
            "was published."
        ) from None

    values = {
        "platform": platform,
        "caption": caption,
        "content_type": content_type,
        "posted_at": posted_at,
    }

    for field in METRIC_FIELDS:
        try:
            values[field] = parse_non_negative_integer(
                form.get(field)
            )
        except ValueError:
            raise ValueError(
                f"{field.capitalize()} must be a whole number "
                "of 0 or more."
            ) from None

    return values


def parse_external_url(value):
    value = (value or "").strip()

    if not value:
        return None

    validate_text_length(value, "external_url", 500)

    if not is_safe_external_url(value):
        raise ValueError(
            "external_url must be a full http:// or https:// link."
        )

    return value


def get_thumbnail_url(thumbnails):
    for size in ("high", "medium", "default"):
        thumbnail = thumbnails.get(size)

        if thumbnail and thumbnail.get("url"):
            return thumbnail["url"]

    return None


def get_youtube_error_message(error):
    status_code = getattr(
        error.resp,
        "status",
        None,
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

    return (
        "YouTube could not complete the request. "
        "Please try again."
    )


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
    required_columns = {
        "platform",
        "caption",
        "content_type",
        "posted_at",
        "views",
        "likes",
        "comments",
        "shares",
        "saves",
    }

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
        text_stream = io.TextIOWrapper(
            uploaded_file.stream,
            encoding="utf-8-sig",
            newline="",
        )

        reader = csv.DictReader(text_stream)

        if not reader.fieldnames:
            raise ValueError(
                "The CSV file does not contain a header row."
            )

        fieldnames = {
            field.strip()
            for field in reader.fieldnames
            if field
        }

        missing_columns = required_columns - fieldnames

        if missing_columns:
            missing_text = ", ".join(
                sorted(missing_columns)
            )

            raise ValueError(
                f"Missing required columns: {missing_text}"
            )

        imported_count = 0
        skipped_count = 0
        row_errors = []

        for row_number, row in enumerate(
            reader,
            start=2,
        ):
            try:
                platform = row.get(
                    "platform",
                    "",
                ).strip()

                caption = row.get(
                    "caption",
                    "",
                ).strip()

                content_type = row.get(
                    "content_type",
                    "",
                ).strip()

                if not platform:
                    raise ValueError(
                        "platform is required."
                    )

                if not caption:
                    raise ValueError(
                        "caption is required."
                    )

                if not content_type:
                    raise ValueError(
                        "content_type is required."
                    )

                validate_text_length(
                    platform,
                    "platform",
                    50,
                )

                validate_text_length(
                    caption,
                    "caption",
                    300,
                )

                validate_text_length(
                    content_type,
                    "content_type",
                    50,
                )

                posted_at = parse_csv_datetime(
                    row.get("posted_at", "")
                )

                views = parse_non_negative_integer(
                    row.get("views")
                )

                likes = parse_non_negative_integer(
                    row.get("likes")
                )

                comments = parse_non_negative_integer(
                    row.get("comments")
                )

                shares = parse_non_negative_integer(
                    row.get("shares")
                )

                saves = parse_non_negative_integer(
                    row.get("saves")
                )

                external_id = row.get(
                    "external_id",
                    "",
                ).strip()

                if not external_id:
                    external_id = generate_csv_external_id(
                        platform,
                        caption,
                        content_type,
                        posted_at,
                    )

                external_url = parse_external_url(
                    row.get("external_url")
                )

                duplicate = Post.query.filter_by(
                    platform=platform,
                    external_id=external_id,
                ).first()

                if duplicate:
                    skipped_count += 1
                    continue

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
                    external_id=external_id,
                    external_url=external_url,
                    source="csv_import",
                )

                db.session.add(post)
                record_metric_snapshot(post)
                imported_count += 1

            except (ValueError, TypeError) as error:
                row_errors.append(
                    f"Row {row_number}: {error}"
                )

        db.session.commit()

        return render_template(
            "import_csv.html",
            result={
                "imported": imported_count,
                "skipped": skipped_count,
                "invalid": len(row_errors),
                "errors": row_errors[:20],
            },
        )

    except (UnicodeDecodeError, csv.Error, ValueError) as error:
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

    output = io.StringIO()

    fieldnames = [
        "platform",
        "caption",
        "content_type",
        "posted_at",
        "views",
        "likes",
        "comments",
        "shares",
        "saves",
        "external_id",
        "external_url",
        "source",
    ]

    writer = csv.DictWriter(
        output,
        fieldnames=fieldnames,
    )

    writer.writeheader()

    for post in posts:
        writer.writerow(
            {
                "platform": post.platform,
                "caption": post.caption,
                "content_type": post.content_type,
                "posted_at": post.posted_at.isoformat(
                    sep=" ",
                    timespec="minutes",
                ),
                "views": post.views,
                "likes": post.likes,
                "comments": post.comments,
                "shares": post.shares,
                "saves": post.saves,
                "external_id": post.external_id or "",
                "external_url": post.external_url or "",
                "source": post.source,
            }
        )

    filename = (
        "social-media-analytics-"
        f"{datetime.now().strftime('%Y-%m-%d')}.csv"
    )

    return Response(
        output.getvalue(),
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
    output = io.StringIO()

    writer = csv.writer(output)

    writer.writerow(
        [
            "platform",
            "caption",
            "content_type",
            "posted_at",
            "views",
            "likes",
            "comments",
            "shares",
            "saves",
            "external_id",
            "external_url",
        ]
    )

    writer.writerow(
        [
            "Instagram",
            "Product launch",
            "Carousel",
            "2026-09-15 14:30",
            "12000",
            "850",
            "63",
            "42",
            "130",
            "instagram-example-101",
            "https://instagram.com/p/example",
        ]
    )

    writer.writerow(
        [
            "TikTok",
            "Behind the scenes",
            "Video",
            "2026-09-17 18:00",
            "43000",
            "5100",
            "280",
            "430",
            "0",
            "tiktok-example-202",
            "https://tiktok.com/@example/video/202",
        ]
    )

    return Response(
        output.getvalue(),
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

    channel_handle = request.form.get(
        "channel_handle",
        "",
    ).strip()

    if not channel_handle:
        return render_template(
            "import_youtube.html",
            error="Enter a YouTube channel handle.",
        )

    channel_handle = channel_handle.lstrip("@")

    try:
        youtube = build(
            "youtube",
            "v3",
            developerKey=YOUTUBE_API_KEY,
            cache_discovery=False,
        )

        channel_response = youtube.channels().list(
            part="snippet,contentDetails,statistics",
            forHandle=channel_handle,
            maxResults=1,
        ).execute()

        channels = channel_response.get("items", [])

        if not channels:
            return render_template(
                "import_youtube.html",
                error=(
                    "YouTube channel not found. Check the "
                    "channel handle and try again."
                ),
            )

        channel = channels[0]
        channel_id = channel["id"]
        channel_name = channel["snippet"]["title"]
        channel_handle_with_at = f"@{channel_handle}"

        social_account = SocialAccount.query.filter_by(
            platform="YouTube",
            external_account_id=channel_id,
        ).first()

        if social_account is None:
            social_account = SocialAccount(
                platform="YouTube",
                account_name=channel_name,
                account_handle=channel_handle_with_at,
                external_account_id=channel_id,
            )

            db.session.add(social_account)

        else:
            social_account.account_name = channel_name
            social_account.account_handle = (
                channel_handle_with_at
            )

        sync_time = datetime.now(
            timezone.utc
        ).replace(tzinfo=None)

        social_account.last_synced_at = sync_time

        db.session.flush()

        uploads_playlist_id = channel[
            "contentDetails"
        ]["relatedPlaylists"]["uploads"]

        playlist_response = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=uploads_playlist_id,
            maxResults=50,
        ).execute()

        video_ids = [
            item["contentDetails"]["videoId"]
            for item in playlist_response.get("items", [])
        ]

        if not video_ids:
            db.session.rollback()

            return render_template(
                "import_youtube.html",
                error=(
                    "No public videos were found for this "
                    "YouTube channel."
                ),
            )

        video_response = youtube.videos().list(
            part="snippet,statistics",
            id=",".join(video_ids),
        ).execute()

        imported_count = 0
        updated_count = 0

        for video in video_response.get("items", []):
            video_id = video["id"]
            snippet = video.get("snippet", {})
            statistics = video.get("statistics", {})

            published_at = datetime.fromisoformat(
                snippet["publishedAt"].replace(
                    "Z",
                    "+00:00",
                )
            ).replace(tzinfo=None)

            post = Post.query.filter_by(
                platform="YouTube",
                external_id=video_id,
            ).first()

            if post is None:
                post = Post(
                    platform="YouTube",
                    caption=snippet.get(
                        "title",
                        "Untitled YouTube video",
                    ),
                    content_type="Video",
                    posted_at=published_at,
                    external_id=video_id,
                    source="youtube_api",
                    social_account=social_account,
                )

                db.session.add(post)
                imported_count += 1

            else:
                updated_count += 1

            post.platform = "YouTube"
            post.caption = snippet.get(
                "title",
                "Untitled YouTube video",
            )
            post.content_type = "Video"
            post.posted_at = published_at
            post.views = int(
                statistics.get("viewCount", 0)
            )
            post.likes = int(
                statistics.get("likeCount", 0)
            )
            post.comments = int(
                statistics.get("commentCount", 0)
            )
            post.shares = 0
            post.saves = 0
            post.external_url = (
                f"https://www.youtube.com/watch?v={video_id}"
            )
            post.thumbnail_url = get_thumbnail_url(
                snippet.get("thumbnails", {})
            )
            post.source = "youtube_api"
            post.social_account = social_account

            record_metric_snapshot(
                post,
                captured_at=sync_time,
            )

        db.session.commit()

        return render_template(
            "import_youtube.html",
            success=(
                f"Imported {imported_count} new videos and "
                f"updated {updated_count} existing videos "
                f"from {channel_name}."
            ),
        )

    except HttpError as error:
        db.session.rollback()

        app.logger.exception(
            "YouTube API request failed."
        )

        return render_template(
            "import_youtube.html",
            error=get_youtube_error_message(error),
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

import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


# ---------------------------------------------------------
# Application setup
# ---------------------------------------------------------

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")

load_dotenv(ENV_PATH)

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///tracker.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")


# ---------------------------------------------------------
# Database models
# ---------------------------------------------------------

class SocialAccount(db.Model):
    __tablename__ = "social_account"

    __table_args__ = (
        db.UniqueConstraint(
            "platform",
            "external_account_id",
            name="uq_social_account_platform_external_id",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    platform = db.Column(
        db.String(50),
        nullable=False,
    )

    account_name = db.Column(
        db.String(150),
        nullable=False,
    )

    account_handle = db.Column(
        db.String(150),
        nullable=True,
    )

    external_account_id = db.Column(
        db.String(150),
        nullable=False,
    )

    last_synced_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    posts = db.relationship(
        "Post",
        back_populates="social_account",
        lazy=True,
    )


class Post(db.Model):
    __tablename__ = "post"

    __table_args__ = (
        db.UniqueConstraint(
            "platform",
            "external_id",
            name="uq_post_platform_external_id",
        ),
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    platform = db.Column(
        db.String(50),
        nullable=False,
    )

    caption = db.Column(
        db.String(300),
        nullable=False,
    )

    content_type = db.Column(
        db.String(50),
        nullable=False,
    )

    posted_at = db.Column(
        db.DateTime,
        nullable=False,
    )

    views = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    likes = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    comments = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    shares = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    saves = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    external_id = db.Column(
        db.String(150),
        nullable=True,
    )

    external_url = db.Column(
        db.String(500),
        nullable=True,
    )

    thumbnail_url = db.Column(
        db.String(500),
        nullable=True,
    )

    source = db.Column(
        db.String(30),
        nullable=False,
        default="manual",
    )

    social_account_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "social_account.id",
            name="fk_post_social_account_id",
        ),
        nullable=True,
    )

    social_account = db.relationship(
        "SocialAccount",
        back_populates="posts",
    )

    @property
    def total_interactions(self):
        return (
            (self.likes or 0)
            + (self.comments or 0)
            + (self.shares or 0)
            + (self.saves or 0)
        )

    @property
    def engagement_rate(self):
        if not self.views:
            return 0

        return round(
            (self.total_interactions / self.views) * 100,
            2,
        )


# ---------------------------------------------------------
# Helper functions
# ---------------------------------------------------------

def parse_non_negative_integer(value):
    if value is None or value.strip() == "":
        return 0

    number = int(value)

    if number < 0:
        raise ValueError("Metrics cannot be negative.")

    return number


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


def shorten_title(title, maximum_length=35):
    if len(title) <= maximum_length:
        return title

    return f"{title[:maximum_length - 3]}..."


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

    total_posts = len(posts)

    total_views = sum(
        post.views or 0
        for post in posts
    )

    total_interactions = sum(
        post.total_interactions
        for post in posts
    )

    average_engagement_rate = (
        round(
            (total_interactions / total_views) * 100,
            2,
        )
        if total_views > 0
        else 0
    )

    return render_template(
        "dashboard.html",
        posts=posts,
        social_accounts=social_accounts,
        total_posts=total_posts,
        total_views=total_views,
        total_interactions=total_interactions,
        average_engagement_rate=average_engagement_rate,
    )


# ---------------------------------------------------------
# Analytics API
# ---------------------------------------------------------

@app.route("/api/analytics")
def analytics_api():
    account_id = request.args.get(
        "account_id",
        type=int,
    )

    query = Post.query

    if account_id is not None:
        query = query.filter(
            Post.social_account_id == account_id
        )

    posts = query.all()

    top_posts = sorted(
        posts,
        key=lambda post: post.views or 0,
        reverse=True,
    )[:10]

    chronological_posts = sorted(
        posts,
        key=lambda post: post.posted_at,
    )

    likes = sum(
        post.likes or 0
        for post in posts
    )

    comments = sum(
        post.comments or 0
        for post in posts
    )

    shares = sum(
        post.shares or 0
        for post in posts
    )

    saves = sum(
        post.saves or 0
        for post in posts
    )

    total_views = sum(
        post.views or 0
        for post in posts
    )

    total_interactions = (
        likes
        + comments
        + shares
        + saves
    )

    overall_engagement_rate = (
        round(
            (total_interactions / total_views) * 100,
            2,
        )
        if total_views > 0
        else 0
    )

    return jsonify(
        {
            "summary": {
                "total_posts": len(posts),
                "total_views": total_views,
                "total_interactions": total_interactions,
                "engagement_rate": overall_engagement_rate,
            },
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
                    "engagement_rate": (
                        post.engagement_rate
                    ),
                }
                for post in chronological_posts
            ],
            "interactions": {
                "likes": likes,
                "comments": comments,
                "shares": shares,
                "saves": saves,
            },
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
# Add a manual post
# ---------------------------------------------------------

@app.route("/add-post", methods=["GET", "POST"])
def add_post():
    error = None

    if request.method == "POST":
        try:
            platform = request.form["platform"].strip()
            caption = request.form["caption"].strip()
            content_type = request.form[
                "content_type"
            ].strip()

            posted_at = datetime.strptime(
                request.form["posted_at"],
                "%Y-%m-%dT%H:%M",
            )

            views = parse_non_negative_integer(
                request.form.get("views")
            )

            likes = parse_non_negative_integer(
                request.form.get("likes")
            )

            comments = parse_non_negative_integer(
                request.form.get("comments")
            )

            shares = parse_non_negative_integer(
                request.form.get("shares")
            )

            saves = parse_non_negative_integer(
                request.form.get("saves")
            )

            if not platform:
                raise ValueError("Platform is required.")

            if not caption:
                raise ValueError("Caption is required.")

            if not content_type:
                raise ValueError(
                    "Content type is required."
                )

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
                external_id=None,
                external_url=None,
                thumbnail_url=None,
                source="manual",
                social_account_id=None,
            )

            db.session.add(post)
            db.session.commit()

            return redirect(url_for("dashboard"))

        except (ValueError, KeyError):
            db.session.rollback()

            error = (
                "Please complete every required field and use "
                "non-negative numbers for all metrics."
            )

    return render_template(
        "add_post.html",
        error=error,
    )


# ---------------------------------------------------------
# Edit a manual post
# ---------------------------------------------------------

@app.route(
    "/posts/<int:post_id>/edit",
    methods=["GET", "POST"],
)
def edit_post(post_id):
    post = db.get_or_404(Post, post_id)

    # YouTube posts must be updated through synchronization.
    if post.source != "manual":
        return redirect(url_for("dashboard"))

    error = None

    if request.method == "POST":
        try:
            platform = request.form["platform"].strip()
            caption = request.form["caption"].strip()
            content_type = request.form[
                "content_type"
            ].strip()

            posted_at = datetime.strptime(
                request.form["posted_at"],
                "%Y-%m-%dT%H:%M",
            )

            views = parse_non_negative_integer(
                request.form.get("views")
            )

            likes = parse_non_negative_integer(
                request.form.get("likes")
            )

            comments = parse_non_negative_integer(
                request.form.get("comments")
            )

            shares = parse_non_negative_integer(
                request.form.get("shares")
            )

            saves = parse_non_negative_integer(
                request.form.get("saves")
            )

            if not platform:
                raise ValueError("Platform is required.")

            if not caption:
                raise ValueError("Caption is required.")

            if not content_type:
                raise ValueError(
                    "Content type is required."
                )

            post.platform = platform
            post.caption = caption
            post.content_type = content_type
            post.posted_at = posted_at
            post.views = views
            post.likes = likes
            post.comments = comments
            post.shares = shares
            post.saves = saves

            db.session.commit()

            return redirect(url_for("dashboard"))

        except (ValueError, KeyError):
            db.session.rollback()

            error = (
                "Please complete every required field and use "
                "non-negative numbers for all metrics."
            )

    return render_template(
        "edit_post.html",
        post=post,
        error=error,
    )


# ---------------------------------------------------------
# Delete a manual post
# ---------------------------------------------------------

@app.post("/posts/<int:post_id>/delete")
def delete_post(post_id):
    post = db.get_or_404(Post, post_id)

    # Imported posts must be managed through their platform.
    if post.source != "manual":
        return redirect(url_for("dashboard"))

    db.session.delete(post)
    db.session.commit()

    return redirect(url_for("dashboard"))


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

        social_account.last_synced_at = datetime.now(
            timezone.utc
        ).replace(tzinfo=None)

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
            statistics = video.get(
                "statistics",
                {},
            )

            published_at = datetime.fromisoformat(
                snippet["publishedAt"].replace(
                    "Z",
                    "+00:00",
                )
            ).replace(tzinfo=None)

            thumbnail_url = get_thumbnail_url(
                snippet.get("thumbnails", {})
            )

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
                    views=0,
                    likes=0,
                    comments=0,
                    shares=0,
                    saves=0,
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

            post.thumbnail_url = thumbnail_url
            post.source = "youtube_api"
            post.social_account = social_account

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
# Start application
# ---------------------------------------------------------

if __name__ == "__main__":
    with app.app_context():
        db.create_all()

    app.run(debug=True)
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, url_for
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
    db.ForeignKey("social_account.id"),
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


# ---------------------------------------------------------
# Dashboard
# ---------------------------------------------------------

@app.route("/")
def dashboard():
    posts = Post.query.order_by(
        Post.posted_at.desc()
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
        total_posts=total_posts,
        total_views=total_views,
        total_interactions=total_interactions,
        average_engagement_rate=average_engagement_rate,
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
# Manual post entry
# ---------------------------------------------------------

@app.route("/add-post", methods=["GET", "POST"])
def add_post():
    error = None

    if request.method == "POST":
        try:
            platform = request.form["platform"].strip()
            caption = request.form["caption"].strip()
            content_type = request.form["content_type"].strip()

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
                raise ValueError("Content type is required.")

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
# YouTube import
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

    # Accept @GoogleDevelopers or GoogleDevelopers.
    channel_handle = channel_handle.lstrip("@")

    try:
        youtube = build(
            "youtube",
            "v3",
            developerKey=YOUTUBE_API_KEY,
            cache_discovery=False,
        )

        # Retrieve the channel.
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

        # Find or create the connected account.
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

        # Create an account ID before connecting posts.
        db.session.flush()

        uploads_playlist_id = channel[
            "contentDetails"
        ]["relatedPlaylists"]["uploads"]

        # Get the channel's 50 most recent video IDs.
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

        # Retrieve the video details and statistics.
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

            thumbnail_url = get_thumbnail_url(
                snippet.get("thumbnails", {})
            )

            # Find an existing YouTube video.
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

            # Update the post with current YouTube data.
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

            # Public YouTube statistics do not include these.
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
# Start the application
# ---------------------------------------------------------

if __name__ == "__main__":
    with app.app_context():
        db.create_all()

    app.run(debug=True)
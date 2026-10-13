"""CSV import/export and YouTube channel synchronization."""

from datetime import datetime, timezone

from flask import (
    Blueprint,
    Response,
    current_app,
    render_template,
    request,
)

from extensions import db
from models import Post
from services.analytics import PostFilters, apply_post_filters
from services.csv_service import (
    export_posts_csv,
    import_posts_from_csv,
    sample_csv_content,
)
from services.youtube import (
    YouTubeClient,
    YouTubeError,
    normalize_handle,
    sync_channel,
)


bp = Blueprint("imports", __name__)


@bp.route("/import-csv", methods=["GET", "POST"])
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

        current_app.logger.exception(
            "Unexpected CSV import error."
        )

        return render_template(
            "import_csv.html",
            error=(
                "The CSV could not be imported. "
                "Check the file and try again."
            ),
        )

    current_app.logger.info(
        "CSV import finished: %s imported, %s skipped, %s invalid.",
        result.imported,
        result.skipped,
        len(result.errors),
    )

    return render_template(
        "import_csv.html",
        result=result.as_template_data(),
    )


@bp.route("/export-csv")
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


@bp.route("/sample-csv")
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


@bp.route("/import-youtube", methods=["GET", "POST"])
def import_youtube():
    if request.method == "GET":
        return render_template("import_youtube.html")

    api_key = current_app.config.get("YOUTUBE_API_KEY")

    if not api_key:
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
            YouTubeClient(api_key),
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
        current_app.logger.warning(
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

        current_app.logger.exception(
            "Unexpected YouTube import error."
        )

        return render_template(
            "import_youtube.html",
            error=(
                "An unexpected error occurred while importing "
                "the channel. Check the terminal for details."
            ),
        )

    current_app.logger.info(
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

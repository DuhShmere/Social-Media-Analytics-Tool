"""Create, edit, and delete manual or CSV-imported posts."""

from flask import Blueprint, redirect, render_template, request, url_for

from extensions import db
from models import Post, record_metric_snapshot
from validators import parse_post_form


bp = Blueprint("posts", __name__)


@bp.route("/add-post", methods=["GET", "POST"])
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

            return redirect(url_for("main.dashboard"))

        except ValueError as validation_error:
            db.session.rollback()
            error = str(validation_error)

    return render_template(
        "add_post.html",
        error=error,
    )


@bp.route(
    "/posts/<int:post_id>/edit",
    methods=["GET", "POST"],
)
def edit_post(post_id):
    post = db.get_or_404(Post, post_id)

    if not post.is_editable:
        return redirect(url_for("main.dashboard"))

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

            return redirect(url_for("main.dashboard"))

        except ValueError as validation_error:
            db.session.rollback()
            error = str(validation_error)

    return render_template(
        "edit_post.html",
        post=post,
        error=error,
    )


@bp.post("/posts/<int:post_id>/delete")
def delete_post(post_id):
    post = db.get_or_404(Post, post_id)

    if not post.is_editable:
        return redirect(url_for("main.dashboard"))

    db.session.delete(post)
    db.session.commit()

    return redirect(url_for("main.dashboard"))

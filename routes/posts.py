"""Create, edit, and delete manual or CSV-imported posts."""

from flask import Blueprint, redirect, render_template, request, url_for

from extensions import db
from models import Post, record_metric_snapshot
from validators import (
    CONTENT_TYPE_CHOICES,
    METRIC_FIELDS,
    PLATFORM_CHOICES,
    parse_post_form,
)


bp = Blueprint("posts", __name__)


def blank_form_values():
    return {
        "platform": "",
        "caption": "",
        "content_type": "",
        "posted_at": "",
        **{field: "0" for field in METRIC_FIELDS},
    }


def post_form_values(post):
    return {
        "platform": post.platform,
        "caption": post.caption,
        "content_type": post.content_type,
        "posted_at": post.posted_at.strftime("%Y-%m-%dT%H:%M"),
        **{field: str(getattr(post, field)) for field in METRIC_FIELDS},
    }


def submitted_form_values():
    # Show the user exactly what they typed so a mistake in one field
    # does not erase the others.
    return {**blank_form_values(), **request.form.to_dict()}


def with_current_value(choices, current_value):
    if current_value and current_value not in choices:
        return [*choices, current_value]

    return list(choices)


def render_post_form(template, values, error, **context):
    return render_template(
        template,
        values=values,
        error=error,
        platform_choices=with_current_value(
            PLATFORM_CHOICES,
            values["platform"],
        ),
        content_type_choices=with_current_value(
            CONTENT_TYPE_CHOICES,
            values["content_type"],
        ),
        **context,
    )


@bp.route("/add-post", methods=["GET", "POST"])
def add_post():
    if request.method == "GET":
        return render_post_form("add_post.html", blank_form_values(), None)

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

        return render_post_form(
            "add_post.html",
            submitted_form_values(),
            str(validation_error),
        )


@bp.route(
    "/posts/<int:post_id>/edit",
    methods=["GET", "POST"],
)
def edit_post(post_id):
    post = db.get_or_404(Post, post_id)

    if not post.is_editable:
        return redirect(url_for("main.dashboard"))

    if request.method == "GET":
        return render_post_form(
            "edit_post.html",
            post_form_values(post),
            None,
            post=post,
        )

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

        return render_post_form(
            "edit_post.html",
            submitted_form_values(),
            str(validation_error),
            post=post,
        )


@bp.post("/posts/<int:post_id>/delete")
def delete_post(post_id):
    post = db.get_or_404(Post, post_id)

    if not post.is_editable:
        return redirect(url_for("main.dashboard"))

    db.session.delete(post)
    db.session.commit()

    return redirect(url_for("main.dashboard"))

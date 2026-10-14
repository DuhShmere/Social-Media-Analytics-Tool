"""Input validation helpers that do not depend on Flask.

Every ValueError raised here has a message that is safe to show
to the user.
"""

from datetime import datetime
from urllib.parse import urlsplit


METRIC_FIELDS = ("views", "likes", "comments", "shares", "saves")

# Suggested choices for the add/edit form. Posts imported from CSV can
# hold other values, so the form always includes the post's own value.
PLATFORM_CHOICES = ("Instagram", "TikTok", "YouTube", "LinkedIn", "X")
CONTENT_TYPE_CHOICES = ("Video", "Short", "Reel", "Image", "Carousel", "Text")


def is_safe_external_url(value):
    if not value:
        return False

    try:
        parts = urlsplit(value)
    except ValueError:
        return False

    return parts.scheme.lower() in {"http", "https"} and bool(
        parts.netloc
    )


def parse_non_negative_integer(value):
    if value is None or str(value).strip() == "":
        return 0

    number = int(str(value).strip())

    if number < 0:
        raise ValueError("Metrics cannot be negative.")

    return number


def parse_metric(value, label):
    try:
        return parse_non_negative_integer(value)
    except ValueError:
        raise ValueError(
            f"{label} must be a whole number of 0 or more."
        ) from None


def validate_text_length(value, field_name, limit):
    if len(value) > limit:
        raise ValueError(
            f"{field_name} cannot exceed {limit} characters."
        )


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


def parse_post_form(form):
    """Validate the add/edit post form and return model field values."""
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
        values[field] = parse_metric(
            form.get(field),
            field.capitalize(),
        )

    return values

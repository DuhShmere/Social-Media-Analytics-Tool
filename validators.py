"""Small validation helpers that do not depend on Flask."""

from urllib.parse import urlsplit


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

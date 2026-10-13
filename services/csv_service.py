"""CSV import and export for posts.

import_posts_from_csv() adds posts to the database session but does
not commit. The caller owns the transaction, so a file-level error
can roll back the whole import.
"""

import csv
import hashlib
import io
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func

from models import Post, record_metric_snapshot
from validators import (
    METRIC_FIELDS,
    parse_external_url,
    parse_metric,
    validate_text_length,
)


REQUIRED_COLUMNS = (
    "platform",
    "caption",
    "content_type",
    "posted_at",
    *METRIC_FIELDS,
)

OPTIONAL_COLUMNS = ("external_id", "external_url")

EXPORT_COLUMNS = (*REQUIRED_COLUMNS, *OPTIONAL_COLUMNS, "source")

MAX_REPORTED_ERRORS = 20

# Spreadsheet apps run a cell that starts with one of these as a
# formula. Exported text cells that start with one get a leading
# apostrophe so they open as plain text (OWASP CSV injection).
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

SAMPLE_ROWS = (
    (
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
    ),
    (
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
    ),
)


@dataclass
class CsvImportResult:
    imported: int = 0
    skipped: int = 0
    errors: list = field(default_factory=list)

    def as_template_data(self):
        return {
            "imported": self.imported,
            "skipped": self.skipped,
            "invalid": len(self.errors),
            "errors": self.errors[:MAX_REPORTED_ERRORS],
        }


def parse_csv_datetime(value):
    value = value.strip()

    if not value:
        raise ValueError("posted_at is required.")

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError:
        raise ValueError(
            "posted_at must be a date and time like "
            "2026-09-15 14:30."
        ) from None

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


def escape_formula(value):
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return f"'{value}"

    return value


def unescape_formula(value):
    """Undo escape_formula() so an export can be re-imported unchanged."""
    if value.startswith("'") and value[1:].startswith(FORMULA_PREFIXES):
        return value[1:]

    return value


def normalize_row(row):
    """Strip header names and values, and undo formula escaping.

    csv.DictReader uses None for missing values in short rows and a
    None key for extra values in long rows. Both become safe here.
    """
    normalized = {}

    for key, value in row.items():
        if key is None:
            continue

        normalized[key.strip()] = unescape_formula((value or "").strip())

    return normalized


def find_matching_post(values):
    """Find a post with the same content, whatever its source.

    Manual posts have no external_id, so exporting and re-importing
    them can only be matched on what the post is: the same fields
    generate_csv_external_id() uses.
    """
    return Post.query.filter(
        func.lower(Post.platform) == values["platform"].lower(),
        func.lower(Post.caption) == values["caption"].lower(),
        func.lower(Post.content_type) == values["content_type"].lower(),
        Post.posted_at == values["posted_at"],
    ).first()


def parse_csv_row(row):
    """Validate one normalized CSV row and return Post field values."""
    platform = row.get("platform", "")
    caption = row.get("caption", "")
    content_type = row.get("content_type", "")

    for name, value in (
        ("platform", platform),
        ("caption", caption),
        ("content_type", content_type),
    ):
        if not value:
            raise ValueError(f"{name} is required.")

    validate_text_length(platform, "platform", 50)
    validate_text_length(caption, "caption", 300)
    validate_text_length(content_type, "content_type", 50)

    posted_at = parse_csv_datetime(row.get("posted_at", ""))

    values = {
        "platform": platform,
        "caption": caption,
        "content_type": content_type,
        "posted_at": posted_at,
    }

    for metric in METRIC_FIELDS:
        values[metric] = parse_metric(row.get(metric), metric)

    external_id = row.get("external_id", "")
    validate_text_length(external_id, "external_id", 150)

    values["external_id"] = external_id or generate_csv_external_id(
        platform,
        caption,
        content_type,
        posted_at,
    )
    values["external_url"] = parse_external_url(
        row.get("external_url")
    )

    return values


def import_posts_from_csv(binary_stream, session):
    """Add valid, non-duplicate rows to the session.

    Raises ValueError for problems with the file as a whole. Problems
    with individual rows are collected in the result instead.
    """
    result = CsvImportResult()
    seen_keys = set()

    try:
        text_stream = io.TextIOWrapper(
            binary_stream,
            encoding="utf-8-sig",
            newline="",
        )

        reader = csv.DictReader(text_stream)

        if not reader.fieldnames:
            raise ValueError(
                "The CSV file does not contain a header row."
            )

        fieldnames = {
            name.strip()
            for name in reader.fieldnames
            if name
        }

        missing_columns = set(REQUIRED_COLUMNS) - fieldnames

        if missing_columns:
            raise ValueError(
                "Missing required columns: "
                + ", ".join(sorted(missing_columns))
            )

        for row_number, row in enumerate(reader, start=2):
            normalized = normalize_row(row)

            try:
                values = parse_csv_row(normalized)
            except ValueError as error:
                result.errors.append(f"Row {row_number}: {error}")
                continue

            key = (values["platform"], values["external_id"])

            is_duplicate = (
                key in seen_keys
                or Post.query.filter_by(
                    platform=values["platform"],
                    external_id=values["external_id"],
                ).first() is not None
                or (
                    not normalized.get("external_id")
                    and find_matching_post(values) is not None
                )
            )

            if is_duplicate:
                result.skipped += 1
                continue

            seen_keys.add(key)

            post = Post(**values, source="csv_import")
            session.add(post)
            record_metric_snapshot(post)
            result.imported += 1

    except UnicodeDecodeError:
        raise ValueError(
            "The CSV file must be saved with UTF-8 encoding."
        ) from None

    except csv.Error as error:
        raise ValueError(
            f"The CSV file could not be read: {error}"
        ) from None

    return result


def write_csv(header, rows):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    writer.writerows(
        [escape_formula(cell) for cell in row]
        for row in rows
    )
    return output.getvalue()


def export_posts_csv(posts):
    return write_csv(
        EXPORT_COLUMNS,
        (
            (
                post.platform,
                post.caption,
                post.content_type,
                post.posted_at.isoformat(
                    sep=" ",
                    timespec="minutes",
                ),
                post.views,
                post.likes,
                post.comments,
                post.shares,
                post.saves,
                post.external_id or "",
                post.external_url or "",
                post.source,
            )
            for post in posts
        ),
    )


def sample_csv_content():
    return write_csv(
        (*REQUIRED_COLUMNS, *OPTIONAL_COLUMNS),
        SAMPLE_ROWS,
    )

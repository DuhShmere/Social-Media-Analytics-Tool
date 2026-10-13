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


def normalize_row(row):
    """Strip header names and values.

    csv.DictReader uses None for missing values in short rows and a
    None key for extra values in long rows. Both become safe here.
    """
    normalized = {}

    for key, value in row.items():
        if key is None:
            continue

        normalized[key.strip()] = (value or "").strip()

    return normalized


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
            try:
                values = parse_csv_row(normalize_row(row))
            except ValueError as error:
                result.errors.append(f"Row {row_number}: {error}")
                continue

            key = (values["platform"], values["external_id"])

            if key in seen_keys or Post.query.filter_by(
                platform=values["platform"],
                external_id=values["external_id"],
            ).first():
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
    writer.writerows(rows)
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

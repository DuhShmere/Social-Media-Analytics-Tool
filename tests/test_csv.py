from io import BytesIO

import pytest

from app import MetricSnapshot, Post
from services.csv_service import normalize_row


HEADER = (
    "platform,caption,content_type,posted_at,views,likes,"
    "comments,shares,saves,external_id,external_url\n"
)

VALID_ROW = "Instagram,Valid row,Reel,2026-09-15T14:30,100,10,1,1,1,ok-1,\n"


def upload_csv(client, content, filename="posts.csv"):
    if isinstance(content, str):
        content = content.encode("utf-8")

    return client.post(
        "/import-csv",
        data={"csv_file": (BytesIO(content), filename)},
        content_type="multipart/form-data",
    )


def test_normalize_row_handles_short_and_long_rows():
    # DictReader gives None for missing values and a None key for extras.
    row = {" platform ": " YouTube ", "caption": None, None: ["extra"]}

    assert normalize_row(row) == {"platform": "YouTube", "caption": ""}


def test_short_row_is_reported_without_losing_valid_rows(client, app):
    response = upload_csv(
        client,
        HEADER + VALID_ROW + "Instagram,Short row\n",
    )

    assert response.status_code == 200
    assert b"Row 3: content_type is required." in response.data

    with app.app_context():
        assert [post.caption for post in Post.query.all()] == ["Valid row"]


def test_header_names_with_spaces_are_accepted(client, app):
    response = upload_csv(
        client,
        HEADER.replace(",", ", ") + VALID_ROW,
    )

    assert response.status_code == 200

    with app.app_context():
        post = Post.query.one()
        assert post.caption == "Valid row"
        assert post.views == 100


@pytest.mark.parametrize(
    ("row", "expected_message"),
    [
        (
            "Instagram,Bad,Reel,2026-09-15T14:30,abc,1,1,1,1,,\n",
            b"Row 2: views must be a whole number of 0 or more.",
        ),
        (
            "Instagram,Bad,Reel,2026-09-15T14:30,10,-3,1,1,1,,\n",
            b"Row 2: likes must be a whole number of 0 or more.",
        ),
        (
            "Instagram,Bad,Reel,yesterday,10,1,1,1,1,,\n",
            b"Row 2: posted_at must be a date and time like",
        ),
        (
            "Instagram,Bad,Reel,,10,1,1,1,1,,\n",
            b"Row 2: posted_at is required.",
        ),
        (
            "Instagram,Bad,Reel,2026-09-15T14:30,10,1,1,1,1,"
            + "x" * 151
            + ",\n",
            b"Row 2: external_id cannot exceed 150 characters.",
        ),
    ],
    ids=[
        "non-numeric-metric",
        "negative-metric",
        "invalid-date",
        "missing-date",
        "external-id-too-long",
    ],
)
def test_invalid_rows_get_clear_messages(client, app, row, expected_message):
    response = upload_csv(client, HEADER + row)

    assert response.status_code == 200
    assert expected_message in response.data

    with app.app_context():
        assert Post.query.count() == 0


def test_missing_columns_rejects_whole_file(client, app):
    header = HEADER.replace("views,", "").replace("likes,", "")

    response = upload_csv(client, header + "Instagram,Row,Reel,2026-09-15T14:30,1,1,1,,\n")

    assert response.status_code == 200
    assert b"Missing required columns: likes, views" in response.data

    with app.app_context():
        assert Post.query.count() == 0


def test_non_utf8_file_gets_clear_message(client, app):
    content = (HEADER + VALID_ROW.replace("Valid", "Café")).encode("latin-1")

    response = upload_csv(client, content)

    assert b"The CSV file must be saved with UTF-8 encoding." in response.data

    with app.app_context():
        assert Post.query.count() == 0


@pytest.mark.parametrize(
    ("filename", "content", "expected_message"),
    [
        ("posts.txt", HEADER + VALID_ROW, b"The uploaded file must be a CSV file."),
        ("", HEADER + VALID_ROW, b"Select a CSV file to upload."),
        ("posts.csv", "", b"The CSV file does not contain a header row."),
    ],
    ids=["wrong-extension", "no-file", "empty-file"],
)
def test_rejected_uploads(client, filename, content, expected_message):
    response = upload_csv(client, content, filename=filename)

    assert response.status_code == 200
    assert expected_message in response.data


def test_duplicate_rows_without_external_id_import_once(client, app):
    row = "TikTok,Same post,Video,2026-09-16 10:00,20,2,2,2,2,,\n"

    response = upload_csv(client, HEADER + row + row)

    assert b"Imported 1 posts" in response.data

    with app.app_context():
        post = Post.query.one()
        assert post.external_id.startswith("csv-")
        assert MetricSnapshot.query.count() == 1


def test_reimporting_an_export_creates_no_duplicates(client, app):
    upload_csv(
        client,
        HEADER
        + VALID_ROW
        + "TikTok,Second,Video,2026-09-16 10:00,20,2,2,2,2,,"
        "https://www.tiktok.com/@example/video/1\n",
    )

    exported = client.get("/export-csv").data
    response = upload_csv(client, exported)

    assert b"Imported 0 posts" in response.data

    with app.app_context():
        assert Post.query.count() == 2
        assert MetricSnapshot.query.count() == 2


def test_timezone_offsets_are_stored_as_utc(client, app):
    row = "TikTok,Offset,Video,2026-09-16T10:00:00-07:00,1,1,1,1,1,,\n"

    upload_csv(client, HEADER + row)

    with app.app_context():
        assert Post.query.one().posted_at.isoformat() == "2026-09-16T17:00:00"

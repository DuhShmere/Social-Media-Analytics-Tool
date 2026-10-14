import re
from datetime import datetime
from io import BytesIO

from extensions import db
from models import Post, SocialAccount, record_metric_snapshot


def add_post(**overrides):
    fields = {
        "platform": "YouTube",
        "caption": "Page test",
        "content_type": "Video",
        "posted_at": datetime(2026, 9, 15, 14, 30),
        "views": 1000,
        "likes": 100,
        "comments": 10,
        "shares": 5,
        "saves": 5,
        "source": "manual",
    }
    fields.update(overrides)

    post = Post(**fields)
    db.session.add(post)
    record_metric_snapshot(post)
    db.session.commit()
    return post


def selected_option(html, select_id):
    select = re.search(
        rf'<select id="{select_id}".*?</select>',
        html,
        re.S,
    ).group(0)

    return re.findall(r'<option\s+value="([^"]*)"\s+selected', select)


def input_value(html, input_id):
    tag = re.search(rf'<input\s+id="{input_id}"[^>]*>', html, re.S).group(0)
    return re.search(r'value="([^"]*)"', tag).group(1)


# ---------------------------------------------------------
# Add and edit forms
# ---------------------------------------------------------

def test_edit_form_keeps_imported_platform_and_content_type(client, app):
    # CSV imports can hold values outside the form's suggested choices.
    # The form must still select them, or saving would silently change them.
    with app.app_context():
        post_id = add_post(
            platform="Facebook",
            content_type="Reel",
            source="csv_import",
        ).id

    html = client.get(f"/posts/{post_id}/edit").get_data(as_text=True)

    assert selected_option(html, "platform") == ["Facebook"]
    assert selected_option(html, "content_type") == ["Reel"]
    assert input_value(html, "posted_at") == "2026-09-15T14:30"
    assert input_value(html, "views") == "1000"


def test_saving_unchanged_edit_form_keeps_values(client, app):
    with app.app_context():
        post_id = add_post(platform="Facebook", content_type="Reel").id

    html = client.get(f"/posts/{post_id}/edit").get_data(as_text=True)

    form = {
        "platform": selected_option(html, "platform")[0],
        "content_type": selected_option(html, "content_type")[0],
        "caption": input_value(html, "caption"),
        "posted_at": input_value(html, "posted_at"),
        **{
            field: input_value(html, field)
            for field in ("views", "likes", "comments", "shares", "saves")
        },
    }

    response = client.post(f"/posts/{post_id}/edit", data=form)

    assert response.status_code == 302

    with app.app_context():
        post = db.session.get(Post, post_id)
        assert post.platform == "Facebook"
        assert post.content_type == "Reel"


def test_add_form_keeps_input_after_error(client):
    response = client.post(
        "/add-post",
        data={
            "platform": "TikTok",
            "caption": "Keep my caption",
            "content_type": "Short",
            "posted_at": "2026-09-20T09:15",
            "views": "2500",
            "likes": "-1",
            "comments": "3",
            "shares": "2",
            "saves": "1",
        },
    )
    html = response.get_data(as_text=True)

    assert "Likes must be a whole number of 0 or more." in html
    assert selected_option(html, "platform") == ["TikTok"]
    assert selected_option(html, "content_type") == ["Short"]
    assert input_value(html, "caption") == "Keep my caption"
    assert input_value(html, "posted_at") == "2026-09-20T09:15"
    assert input_value(html, "views") == "2500"
    assert input_value(html, "likes") == "-1"


def test_edit_form_shows_submitted_values_after_error(client, app):
    with app.app_context():
        post_id = add_post(caption="Saved caption").id

    response = client.post(
        f"/posts/{post_id}/edit",
        data={
            "platform": "YouTube",
            "caption": "Unsaved new caption",
            "content_type": "Video",
            "posted_at": "not-a-date",
            "views": "5",
        },
    )
    html = response.get_data(as_text=True)

    assert "Enter a valid date and time" in html
    assert input_value(html, "caption") == "Unsaved new caption"


def test_add_form_starts_blank_with_zero_metrics(client):
    html = client.get("/add-post").get_data(as_text=True)

    assert selected_option(html, "platform") == [""]
    assert input_value(html, "caption") == ""
    assert input_value(html, "views") == "0"


# ---------------------------------------------------------
# Uploads, dashboard, accounts, charts
# ---------------------------------------------------------

def test_oversized_upload_shows_friendly_message(client, app):
    too_large = BytesIO(b"x" * (app.config["MAX_CONTENT_LENGTH"] + 1))

    response = client.post(
        "/import-csv",
        data={"csv_file": (too_large, "big.csv")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 413
    assert b"The maximum upload size is 2 MB." in response.data
    assert b'<form class="data-form"' in response.data


def test_posts_without_account_are_not_labeled_manual(client, app):
    with app.app_context():
        add_post(caption="Imported row", source="csv_import")

    html = client.get("/").get_data(as_text=True)
    row = re.search(r'<tr\s+class="post-row".*?</tr>', html, re.S).group(0)
    cells = re.findall(r"<td>(.*?)</td>", row, re.S)

    # Third column is the account; fourth is the source badge.
    assert "Not linked" in cells[2]
    assert "CSV" in cells[3]


def test_accounts_page_lists_account_details(client, app):
    with app.app_context():
        account = SocialAccount(
            platform="YouTube",
            account_name="Example Channel",
            account_handle="@example",
            external_account_id="UC-example",
            last_synced_at=datetime(2026, 9, 23, 17, 53),
        )
        db.session.add(account)
        add_post(source="youtube_api", social_account=account, external_id="v1")

    html = client.get("/accounts").get_data(as_text=True)

    assert '<section class="account-grid">' in html
    assert "Example Channel" in html
    assert "@example" in html
    assert re.search(r"<dd>\s*1\s*</dd>", html)
    assert "Sep 23, 2026 at 05:53 PM" in html


def test_chart_pages_load_pinned_chartjs_with_integrity(client, app):
    with app.app_context():
        post_id = add_post().id

    for path in ("/", f"/posts/{post_id}"):
        html = client.get(path).get_data(as_text=True)

        assert "chart.js@4.5.1/dist/chart.umd.min.js" in html
        assert 'integrity="sha384-' in html
        assert 'crossorigin="anonymous"' in html
        assert 'src="https://cdn.jsdelivr.net/npm/chart.js"' not in html

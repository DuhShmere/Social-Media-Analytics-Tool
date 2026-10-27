"""Automated accessibility checks.

These cover what can be checked from HTML and CSS: contrast of the
design tokens, form labels, landmarks, and accessible names. Visual
focus and layout are checked manually with screenshots.
"""

import re
from datetime import datetime
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path

import pytest

from extensions import db
from models import Post, SocialAccount, record_metric_snapshot


CSS_PATH = Path(__file__).resolve().parent.parent / "static" / "css" / "style.css"


# ---------------------------------------------------------
# Color contrast (WCAG 2.2 AA)
# ---------------------------------------------------------

def css_tokens():
    root = re.search(r":root\s*{(.*?)}", CSS_PATH.read_text(), re.S).group(1)
    tokens = dict(re.findall(r"(--[\w-]+):\s*([^;]+);", root))

    def resolve(value):
        reference = re.fullmatch(r"var\((--[\w-]+)\)", value.strip())
        return resolve(tokens[reference.group(1)]) if reference else value.strip()

    return {name: resolve(value) for name, value in tokens.items()}


def relative_luminance(hex_color):
    hex_color = hex_color.lstrip("#")
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(first, second):
    lighter, darker = sorted(
        (relative_luminance(first), relative_luminance(second)),
        reverse=True,
    )
    return (lighter + 0.05) / (darker + 0.05)


TEXT = 4.5  # Normal-size text.
UI = 3.0    # Borders, focus rings, and other user-interface parts.


@pytest.mark.parametrize(
    ("foreground", "background", "minimum"),
    [
        ("--text", "--surface", TEXT),
        ("--muted", "--surface", TEXT),
        ("--muted", "--background", TEXT),
        ("--muted-strong", "#eef2ff", TEXT),  # Recommendation card.
        ("--primary", "--surface", TEXT),     # Links.
        ("#ffffff", "--primary", TEXT),       # Primary button text.
        ("--error-text", "--error-background", TEXT),
        ("--success-text", "--success-background", TEXT),
        ("--input-border", "--surface", UI),
        ("--focus-ring", "--surface", UI),
        ("--focus-ring", "--background", UI),
    ],
)
def test_design_tokens_meet_contrast_minimums(foreground, background, minimum):
    tokens = css_tokens()
    first = tokens.get(foreground, foreground)
    second = tokens.get(background, background)

    assert contrast_ratio(first, second) >= minimum


# ---------------------------------------------------------
# Page structure
# ---------------------------------------------------------

class FormAudit(HTMLParser):
    """Collects form controls, labels, and landmarks from a page."""

    def __init__(self):
        super().__init__()
        self.controls = []
        self.label_targets = set()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if "id" in attrs:
            self.ids.add(attrs["id"])

        if tag == "label" and attrs.get("for"):
            self.label_targets.add(attrs["for"])

        if tag in {"input", "select", "textarea"} and attrs.get("type") not in {
            "hidden",
            "submit",
            "button",
        }:
            self.controls.append(attrs)


def audit(html):
    parser = FormAudit()
    parser.feed(html)
    return parser


def add_post(**overrides):
    fields = {
        "platform": "YouTube",
        "caption": "Accessible caption",
        "content_type": "Video",
        "posted_at": datetime(2026, 9, 15, 14, 30),
        "views": 1000,
        "likes": 100,
        "comments": 10,
        "shares": 5,
        "saves": 5,
        "source": "manual",
        "external_url": "https://www.youtube.com/watch?v=example",
    }
    fields.update(overrides)
    post = Post(**fields)
    db.session.add(post)
    record_metric_snapshot(post)
    db.session.commit()
    return post


@pytest.fixture()
def pages(app):
    with app.app_context():
        post_id = add_post().id

    return [
        "/",
        "/accounts",
        "/add-post",
        f"/posts/{post_id}/edit",
        f"/posts/{post_id}",
        "/import-csv",
        "/import-youtube",
        "/terms",
        "/privacy",
        "/does-not-exist",
    ]


def test_every_form_control_has_a_label(client, pages):
    for path in pages:
        page = audit(client.get(path).get_data(as_text=True))

        for control in page.controls:
            has_label = (
                control.get("id") in page.label_targets
                or control.get("aria-label")
            )
            assert has_label, f"{path}: unlabeled control {control}"


def test_every_page_has_skip_link_and_main_landmark(client, pages):
    for path in pages:
        html = client.get(path).get_data(as_text=True)

        assert '<a class="skip-link" href="#main-content">' in html, path
        assert '<main id="main-content"' in html, path
        assert '<nav class="navigation" aria-label="Main">' in html, path


@pytest.mark.parametrize(
    ("path", "current_label"),
    [
        ("/", "Dashboard"),
        ("/accounts", "Accounts"),
        ("/add-post", "Add Post"),
        ("/import-youtube", "Import YouTube"),
        ("/import-csv", "Import CSV"),
    ],
)
def test_navigation_marks_only_the_current_page(client, path, current_label):
    html = client.get(path).get_data(as_text=True)
    current = re.findall(r'aria-current="page"\s*>\s*([^<]+?)\s*<', html)

    assert current == [current_label]


def test_pages_outside_navigation_mark_nothing_current(client, app):
    with app.app_context():
        post_id = add_post().id

    html = client.get(f"/posts/{post_id}").get_data(as_text=True)

    assert 'aria-current="page"' not in html


# ---------------------------------------------------------
# Announced messages and accessible names
# ---------------------------------------------------------

def test_form_errors_are_announced(client):
    html = client.post("/add-post", data={"platform": ""}).get_data(as_text=True)

    assert '<div class="message error-message" role="alert">' in html


def test_import_results_are_announced(client):
    content = (
        "platform,caption,content_type,posted_at,views,likes,"
        "comments,shares,saves\n"
        "Instagram,Row,Reel,2026-09-15T14:30,1,1,1,1,1\n"
    ).encode("utf-8")

    html = client.post(
        "/import-csv",
        data={"csv_file": (BytesIO(content), "posts.csv")},
        content_type="multipart/form-data",
    ).get_data(as_text=True)

    assert '<div class="message success-message" role="status">' in html


def test_row_actions_name_their_post(client, app):
    with app.app_context():
        add_post(caption="Launch recap")
        add_post(
            caption="Synced video",
            source="youtube_api",
            external_id="vid-1",
        )

    html = client.get("/").get_data(as_text=True)

    for action in ("Analytics", "Edit", "Delete"):
        assert (
            f'{action}<span class="visually-hidden">: Launch recap</span>'
            in html
        )

    assert 'View<span class="visually-hidden">: Synced video</span>' in html


def test_links_that_open_new_tabs_say_so(client, app):
    with app.app_context():
        post_id = add_post().id

    for path in ("/", f"/posts/{post_id}"):
        html = client.get(path).get_data(as_text=True)
        new_tab_links = re.findall(
            r'<a[^>]*target="_blank"[^>]*>.*?</a>',
            html,
            re.S,
        )

        assert new_tab_links, path

        for link in new_tab_links:
            assert "(opens in a new tab)" in link
            assert 'rel="noopener noreferrer"' in link


def test_sync_buttons_name_their_account(client, app):
    with app.app_context():
        db.session.add(
            SocialAccount(
                platform="YouTube",
                account_name="Example Channel",
                account_handle="@example",
                external_account_id="UC-example",
            )
        )
        db.session.commit()

    html = client.get("/accounts").get_data(as_text=True)

    assert (
        'Sync Now<span class="visually-hidden">: Example Channel</span>'
        in html
    )

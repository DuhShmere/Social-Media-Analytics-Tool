"""CSRF protection. TestingConfig turns it off, so these tests turn it on."""

import re
from datetime import datetime

import pytest

from config import get_config
from extensions import db
from models import Post, SocialAccount, record_metric_snapshot


@pytest.fixture()
def csrf_on(app, monkeypatch):
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
    return app


def add_post(**overrides):
    fields = {
        "platform": "YouTube",
        "caption": "CSRF test",
        "content_type": "Video",
        "posted_at": datetime(2026, 9, 15, 14, 30),
        "views": 10,
        "likes": 1,
        "comments": 0,
        "shares": 0,
        "saves": 0,
        "source": "manual",
    }
    fields.update(overrides)
    post = Post(**fields)
    db.session.add(post)
    record_metric_snapshot(post)
    db.session.commit()
    return post


def csrf_token(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


VALID_FORM = {
    "platform": "Instagram",
    "caption": "Created with a token",
    "content_type": "Reel",
    "posted_at": "2026-09-15T14:30",
    "views": "100",
}


def test_post_without_token_is_rejected(client, csrf_on):
    response = client.post("/add-post", data=VALID_FORM)

    assert response.status_code == 400
    assert b"This page expired" in response.data

    with csrf_on.app_context():
        assert Post.query.count() == 0


def test_post_with_wrong_token_is_rejected(client, csrf_on):
    client.get("/add-post")

    response = client.post(
        "/add-post",
        data={**VALID_FORM, "csrf_token": "forged-token"},
    )

    assert response.status_code == 400


def test_post_with_token_from_the_form_succeeds(client, csrf_on):
    token = csrf_token(client.get("/add-post").get_data(as_text=True))

    response = client.post("/add-post", data={**VALID_FORM, "csrf_token": token})

    assert response.status_code == 302

    with csrf_on.app_context():
        assert Post.query.one().caption == "Created with a token"


def test_cross_site_delete_is_blocked(client, csrf_on):
    # A malicious page can make the browser POST here, but it cannot
    # read this site's pages to learn the token.
    with csrf_on.app_context():
        post_id = add_post().id

    response = client.post(f"/posts/{post_id}/delete")

    assert response.status_code == 400

    with csrf_on.app_context():
        assert db.session.get(Post, post_id) is not None


def test_every_post_form_includes_a_token(client, csrf_on):
    with csrf_on.app_context():
        post_id = add_post().id
        db.session.add(
            SocialAccount(
                platform="YouTube",
                account_name="Example Channel",
                account_handle="@example",
                external_account_id="UC-example",
            )
        )
        db.session.commit()

    for path in ("/", "/accounts", "/add-post", f"/posts/{post_id}/edit", "/import-csv", "/import-youtube"):
        html = client.get(path).get_data(as_text=True)
        forms = re.findall(r'<form\b[^>]*method="POST".*?</form>', html, re.S)

        assert forms, path

        for form in forms:
            assert 'name="csrf_token"' in form, path


def test_production_requires_secret_key(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user@db/analytics")
    monkeypatch.delenv("SECRET_KEY", raising=False)

    with pytest.raises(ValueError, match="SECRET_KEY must be set"):
        get_config("production")


def test_production_session_cookie_is_secure_by_default(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user@db/analytics")
    monkeypatch.setenv("SECRET_KEY", "fake-secret")
    monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)

    assert get_config("production").SESSION_COOKIE_SECURE is True

    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")

    assert get_config("production").SESSION_COOKIE_SECURE is False


def test_development_generates_a_key_when_missing(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)

    first = get_config("development").SECRET_KEY
    second = get_config("development").SECRET_KEY

    assert len(first) == 64
    assert first != second


def test_session_cookie_is_same_site_lax(client, csrf_on):
    response = client.get("/add-post")
    cookie = response.headers.get("Set-Cookie", "")

    assert "SameSite=Lax" in cookie
    assert "HttpOnly" in cookie

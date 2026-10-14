import re
from pathlib import Path

import pytest

from config import get_config
from security import SECURITY_HEADERS


TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


@pytest.mark.parametrize(
    "path",
    ["/", "/api/analytics", "/health", "/does-not-exist", "/static/css/style.css"],
)
def test_every_response_has_security_headers(client, path):
    response = client.get(path)

    for name, value in SECURITY_HEADERS.items():
        assert response.headers.get(name) == value, (path, name)


def test_content_security_policy_forbids_inline_code():
    policy = SECURITY_HEADERS["Content-Security-Policy"]

    assert "'unsafe-inline'" not in policy
    assert "'unsafe-eval'" not in policy
    assert "frame-ancestors 'none'" in policy
    assert "object-src 'none'" in policy


def test_templates_have_no_inline_scripts_styles_or_handlers():
    # The Content Security Policy blocks these, so they would break pages.
    for template in TEMPLATES.rglob("*.html"):
        html = template.read_text()
        name = template.relative_to(TEMPLATES)

        for tag in re.findall(r"<script\b[^>]*>", html):
            assert "src=" in tag, f"{name}: inline <script>"

        assert "<style" not in html, f"{name}: <style> block"
        assert not re.search(r"\sstyle=", html), f"{name}: style attribute"
        assert not re.search(r"\son[a-z]+=", html), f"{name}: inline handler"


def test_hsts_is_sent_only_over_https(client):
    insecure = client.get("/")
    secure = client.get("/", base_url="https://localhost")

    assert "Strict-Transport-Security" not in insecure.headers
    assert secure.headers["Strict-Transport-Security"] == (
        "max-age=31536000; includeSubDomains"
    )


def test_production_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="DATABASE_URL must be set"):
        get_config("production")


def test_production_starts_with_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user@db/analytics")

    assert get_config("production").SQLALCHEMY_DATABASE_URI == (
        "postgresql://user@db/analytics"
    )

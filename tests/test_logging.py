import json
import logging
import re
import sys

import pytest
from flask import g

from logging_config import JsonFormatter, RequestIdFilter


def request_records(caplog):
    return [r for r in caplog.records if hasattr(r, "duration_ms")]


def test_valid_incoming_request_id_is_reused(client):
    response = client.get("/accounts", headers={"X-Request-ID": "trace-abc.123"})

    assert response.headers["X-Request-ID"] == "trace-abc.123"


@pytest.mark.parametrize(
    "incoming",
    [None, "has spaces", "x" * 65, "<script>alert(1)</script>"],
    ids=["missing", "spaces", "too-long", "html-injection"],
)
def test_invalid_or_missing_request_id_is_replaced(client, incoming):
    headers = {"X-Request-ID": incoming} if incoming else {}

    response = client.get("/accounts", headers=headers)

    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])


def test_each_request_is_logged_with_timing(client, caplog):
    caplog.set_level(logging.INFO, logger="social_analytics")

    response = client.get("/accounts")

    [record] = request_records(caplog)
    assert record.method == "GET"
    assert record.path == "/accounts"
    assert record.status == 200
    assert record.duration_ms >= 0
    assert record.levelno == logging.INFO

    # The request ID filter reads it from the same request.
    assert response.headers["X-Request-ID"]


def test_health_checks_log_at_debug_and_static_files_not_at_all(client, caplog):
    caplog.set_level(logging.DEBUG, logger="social_analytics")

    client.get("/health")
    client.get("/static/css/style.css")

    records = request_records(caplog)
    assert [(r.path, r.levelno) for r in records] == [("/health", logging.DEBUG)]


def test_requests_rejected_by_csrf_still_get_id_and_timing(app, client, caplog, monkeypatch):
    # Regression: the CSRF check used to run before the request ID was set.
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
    caplog.set_level(logging.INFO, logger="social_analytics")

    response = client.post("/add-post", data={})

    assert response.status_code == 400
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])

    [record] = request_records(caplog)
    assert record.status == 400
    assert record.duration_ms is not None


def test_json_formatter_writes_one_parseable_object(app):
    record = logging.LogRecord(
        "social_analytics", logging.INFO, __file__, 1,
        "GET %s %s", ("/accounts", 200), None,
    )
    record.method, record.path, record.status, record.duration_ms = "GET", "/accounts", 200, 4.2

    with app.test_request_context("/accounts"):
        g.request_id = "trace-1"
        RequestIdFilter().filter(record)

    entry = json.loads(JsonFormatter().format(record))

    assert entry["message"] == "GET /accounts 200"
    assert entry["request_id"] == "trace-1"
    assert entry["level"] == "INFO"
    assert entry["status"] == 200
    assert entry["duration_ms"] == 4.2
    assert entry["timestamp"].endswith("+00:00")


def test_json_formatter_includes_exceptions():
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = logging.LogRecord(
            "social_analytics", logging.ERROR, __file__, 1,
            "failed", (), sys.exc_info(),
        )

    RequestIdFilter().filter(record)
    entry = json.loads(JsonFormatter().format(record))

    assert entry["request_id"] == "-"
    assert "RuntimeError: boom" in entry["exception"]

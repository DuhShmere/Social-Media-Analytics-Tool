"""Logging: JSON lines in production, readable text in development.

Every request gets an ID, taken from a valid incoming X-Request-ID
header or generated, which is added to each log line written while
handling it and returned in the X-Request-ID response header. Each
request is logged once with its method, path, status, and duration.
"""

import json
import logging
import re
import sys
import time
import uuid
from datetime import datetime, timezone

from flask import g, has_request_context, request


REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Fields that request_log() passes with `extra=` and JSON output includes.
REQUEST_FIELDS = ("method", "path", "status", "duration_ms")


class RequestIdFilter(logging.Filter):
    """Adds the current request's ID, or "-" outside a request."""

    def filter(self, record):
        record.request_id = (
            g.get("request_id", "-") if has_request_context() else "-"
        )
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "timestamp": datetime.fromtimestamp(
                record.created, timezone.utc
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }

        for field in REQUEST_FIELDS:
            if hasattr(record, field):
                entry[field] = getattr(record, field)

        if record.exc_info:
            # exc_text may already be set, and redacted, by a filter.
            entry["exception"] = record.exc_text or self.formatException(
                record.exc_info
            )

        return json.dumps(entry)


TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"


def configure_logging(config_name):
    """Send all log records to stdout in the environment's format.

    Must run before app.logger is first used, so Flask sees a handler
    and does not add its own. Tests keep pytest's logging setup.
    """
    if config_name == "testing":
        return

    root = logging.getLogger()

    # create_app() can run more than once in a process; add one handler.
    if any(getattr(h, "_social_analytics", False) for h in root.handlers):
        return

    handler = logging.StreamHandler(sys.stdout)
    handler._social_analytics = True
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(
        JsonFormatter()
        if config_name == "production"
        else logging.Formatter(TEXT_FORMAT)
    )

    root.addHandler(handler)
    root.setLevel(logging.INFO)


def register_request_logging(app):
    @app.before_request
    def start_request():
        incoming = request.headers.get("X-Request-ID", "")
        g.request_id = (
            incoming
            if REQUEST_ID_PATTERN.match(incoming)
            else uuid.uuid4().hex
        )
        g.request_started = time.perf_counter()

    @app.after_request
    def log_request(response):
        response.headers["X-Request-ID"] = g.get("request_id", "-")

        if request.path.startswith("/static/"):
            return response

        started = g.get("request_started")
        duration_ms = (
            round((time.perf_counter() - started) * 1000, 1)
            if started is not None
            else None
        )

        # Health checks run every few seconds; keep them out of INFO logs.
        level = logging.DEBUG if request.path == "/health" else logging.INFO

        app.logger.log(
            level,
            "%s %s %s %sms",
            request.method,
            request.path,
            response.status_code,
            duration_ms,
            extra={
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )

        return response

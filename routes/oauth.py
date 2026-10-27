"""Helpers shared by the OAuth routes (Instagram and TikTok)."""

import secrets
from datetime import datetime, timezone

from flask import current_app, redirect, session, url_for


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def missing_settings(names):
    return [name for name in names if not current_app.config.get(name)]


def accounts_page():
    return redirect(url_for("main.accounts"))


def start_state(session_key):
    """Remember a random value the platform must send back.

    It proves the callback belongs to a login this browser started (the
    OAuth version of CSRF protection).
    """
    state = secrets.token_urlsafe(32)
    session[session_key] = state
    return state


def state_matches(session_key, received_state):
    """Check the returned state once; a state can never be reused."""
    expected = session.pop(session_key, None)
    return bool(expected) and secrets.compare_digest(expected, received_state or "")

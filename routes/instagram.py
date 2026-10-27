"""Connect, sync, and disconnect Instagram accounts (OAuth)."""

import secrets
from datetime import datetime, timezone

import requests
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    request,
    session,
    url_for,
)

from extensions import db
from models import SocialAccount
from services.instagram import (
    PLATFORM,
    InstagramClient,
    InstagramError,
    build_authorization_url,
    exchange_code_for_token,
    exchange_for_long_lived_token,
    store_token,
    sync_instagram_account,
    usable_token,
)
from token_vault import TokenVaultError


bp = Blueprint("instagram", __name__, url_prefix="/instagram")

STATE_SESSION_KEY = "instagram_oauth_state"
SETTINGS = (
    "INSTAGRAM_APP_ID",
    "INSTAGRAM_APP_SECRET",
    "INSTAGRAM_REDIRECT_URI",
    "TOKEN_ENCRYPTION_KEY",
)


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def missing_settings():
    return [name for name in SETTINGS if not current_app.config.get(name)]


def accounts_page():
    return redirect(url_for("main.accounts"))


def sync_message(result, connected=False):
    prefix = f"Connected @{result.account_name}. " if connected else ""
    message = (
        f"{prefix}Imported {result.imported} new posts and updated "
        f"{result.updated} from @{result.account_name}."
    )

    if result.missing_insights:
        message += (
            f" Views, shares, and saves were unavailable for "
            f"{result.missing_insights} posts."
        )

    return message


@bp.get("/connect")
def connect():
    missing = missing_settings()

    if missing:
        flash(
            "Instagram is not set up yet. Add these settings to your .env "
            f"file: {', '.join(missing)}.",
            "error",
        )
        return accounts_page()

    # A random value Instagram sends back, proving the callback belongs
    # to a login this browser started (the OAuth version of CSRF).
    state = secrets.token_urlsafe(32)
    session[STATE_SESSION_KEY] = state

    return redirect(
        build_authorization_url(
            current_app.config["INSTAGRAM_APP_ID"],
            current_app.config["INSTAGRAM_REDIRECT_URI"],
            state,
        )
    )


# Meta's dashboard may save the redirect URI with a trailing slash, so
# accept both forms; INSTAGRAM_REDIRECT_URI must match whichever is saved.
@bp.get("/callback", strict_slashes=False)
def callback():
    expected_state = session.pop(STATE_SESSION_KEY, None)

    if request.args.get("error"):
        flash("Instagram connection was cancelled.", "error")
        return accounts_page()

    received_state = request.args.get("state", "")

    if not expected_state or not secrets.compare_digest(
        expected_state, received_state
    ):
        current_app.logger.warning("Instagram callback with an invalid state.")
        flash(
            "This Instagram login could not be verified. Try connecting "
            "again.",
            "error",
        )
        return accounts_page()

    code = request.args.get("code")

    if not code:
        flash("Instagram did not return a login code. Try again.", "error")
        return accounts_page()

    config = current_app.config
    http = requests.Session()
    now = utc_now()

    try:
        short_token, _user_id = exchange_code_for_token(
            http,
            config["INSTAGRAM_APP_ID"],
            config["INSTAGRAM_APP_SECRET"],
            config["INSTAGRAM_REDIRECT_URI"],
            code,
        )
        token, expires_at = exchange_for_long_lived_token(
            http,
            config["INSTAGRAM_APP_SECRET"],
            short_token,
            now,
        )
        result = sync_instagram_account(
            InstagramClient(token, session=http),
            db.session,
            now,
        )
        store_token(
            result.account,
            token,
            expires_at,
            config["TOKEN_ENCRYPTION_KEY"],
        )
        db.session.commit()

    except (InstagramError, TokenVaultError) as error:
        db.session.rollback()
        current_app.logger.warning(
            "Instagram connection failed: %s (cause: %r)",
            error,
            error.__cause__,
        )
        flash(str(error), "error")
        return accounts_page()

    current_app.logger.info(
        "Instagram connected for %s: %s imported, %s updated.",
        result.account_name,
        result.imported,
        result.updated,
    )
    flash(sync_message(result, connected=True), "success")
    return accounts_page()


def instagram_account_or_404(account_id):
    account = db.get_or_404(SocialAccount, account_id)

    if account.platform != PLATFORM:
        abort(404)

    return account


@bp.post("/accounts/<int:account_id>/sync")
def sync(account_id):
    account = instagram_account_or_404(account_id)
    http = requests.Session()
    now = utc_now()

    try:
        token = usable_token(
            http,
            account,
            current_app.config.get("TOKEN_ENCRYPTION_KEY"),
            now,
        )
        result = sync_instagram_account(
            InstagramClient(token, session=http),
            db.session,
            now,
            account=account,
        )
        db.session.commit()

    except (InstagramError, TokenVaultError) as error:
        db.session.rollback()
        current_app.logger.warning(
            "Instagram sync failed: %s (cause: %r)",
            error,
            error.__cause__,
        )
        flash(str(error), "error")
        return accounts_page()

    flash(sync_message(result), "success")
    return accounts_page()


@bp.post("/accounts/<int:account_id>/disconnect")
def disconnect(account_id):
    account = instagram_account_or_404(account_id)

    # Forget the token; imported posts and their history are kept.
    account.encrypted_access_token = None
    account.access_token_expires_at = None
    db.session.commit()

    flash(
        f"Disconnected {account.account_handle}. Imported posts were kept. "
        "You can also remove this app in Instagram's settings.",
        "success",
    )
    return accounts_page()

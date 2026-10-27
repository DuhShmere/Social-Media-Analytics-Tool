"""Connect, sync, and disconnect TikTok accounts (OAuth)."""

import requests
from flask import Blueprint, abort, current_app, flash, redirect, request

from extensions import db
from models import SocialAccount
from routes.oauth import (
    accounts_page,
    missing_settings,
    start_state,
    state_matches,
    utc_now,
)
from services.tiktok import (
    PLATFORM,
    TikTokClient,
    TikTokError,
    build_authorization_url,
    clear_tokens,
    exchange_code_for_tokens,
    revoke_token,
    store_tokens,
    sync_tiktok_account,
    usable_token,
)
from token_vault import TokenVaultError, decrypt_token


bp = Blueprint("tiktok", __name__, url_prefix="/tiktok")

STATE_SESSION_KEY = "tiktok_oauth_state"
SETTINGS = (
    "TIKTOK_CLIENT_KEY",
    "TIKTOK_CLIENT_SECRET",
    "TIKTOK_REDIRECT_URI",
    "TOKEN_ENCRYPTION_KEY",
)


def sync_message(result, connected=False):
    prefix = f"Connected {result.account_name}. " if connected else ""
    return (
        f"{prefix}Imported {result.imported} new videos and updated "
        f"{result.updated} from {result.account_name}."
    )


def tiktok_account_or_404(account_id):
    account = db.get_or_404(SocialAccount, account_id)

    if account.platform != PLATFORM:
        abort(404)

    return account


@bp.get("/connect")
def connect():
    missing = missing_settings(SETTINGS)

    if missing:
        flash(
            "TikTok is not set up yet. Add these settings to your .env "
            f"file: {', '.join(missing)}.",
            "error",
        )
        return accounts_page()

    state = start_state(STATE_SESSION_KEY)

    return redirect(
        build_authorization_url(
            current_app.config["TIKTOK_CLIENT_KEY"],
            current_app.config["TIKTOK_REDIRECT_URI"],
            state,
        )
    )


# Accept a trailing slash too, in case one is saved in TikTok's settings.
@bp.get("/callback", strict_slashes=False)
def callback():
    state_ok = state_matches(STATE_SESSION_KEY, request.args.get("state"))

    if request.args.get("error"):
        flash("TikTok connection was cancelled.", "error")
        return accounts_page()

    if not state_ok:
        current_app.logger.warning("TikTok callback with an invalid state.")
        flash(
            "This TikTok login could not be verified. Try connecting again.",
            "error",
        )
        return accounts_page()

    code = request.args.get("code")

    if not code:
        flash("TikTok did not return a login code. Try again.", "error")
        return accounts_page()

    config = current_app.config
    http = requests.Session()
    now = utc_now()

    try:
        tokens = exchange_code_for_tokens(
            http,
            config["TIKTOK_CLIENT_KEY"],
            config["TIKTOK_CLIENT_SECRET"],
            config["TIKTOK_REDIRECT_URI"],
            code,
            now,
        )
        result = sync_tiktok_account(
            TikTokClient(tokens.access_token, session=http),
            db.session,
            now,
        )
        store_tokens(result.account, tokens, config["TOKEN_ENCRYPTION_KEY"])
        db.session.commit()

    except (TikTokError, TokenVaultError) as error:
        db.session.rollback()
        current_app.logger.warning(
            "TikTok connection failed: %s (cause: %r)",
            error,
            error.__cause__,
        )
        flash(str(error), "error")
        return accounts_page()

    current_app.logger.info(
        "TikTok connected for %s: %s imported, %s updated.",
        result.account_name,
        result.imported,
        result.updated,
    )
    flash(sync_message(result, connected=True), "success")
    return accounts_page()


@bp.post("/accounts/<int:account_id>/sync")
def sync(account_id):
    account = tiktok_account_or_404(account_id)
    config = current_app.config
    http = requests.Session()
    now = utc_now()

    try:
        token = usable_token(
            http,
            account,
            config.get("TOKEN_ENCRYPTION_KEY"),
            config.get("TIKTOK_CLIENT_KEY"),
            config.get("TIKTOK_CLIENT_SECRET"),
            now,
        )
        result = sync_tiktok_account(
            TikTokClient(token, session=http),
            db.session,
            now,
            account=account,
        )
        db.session.commit()

    except (TikTokError, TokenVaultError) as error:
        db.session.rollback()
        current_app.logger.warning(
            "TikTok sync failed: %s (cause: %r)",
            error,
            error.__cause__,
        )
        flash(str(error), "error")
        return accounts_page()

    flash(sync_message(result), "success")
    return accounts_page()


@bp.post("/accounts/<int:account_id>/disconnect")
def disconnect(account_id):
    account = tiktok_account_or_404(account_id)
    config = current_app.config
    revoked = False

    # Ask TikTok to revoke access, then forget the tokens either way.
    # Imported videos and their history are kept.
    try:
        if account.encrypted_access_token:
            revoke_token(
                requests.Session(),
                config.get("TIKTOK_CLIENT_KEY"),
                config.get("TIKTOK_CLIENT_SECRET"),
                decrypt_token(
                    account.encrypted_access_token,
                    config.get("TOKEN_ENCRYPTION_KEY"),
                ),
            )
            revoked = True
    except (TikTokError, TokenVaultError) as error:
        current_app.logger.warning("TikTok revoke failed: %s", error)

    clear_tokens(account)
    db.session.commit()

    name = account.account_handle or account.account_name
    message = f"Disconnected {name}. Imported videos were kept."

    if not revoked:
        message += (
            " TikTok could not be reached to revoke access, so remove this "
            "app in TikTok's settings to finish."
        )

    flash(message, "success")
    return accounts_page()

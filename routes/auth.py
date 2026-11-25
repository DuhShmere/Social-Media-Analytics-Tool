"""User accounts: sign up, sign in, sign out, and change password.

Every page needs a signed-in user except the few that must stay
public: the sign-in and sign-up pages, the health check (containers
and uptime monitors), the legal pages (Meta and TikTok link to them),
TikTok's site verification file, and static files.

Each user sees only their own social accounts and posts. Routes load
rows with owned_or_404(), which answers 404, not 403, for another
user's row, so IDs cannot be probed to learn what exists.
"""

import hashlib
import hmac
import math
import re
from datetime import timedelta
from urllib.parse import urlparse

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import FailedLogin, User, utc_now


bp = Blueprint("auth", __name__)

USER_ID_KEY = "user_id"
FINGERPRINT_KEY = "password_fingerprint"

USERNAME_PATTERN = re.compile(r"[a-z0-9_.-]{3,30}")
MIN_PASSWORD_LENGTH = 12
# Hashing is deliberately slow, so very long inputs are refused.
MAX_PASSWORD_LENGTH = 128

PUBLIC_ENDPOINTS = {
    "auth.login",
    "auth.signup",
    "health.health",
    "main.terms",
    "main.privacy",
    "tiktok_verification.site_verification",
    "static",
}

# Checked when a username does not exist, so a wrong username takes as
# long as a wrong password and response times do not reveal which
# usernames exist.
_DUMMY_HASH = None


# ---------------------------------------------------------
# Validation
# ---------------------------------------------------------

def normalize_username(username):
    return (username or "").strip().lower()


def validate_username(username):
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError(
            "Usernames are 3 to 30 characters: letters, numbers, dots, "
            "dashes, and underscores."
        )


def validate_password(password):
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Passwords must be at least {MIN_PASSWORD_LENGTH} characters."
        )

    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(
            f"Passwords can be at most {MAX_PASSWORD_LENGTH} characters."
        )


def create_user(username, password):
    """Add a validated user to the session. Raises ValueError."""
    username = normalize_username(username)
    validate_username(username)
    validate_password(password)

    if User.query.filter_by(username=username).first():
        raise ValueError("That username is taken.")

    user = User(username=username)
    user.set_password(password)
    db.session.add(user)
    return user


# ---------------------------------------------------------
# Sessions
# ---------------------------------------------------------

def _password_fingerprint(user):
    # Derived from the stored hash, so changing a password signs out
    # that user's other browsers. The session cookie is signed but
    # readable, so it holds this keyed hash, never the password hash.
    return hmac.new(
        current_app.config["SECRET_KEY"].encode("utf-8"),
        user.password_hash.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def start_session(user):
    # Start a new session so nothing from before sign-in carries over.
    session.clear()
    session[USER_ID_KEY] = user.id
    session[FINGERPRINT_KEY] = _password_fingerprint(user)


def load_signed_in_user():
    user_id = session.get(USER_ID_KEY)

    if user_id is None:
        return None

    user = db.session.get(User, user_id)

    if user is None or not hmac.compare_digest(
        session.get(FINGERPRINT_KEY, ""),
        _password_fingerprint(user),
    ):
        session.clear()
        return None

    return user


def current_user():
    return g.get("user")


def owned_or_404(model, row_id):
    """Load a row that belongs to the signed-in user, or answer 404."""
    row = db.session.get(model, row_id)

    if row is None or row.user_id != current_user().id:
        abort(404)

    return row


def safe_next_page(target):
    """Only return to a page on this site, never to another website."""
    default = url_for("main.dashboard")

    if not target or not target.startswith("/") or target.startswith("//"):
        return default

    # Browsers treat a backslash like a slash, so "/\evil.com" is another site.
    if "\\" in target:
        return default

    parsed = urlparse(target)

    if parsed.scheme or parsed.netloc:
        return default

    return target


def _requested_page():
    if request.query_string:
        return f"{request.path}?{request.query_string.decode('utf-8', 'replace')}"

    return request.path


def require_sign_in():
    g.user = load_signed_in_user()

    if g.user is not None or request.endpoint in PUBLIC_ENDPOINTS:
        return None

    if request.path.startswith("/api/"):
        return jsonify({"error": "Sign in required."}), 401

    # After signing in, return to the page that was asked for. A form
    # submission cannot be replayed, so it returns to the dashboard.
    next_page = _requested_page() if request.method == "GET" else None
    return redirect(url_for("auth.login", next=next_page))


# ---------------------------------------------------------
# Sign-in lockout
# ---------------------------------------------------------

def _now():
    # A function, so tests can move the clock forward.
    return utc_now()


def _lockout_key(username):
    # The column holds 64 characters; real usernames are at most 30.
    return normalize_username(username)[:64]


def lockout_minutes_left(username):
    """Minutes until this username may try again, or 0 if not locked.

    LOGIN_MAX_ATTEMPTS failures within LOGIN_LOCKOUT_MINUTES lock the
    username until LOGIN_LOCKOUT_MINUTES after the last of them.
    Attempts made while locked are refused before the password is
    checked and are not recorded, so they do not extend the lock.
    """
    config = current_app.config
    window = timedelta(minutes=config["LOGIN_LOCKOUT_MINUTES"])
    now = _now()

    latest = (
        FailedLogin.query.filter_by(username=_lockout_key(username))
        .order_by(FailedLogin.attempted_at.desc())
        .limit(config["LOGIN_MAX_ATTEMPTS"])
        .all()
    )

    if len(latest) < config["LOGIN_MAX_ATTEMPTS"]:
        return 0

    newest, oldest = latest[0].attempted_at, latest[-1].attempted_at

    if newest - oldest >= window:
        return 0  # Spread out over more than the window: not a burst.

    unlocks_at = newest + window

    if now >= unlocks_at:
        return 0

    return max(1, math.ceil((unlocks_at - now).total_seconds() / 60))


def record_failed_login(username):
    now = _now()
    window = timedelta(minutes=current_app.config["LOGIN_LOCKOUT_MINUTES"])

    # Forget attempts that can no longer matter: a lock involves
    # failures at most one window apart and lasts one more window.
    FailedLogin.query.filter(FailedLogin.attempted_at <= now - 2 * window).delete()
    db.session.add(FailedLogin(username=_lockout_key(username), attempted_at=now))
    db.session.commit()


def clear_failed_logins(username):
    FailedLogin.query.filter_by(username=_lockout_key(username)).delete()
    db.session.commit()


def _check_credentials(username, password):
    global _DUMMY_HASH

    user = User.query.filter_by(username=normalize_username(username)).first()

    if user is None:
        if _DUMMY_HASH is None:
            probe = User()
            probe.set_password("not-a-real-password")
            _DUMMY_HASH = probe.password_hash

        User(password_hash=_DUMMY_HASH).check_password(password)
        return None

    if len(password) > MAX_PASSWORD_LENGTH or not user.check_password(password):
        return None

    return user


# ---------------------------------------------------------
# Pages
# ---------------------------------------------------------

@bp.route("/login", methods=["GET", "POST"])
def login():
    next_page = safe_next_page(request.values.get("next"))

    if current_user():
        return redirect(next_page)

    if request.method == "GET":
        return render_template(
            "login.html",
            error=None,
            username="",
            next_page=next_page,
        )

    username = request.form.get("username", "")
    minutes_left = lockout_minutes_left(username)

    if minutes_left:
        # Refused before the password is checked, so guessing while
        # locked is pointless and costs no hashing work.
        current_app.logger.warning(
            "Sign-in refused for a locked username from %s.",
            request.remote_addr,
        )
        unit = "minute" if minutes_left == 1 else "minutes"
        return (
            render_template(
                "login.html",
                error=(
                    "Too many failed sign-in attempts. Try again in "
                    f"{minutes_left} {unit}."
                ),
                username=username,
                next_page=next_page,
            ),
            429,
        )

    user = _check_credentials(username, request.form.get("password", ""))

    if user is None:
        record_failed_login(username)
        current_app.logger.warning(
            "Failed sign-in attempt from %s.",
            request.remote_addr,
        )
        return (
            render_template(
                "login.html",
                # One message for both cases, so it does not reveal
                # whether the username exists.
                error="That username and password do not match.",
                username=username,
                next_page=next_page,
            ),
            401,
        )

    clear_failed_logins(username)
    start_session(user)
    current_app.logger.info("User %s signed in.", user.id)
    return redirect(next_page)


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user():
        return redirect(url_for("main.dashboard"))

    if not current_app.config["SIGNUP_ENABLED"]:
        return render_template("signup.html", closed=True), 403

    if request.method == "GET":
        return render_template("signup.html", error=None, username="")

    username = request.form.get("username", "")
    password = request.form.get("password", "")

    try:
        if password != request.form.get("confirm_password", ""):
            raise ValueError("The passwords do not match.")

        user = create_user(username, password)
        db.session.commit()

    except IntegrityError:
        # Someone else took the username at the same moment.
        db.session.rollback()
        error = "That username is taken."

    except ValueError as validation_error:
        db.session.rollback()
        error = str(validation_error)

    else:
        start_session(user)
        current_app.logger.info("User %s signed up.", user.id)
        flash(f"Welcome, {user.username}! Your account is ready.", "success")
        return redirect(url_for("main.dashboard"))

    return (
        render_template("signup.html", error=error, username=username),
        400,
    )


@bp.post("/logout")
def logout():
    session.clear()
    flash("You signed out.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/account/password", methods=["GET", "POST"])
def change_password():
    if request.method == "GET":
        return render_template("change_password.html", error=None)

    user = current_user()
    new_password = request.form.get("new_password", "")

    try:
        current_password = request.form.get("current_password", "")

        if len(current_password) > MAX_PASSWORD_LENGTH or not user.check_password(
            current_password
        ):
            raise ValueError("Your current password is not correct.")

        if new_password != request.form.get("confirm_password", ""):
            raise ValueError("The new passwords do not match.")

        validate_password(new_password)

    except ValueError as validation_error:
        return (
            render_template("change_password.html", error=str(validation_error)),
            400,
        )

    user.set_password(new_password)
    db.session.commit()

    # Other browsers now hold an outdated fingerprint and are signed
    # out; this one gets a new session.
    start_session(user)
    current_app.logger.info("User %s changed their password.", user.id)
    flash("Your password was changed. Other devices were signed out.", "success")
    return redirect(url_for("main.dashboard"))


def register_sign_in(app):
    app.before_request(require_sign_in)

    @app.context_processor
    def sign_in_context():
        return {"current_user": current_user()}

"""Database models.

Schema changes here must be paired with a Flask-Migrate migration.
Every unique and foreign-key constraint is named so SQLite batch
migrations can find it.
"""

from datetime import datetime, timezone

from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db
from validators import is_safe_external_url


# A password hash that no password matches. Used for the owner account
# the multi-user migration creates, until `flask set-password` runs.
UNUSABLE_PASSWORD = "!"


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(db.Model):
    __tablename__ = "users"

    __table_args__ = (
        db.UniqueConstraint("username", name="uq_user_username"),
    )

    id = db.Column(db.Integer, primary_key=True)
    # Stored in lower case, so "Samir" and "samir" are the same user.
    username = db.Column(db.String(30), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    social_accounts = db.relationship(
        "SocialAccount",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    posts = db.relationship(
        "Post",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def set_password(self, password):
        # Werkzeug's scrypt hash, with a random salt per password.
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


def user_id_column(table):
    return db.Column(
        db.Integer,
        db.ForeignKey(
            "users.id",
            name=f"fk_{table}_user_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )


class SocialAccount(db.Model):
    __tablename__ = "social_account"

    # Unique per user: two users may connect the same channel, and each
    # gets a separate copy of its posts.
    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "platform",
            "external_account_id",
            name="uq_social_account_user_platform_external_id",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = user_id_column("social_account")
    platform = db.Column(db.String(50), nullable=False)
    account_name = db.Column(db.String(150), nullable=False)
    account_handle = db.Column(db.String(150))
    external_account_id = db.Column(
        db.String(150),
        nullable=False,
    )
    last_synced_at = db.Column(db.DateTime)

    # OAuth access token for platforms that need one (Instagram),
    # encrypted with token_vault.encrypt_token(). Never stored in plain text.
    encrypted_access_token = db.Column(db.Text)
    access_token_expires_at = db.Column(db.DateTime)

    # TikTok access tokens last 24 hours, so a long-lived refresh token
    # (also encrypted) is kept to get new ones.
    encrypted_refresh_token = db.Column(db.Text)
    refresh_token_expires_at = db.Column(db.DateTime)

    posts = db.relationship(
        "Post",
        back_populates="social_account",
        lazy=True,
    )
    user = db.relationship("User", back_populates="social_accounts")


class Post(db.Model):
    __tablename__ = "post"

    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "platform",
            "external_id",
            name="uq_post_user_platform_external_id",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = user_id_column("post")
    platform = db.Column(db.String(50), nullable=False)
    caption = db.Column(db.String(300), nullable=False)
    content_type = db.Column(
        db.String(50),
        nullable=False,
    )
    posted_at = db.Column(db.DateTime, nullable=False)
    views = db.Column(db.Integer, nullable=False, default=0)
    likes = db.Column(db.Integer, nullable=False, default=0)
    comments = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )
    shares = db.Column(db.Integer, nullable=False, default=0)
    saves = db.Column(db.Integer, nullable=False, default=0)

    external_id = db.Column(db.String(150))
    external_url = db.Column(db.String(500))
    thumbnail_url = db.Column(db.String(500))

    source = db.Column(
        db.String(30),
        nullable=False,
        default="manual",
    )

    social_account_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "social_account.id",
            name="fk_post_social_account_id",
        ),
    )

    social_account = db.relationship(
        "SocialAccount",
        back_populates="posts",
    )
    user = db.relationship("User", back_populates="posts")

    metric_snapshots = db.relationship(
        "MetricSnapshot",
        back_populates="post",
        cascade="all, delete-orphan",
        order_by="MetricSnapshot.captured_at",
    )

    @property
    def total_interactions(self):
        return (
            (self.likes or 0)
            + (self.comments or 0)
            + (self.shares or 0)
            + (self.saves or 0)
        )

    @property
    def engagement_rate(self):
        if not self.views:
            return 0

        return round(
            (self.total_interactions / self.views) * 100,
            2,
        )

    @property
    def is_editable(self):
        return self.source in {
            "manual",
            "csv_import",
        }

    @property
    def safe_external_url(self):
        # Never render a stored URL as a link unless it is http(s).
        # This blocks schemes such as javascript: even if one was
        # saved before input validation existed.
        if is_safe_external_url(self.external_url):
            return self.external_url

        return None


class MetricSnapshot(db.Model):
    __tablename__ = "metric_snapshot"

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "post.id",
            name="fk_metric_snapshot_post_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    captured_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc).replace(
            tzinfo=None
        ),
    )
    views = db.Column(db.Integer, nullable=False, default=0)
    likes = db.Column(db.Integer, nullable=False, default=0)
    comments = db.Column(db.Integer, nullable=False, default=0)
    shares = db.Column(db.Integer, nullable=False, default=0)
    saves = db.Column(db.Integer, nullable=False, default=0)

    post = db.relationship(
        "Post",
        back_populates="metric_snapshots",
    )

    @property
    def total_interactions(self):
        return (
            (self.likes or 0)
            + (self.comments or 0)
            + (self.shares or 0)
            + (self.saves or 0)
        )

    @property
    def engagement_rate(self):
        if not self.views:
            return 0

        return round(
            (self.total_interactions / self.views) * 100,
            2,
        )


def record_metric_snapshot(post, captured_at=None):
    snapshot = MetricSnapshot(
        captured_at=(
            captured_at
            or datetime.now(timezone.utc).replace(tzinfo=None)
        ),
        views=post.views or 0,
        likes=post.likes or 0,
        comments=post.comments or 0,
        shares=post.shares or 0,
        saves=post.saves or 0,
    )

    post.metric_snapshots.append(snapshot)
    return snapshot


class FailedLogin(db.Model):
    """One failed sign-in, kept long enough to enforce the lockout.

    Keyed by the username that was typed, whether or not it exists, so
    a lockout does not reveal which usernames are real.
    """

    __tablename__ = "failed_login"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), nullable=False, index=True)
    attempted_at = db.Column(
        db.DateTime,
        nullable=False,
        default=utc_now,
        index=True,
    )

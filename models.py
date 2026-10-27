"""Database models.

Schema changes here must be paired with a Flask-Migrate migration.
Every unique and foreign-key constraint is named so SQLite batch
migrations can find it.
"""

from datetime import datetime, timezone

from extensions import db
from validators import is_safe_external_url


class SocialAccount(db.Model):
    __tablename__ = "social_account"

    __table_args__ = (
        db.UniqueConstraint(
            "platform",
            "external_account_id",
            name="uq_social_account_platform_external_id",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
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


class Post(db.Model):
    __tablename__ = "post"

    __table_args__ = (
        db.UniqueConstraint(
            "platform",
            "external_id",
            name="uq_post_platform_external_id",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
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

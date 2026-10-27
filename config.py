"""Configuration for each environment.

Environment variables are read when a config object is created,
not when this module is imported, so load_dotenv() always runs first.
"""

import os
import secrets


class Config:
    DEBUG = False
    TESTING = False
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024

    # The session cookie holds the CSRF token. Lax keeps browsers from
    # sending it with most cross-site requests, a second CSRF defense.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    def __init__(self):
        self.SQLALCHEMY_DATABASE_URI = os.getenv(
            "DATABASE_URL",
            "sqlite:///tracker.db",
        )
        self.YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
        # Signs the session cookie and CSRF tokens.
        self.SECRET_KEY = os.getenv("SECRET_KEY")

        # Instagram API with Instagram Login (a Meta developer app).
        self.INSTAGRAM_APP_ID = os.getenv("INSTAGRAM_APP_ID")
        self.INSTAGRAM_APP_SECRET = os.getenv("INSTAGRAM_APP_SECRET")
        self.INSTAGRAM_REDIRECT_URI = os.getenv("INSTAGRAM_REDIRECT_URI")
        # TikTok Login Kit and Display API (a TikTok developer app).
        self.TIKTOK_CLIENT_KEY = os.getenv("TIKTOK_CLIENT_KEY")
        self.TIKTOK_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET")
        self.TIKTOK_REDIRECT_URI = os.getenv("TIKTOK_REDIRECT_URI")
        # Encrypts stored OAuth tokens. See token_vault.py.
        self.TOKEN_ENCRYPTION_KEY = os.getenv("TOKEN_ENCRYPTION_KEY")


class DevelopmentConfig(Config):
    DEBUG = True

    def __init__(self):
        super().__init__()

        # A random key per run is safe for local work. Forms left open
        # across a restart just need a page reload.
        if not self.SECRET_KEY:
            self.SECRET_KEY = secrets.token_hex(32)


class ProductionConfig(Config):
    DEBUG = False

    def __init__(self):
        super().__init__()

        # Fail at startup instead of silently writing to a local SQLite
        # file that a container would lose on restart.
        if not os.getenv("DATABASE_URL"):
            raise ValueError(
                "DATABASE_URL must be set when APP_ENV=production."
            )

        # A random fallback cannot work here: each server worker process
        # would generate its own key and reject the others' CSRF tokens.
        if not self.SECRET_KEY:
            raise ValueError(
                "SECRET_KEY must be set when APP_ENV=production."
            )

        # Send the session cookie only over HTTPS. Set
        # SESSION_COOKIE_SECURE=false only for plain-HTTP setups such as
        # a local container.
        self.SESSION_COOKIE_SECURE = (
            os.getenv("SESSION_COOKIE_SECURE", "true").lower() != "false"
        )


class TestingConfig(Config):
    TESTING = True

    def __init__(self):
        super().__init__()

        # Never touch the real database or the real API key, no matter
        # what DATABASE_URL or .env contain. Tests that need a key set
        # a fake one.
        self.SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        self.YOUTUBE_API_KEY = None
        self.SECRET_KEY = "test-secret-key"
        self.INSTAGRAM_APP_ID = None
        self.INSTAGRAM_APP_SECRET = None
        self.INSTAGRAM_REDIRECT_URI = None
        self.TIKTOK_CLIENT_KEY = None
        self.TIKTOK_CLIENT_SECRET = None
        self.TIKTOK_REDIRECT_URI = None
        self.TOKEN_ENCRYPTION_KEY = None

        # Most tests post forms directly. tests/test_csrf.py turns
        # protection back on to check it.
        self.WTF_CSRF_ENABLED = False


CONFIGS = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config(name):
    try:
        return CONFIGS[name]()
    except KeyError:
        choices = ", ".join(sorted(CONFIGS))
        raise ValueError(
            f"Unknown APP_ENV {name!r}. Use one of: {choices}."
        ) from None

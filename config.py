"""Configuration for each environment.

Environment variables are read when a config object is created,
not when this module is imported, so load_dotenv() always runs first.
"""

import os


class Config:
    DEBUG = False
    TESTING = False
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024

    def __init__(self):
        self.SQLALCHEMY_DATABASE_URI = os.getenv(
            "DATABASE_URL",
            "sqlite:///tracker.db",
        )
        self.YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")


class DevelopmentConfig(Config):
    DEBUG = True


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


class TestingConfig(Config):
    TESTING = True

    def __init__(self):
        super().__init__()

        # Never touch the real database or the real API key, no matter
        # what DATABASE_URL or .env contain. Tests that need a key set
        # a fake one.
        self.SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        self.YOUTUBE_API_KEY = None


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

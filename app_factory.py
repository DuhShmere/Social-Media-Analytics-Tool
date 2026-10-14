"""Builds a configured Flask application."""

import os

from dotenv import load_dotenv
from flask import Flask

import models  # noqa: F401  Registers the models for Flask-Migrate.
from config import get_config
from errors import register_error_handlers
from extensions import csrf, db, migrate
from logging_config import configure_logging, register_request_logging
from routes import register_blueprints
from security import register_security_headers
from services.youtube import ApiKeyRedactionFilter


BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def create_app(config_name="development"):
    if config_name != "testing":
        load_dotenv(os.path.join(BASE_DIR, ".env"))

    # Before app.logger is first used, so Flask does not add its own
    # handler and every line is printed once.
    configure_logging(config_name)

    # The paths are explicit so the app can have a readable logger name
    # without Flask guessing its folders. instance_path matters most:
    # the default sqlite:///tracker.db lives there, and Flask would
    # otherwise fall back to the current working directory.
    app = Flask(
        "social_analytics",
        root_path=BASE_DIR,
        instance_path=os.path.join(BASE_DIR, "instance"),
    )
    app.config.from_object(get_config(config_name))

    # Errors from the YouTube client can include the API key in a URL.
    # Every app shares one logger, so only add the filter once.
    if not any(
        isinstance(log_filter, ApiKeyRedactionFilter)
        for log_filter in app.logger.filters
    ):
        app.logger.addFilter(ApiKeyRedactionFilter())

    # First, so every request has an ID and start time before any other
    # hook (such as the CSRF check) can reject it.
    register_request_logging(app)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)

    register_blueprints(app)
    register_error_handlers(app)
    register_security_headers(app)

    return app

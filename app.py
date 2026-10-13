import os

from dotenv import load_dotenv
from flask import Flask

import models  # noqa: F401  Registers the models for Flask-Migrate.
from errors import register_error_handlers
from extensions import db, migrate
from routes import register_blueprints
from services.youtube import ApiKeyRedactionFilter


# ---------------------------------------------------------
# Application setup
# ---------------------------------------------------------

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")

load_dotenv(ENV_PATH)

app = Flask(__name__)

app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
    "DATABASE_URL",
    "sqlite:///tracker.db",
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
app.config["YOUTUBE_API_KEY"] = os.getenv("YOUTUBE_API_KEY")

# Errors from the YouTube client can include the API key in a URL.
app.logger.addFilter(ApiKeyRedactionFilter())

db.init_app(app)
migrate.init_app(app, db)

register_blueprints(app)
register_error_handlers(app)


# ---------------------------------------------------------
# Start application
# ---------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, port=5001)

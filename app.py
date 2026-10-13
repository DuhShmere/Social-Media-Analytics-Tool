"""Entry point for `python app.py` and `flask --app app ...`.

APP_ENV chooses the configuration: development (default),
production, or testing.
"""

import os

from app_factory import create_app


app = create_app(os.getenv("APP_ENV", "development"))


if __name__ == "__main__":
    app.run(port=5001)

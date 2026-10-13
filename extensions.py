"""Flask extension objects shared by the app, models, and migrations.

They are created here without an app and attached in app.py with
init_app(). Keeping them separate avoids circular imports between
app.py and models.py.
"""

from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()
migrate = Migrate()

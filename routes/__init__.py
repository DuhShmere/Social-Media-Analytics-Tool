"""Route blueprints, grouped by feature."""

from routes import api, health, imports, main, posts


def register_blueprints(app):
    for module in (main, posts, imports, api, health):
        app.register_blueprint(module.bp)

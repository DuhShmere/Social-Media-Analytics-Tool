"""Route blueprints, grouped by feature."""

from routes import api, imports, main, posts


def register_blueprints(app):
    for module in (main, posts, imports, api):
        app.register_blueprint(module.bp)

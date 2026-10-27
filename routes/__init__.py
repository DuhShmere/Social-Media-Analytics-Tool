"""Route blueprints, grouped by feature."""

from routes import api, health, imports, instagram, main, posts, tiktok


def register_blueprints(app):
    for module in (main, posts, imports, instagram, tiktok, api, health):
        app.register_blueprint(module.bp)

    app.register_blueprint(tiktok.verification_bp)

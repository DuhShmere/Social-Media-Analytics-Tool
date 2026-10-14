"""404, 413, CSRF, and 500 handlers. /api/ routes get JSON instead of HTML."""

from flask import current_app, jsonify, render_template, request

from flask_wtf.csrf import CSRFError

from extensions import db


def wants_json_response():
    return request.path.startswith("/api/")


def page_not_found(error):
    if wants_json_response():
        return jsonify({"error": "Not found."}), 404

    return render_template("errors/404.html"), 404


def upload_too_large(error):
    limit_mb = current_app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    message = (
        f"The file is too large. The maximum upload size is {limit_mb} MB."
    )

    if wants_json_response():
        return jsonify({"error": message}), 413

    # The CSV import is the only form that accepts files, so show the
    # message there and let the user choose a smaller file.
    return render_template("import_csv.html", error=message), 413


def csrf_failed(error):
    # Usually a page left open too long or across a server restart.
    current_app.logger.warning("CSRF check failed: %s", error.description)
    return render_template("errors/csrf.html"), 400


def internal_server_error(error):
    # Flask has already logged the exception. Roll back so a failed
    # transaction is never left open on the session.
    db.session.rollback()

    if wants_json_response():
        return jsonify({"error": "Internal server error."}), 500

    return render_template("errors/500.html"), 500


def register_error_handlers(app):
    app.register_error_handler(404, page_not_found)
    app.register_error_handler(413, upload_too_large)
    app.register_error_handler(CSRFError, csrf_failed)
    app.register_error_handler(500, internal_server_error)

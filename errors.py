"""404 and 500 handlers. /api/ routes get JSON instead of HTML."""

from flask import jsonify, render_template, request

from extensions import db


def wants_json_response():
    return request.path.startswith("/api/")


def page_not_found(error):
    if wants_json_response():
        return jsonify({"error": "Not found."}), 404

    return render_template("errors/404.html"), 404


def internal_server_error(error):
    # Flask has already logged the exception. Roll back so a failed
    # transaction is never left open on the session.
    db.session.rollback()

    if wants_json_response():
        return jsonify({"error": "Internal server error."}), 500

    return render_template("errors/500.html"), 500


def register_error_handlers(app):
    app.register_error_handler(404, page_not_found)
    app.register_error_handler(500, internal_server_error)

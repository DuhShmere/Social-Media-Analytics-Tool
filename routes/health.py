"""Health check for load balancers, containers, and uptime monitors."""

from flask import Blueprint, current_app, jsonify
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from extensions import db


bp = Blueprint("health", __name__)


@bp.get("/health")
def health():
    try:
        db.session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        db.session.rollback()
        # Log the details; never expose them to callers.
        current_app.logger.exception("Health check: database unavailable.")
        response = jsonify({"status": "error", "database": "unavailable"})
        response.status_code = 503
    else:
        response = jsonify({"status": "ok", "database": "ok"})

    # A cached "ok" would hide an outage.
    response.headers["Cache-Control"] = "no-store"
    return response

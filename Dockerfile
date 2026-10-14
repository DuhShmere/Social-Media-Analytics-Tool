# Production image: gunicorn serving the Flask app as a non-root user.
#
# Build:  docker build -t social-analytics .
# Run:    docker run -p 8000:8000 \
#           -e SECRET_KEY=... \
#           -e DATABASE_URL=sqlite:////app/instance/tracker.db \
#           -e SESSION_COOKIE_SECURE=false \
#           -e YOUTUBE_API_KEY=... \
#           -v analytics-data:/app/instance \
#           social-analytics

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_ENV=production

WORKDIR /app

# Install dependencies first, so code changes reuse this cached layer.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Run as an unprivileged user. instance/ holds the SQLite database
# and is the place to mount a volume.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/instance \
    && chown appuser:appuser /app/instance

COPY --chown=appuser:appuser . .

USER appuser

EXPOSE 8000

# Marks the container unhealthy if the app or its database stops
# responding. Uses Python because the slim image has no curl.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/health', timeout=4)"

ENTRYPOINT ["./docker-entrypoint.sh"]

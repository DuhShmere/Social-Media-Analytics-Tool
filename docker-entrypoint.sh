#!/bin/sh
# Bring the database schema up to date, then replace this shell with
# gunicorn so it receives stop signals directly.
set -e

flask --app app db upgrade

exec gunicorn "app:app" \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --timeout 120

#!/bin/sh
set -eu

if [ "$#" -eq 1 ] && [ "$1" = "gunicorn" ]; then
    set -- gunicorn config.wsgi:application \
        --bind 0.0.0.0:8000 \
        --workers "${GUNICORN_WORKERS:-3}" \
        --worker-tmp-dir /dev/shm \
        --timeout "${GUNICORN_TIMEOUT:-30}" \
        --graceful-timeout "${GUNICORN_GRACEFUL_TIMEOUT:-30}" \
        --keep-alive "${GUNICORN_KEEPALIVE:-5}" \
        --max-requests "${GUNICORN_MAX_REQUESTS:-2000}" \
        --max-requests-jitter "${GUNICORN_MAX_REQUESTS_JITTER:-200}" \
        --access-logfile - \
        --error-logfile - \
        --capture-output
fi

exec "$@"

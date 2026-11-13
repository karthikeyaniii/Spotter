#!/bin/sh
set -e
# Single-container runs can migrate on start (RUN_MIGRATIONS=1). With several
# replicas, run migrations once as a separate job instead (see docker-compose.yml).
if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  python manage.py migrate --noinput
fi
exec "$@"

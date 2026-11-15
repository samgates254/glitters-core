#!/bin/sh
set -eu

if [ "${GLITTERS_AUTO_MIGRATE:-false}" = "true" ]; then
  python manage.py migrate --noinput
fi

exec "$@"

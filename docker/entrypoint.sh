#!/bin/sh
set -e
echo "Warte auf PostgreSQL ..."
until pg_isready -h "$POSTGRES_HOST" -p "${POSTGRES_PORT:-5432}" -U "$POSTGRES_USER" >/dev/null 2>&1; do sleep 1; done
python manage.py migrate --noinput
python manage.py collectstatic --noinput
python manage.py erp_grunddaten
if [ "${ERP_DEMODATEN:-0}" = "1" ]; then python manage.py erp_demodaten; fi
exec "$@"

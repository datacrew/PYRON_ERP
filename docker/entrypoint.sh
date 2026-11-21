#!/bin/sh
# Start-Skript des App-Containers: wartet auf PostgreSQL, migriert, sammelt Static-Dateien, startet Gunicorn.
set -e
echo "[erp] Warte auf PostgreSQL ${POSTGRES_HOST:-localhost}:${POSTGRES_PORT:-5432} ..."
python - <<'PY'
import os, sys, time
import psycopg2
for versuch in range(90):
    try:
        psycopg2.connect(dbname=os.environ.get("POSTGRES_DB", "erp"), user=os.environ.get("POSTGRES_USER", "erp"),
                         password=os.environ.get("POSTGRES_PASSWORD", ""), host=os.environ.get("POSTGRES_HOST", "localhost"),
                         port=os.environ.get("POSTGRES_PORT", "5432"), connect_timeout=3).close()
        sys.exit(0)
    except psycopg2.OperationalError:
        time.sleep(2)
print("[erp] FEHLER: PostgreSQL nicht erreichbar.", file=sys.stderr)
sys.exit(1)
PY
python manage.py migrate --noinput
python manage.py collectstatic --noinput -v0
# Grunddaten (Module, Rollen, Kontenrahmen, Nummernkreise) – idempotent
if [ "${ERP_GRUNDDATEN:-1}" = "1" ]; then python manage.py erp_grunddaten; fi
# Demodaten NIE im Produktivsystem (nur Test-/Schulungsinstanz)
if [ "${ERP_DEMODATEN:-0}" = "1" ]; then
    echo "[erp] WARNUNG: ERP_DEMODATEN=1 – Demodaten mit bekannten Passwörtern werden geladen!"
    python manage.py erp_demodaten
fi
exec "$@"

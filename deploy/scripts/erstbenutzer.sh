#!/usr/bin/env bash
# =====================================================================================
# ERP Mittelstand – ersten Administrator anlegen
#
# Aufruf:  erstbenutzer.sh [--docker] <benutzername> [e-mail]
#   --docker   Befehl im laufenden app-Container ausführen (im Verzeichnis deploy/ aufrufen)
#
# Das Passwort wird abgefragt (mind. 8 Zeichen, nicht zu einfach, nicht dem Namen ähnlich).
# Bereits vorhandenen Benutzer zurücksetzen: ERP_PASSWORT_SETZEN=1 erstbenutzer.sh <name>
# =====================================================================================
set -euo pipefail
SKRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=SCRIPTDIR/gemeinsam.sh
source "$SKRIPT_DIR/gemeinsam.sh"

DOCKER=0
if [[ "${1:-}" == "--docker" ]]; then DOCKER=1; shift; fi
case "${1:-}" in -h|--hilfe|--help|"") sed -n '2,10p' "$0"; exit 0 ;; esac
NAME="$1"; EMAIL="${2:-}"

args=(erp_erstbenutzer --benutzername "$NAME")
[[ -n "$EMAIL" ]] && args+=(--email "$EMAIL")
[[ "${ERP_PASSWORT_SETZEN:-0}" == "1" ]] && args+=(--passwort-setzen)

if [[ "$DOCKER" == "1" ]]; then
    COMPOSE_DATEI="${COMPOSE_DATEI:-$SKRIPT_DIR/../docker-compose.prod.yml}"
    exec docker compose -f "$COMPOSE_DATEI" exec app python manage.py "${args[@]}"
fi

APP_DIR="$(cd "$SKRIPT_DIR/../.." && pwd)"
PYTHON="${ERP_PYTHON:-$APP_DIR/../venv/bin/python}"
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"
cd "$APP_DIR"
# Nativ als Systembenutzer „erp“ ausführen (Zugriff auf .env und Media)
if [[ "$(id -u)" == "0" ]] && id erp >/dev/null 2>&1; then
    exec sudo -u erp "$PYTHON" manage.py "${args[@]}"
fi
exec "$PYTHON" manage.py "${args[@]}"

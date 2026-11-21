#!/usr/bin/env bash
# =====================================================================================
# ERP Mittelstand – Update einspielen
#
# Aufruf:  update.sh [--docker] [--paket erp_system_release.tar.gz] [--ohne-backup]
#   --docker        Docker-Weg (Images neu bauen, Container neu starten)
#   --paket DATEI   neues Release-Archiv einspielen; ohne Angabe: „git pull“ im Projektverzeichnis
#   --ohne-backup   KEINE Sicherung vorher (nicht empfohlen)
#
# Ablauf: 1. Sicherung (backup.sh --manuell)  2. Code aktualisieren (.env, Media, venv bleiben erhalten)
#         3. Abhängigkeiten  4. Migrationen + collectstatic + Grunddaten  5. Neustart  6. Gesundheitsprüfung
# Nativ als root ausführen (sudo).
# =====================================================================================
set -euo pipefail
SKRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=SCRIPTDIR/gemeinsam.sh
source "$SKRIPT_DIR/gemeinsam.sh"

DOCKER=0; PAKET=""; BACKUP=1
while [[ $# -gt 0 ]]; do
    case "$1" in
        --docker) DOCKER=1 ;;
        --paket) PAKET="$(readlink -f "${2:?--paket braucht eine Datei}")"; shift ;;
        --ohne-backup) BACKUP=0 ;;
        -h|--hilfe|--help) sed -n '2,13p' "$0"; exit 0 ;;
        *) abbruch "Unbekannter Schalter: $1 (siehe --hilfe)" ;;
    esac
    shift
done
APP_DIR="$(cd "$SKRIPT_DIR/../.." && pwd)"
[[ -z "$PAKET" || -f "$PAKET" ]] || abbruch "Paket $PAKET nicht gefunden."

code_aktualisieren() {
    if [[ -n "$PAKET" ]]; then
        info "Neues Paket wird entpackt: $PAKET"
        local tmp
        tmp="$(mktemp -d)"
        tar -xzf "$PAKET" -C "$tmp"
        local quelle="$tmp"
        [[ -f "$tmp/manage.py" ]] || quelle="$(dirname "$(find "$tmp" -maxdepth 2 -name manage.py | head -n1)")"
        [[ -f "$quelle/manage.py" ]] || abbruch "Paket enthält kein ERP-Projekt (manage.py fehlt)."
        rsync -a --delete \
            --exclude '.env' --exclude 'deploy/.env' --exclude 'media/' --exclude 'staticfiles/' \
            --exclude 'venv/' --exclude '.venv/' --exclude '.git/' --exclude 'deploy/backups/' \
            --exclude 'deploy/rclone/' --exclude 'deploy/ssh/' \
            "$quelle/" "$APP_DIR/"
        rm -rf "$tmp"
    elif [[ -d "$APP_DIR/.git" ]]; then
        info "git pull in $APP_DIR"
        git -C "$APP_DIR" pull --ff-only
    else
        abbruch "Kein Git-Repository und kein --paket angegeben."
    fi
    ok "Code aktualisiert"
}

if [[ "$DOCKER" == "1" ]]; then
    COMPOSE=(docker compose -f "$SKRIPT_DIR/../docker-compose.prod.yml")
    if [[ "$BACKUP" == "1" ]]; then
        info "Sicherung vor dem Update …"
        "${COMPOSE[@]}" exec -T backup /skripte/backup.sh --manuell
    fi
    code_aktualisieren
    info "Images werden neu gebaut …"
    "${COMPOSE[@]}" build --pull app backup
    "${COMPOSE[@]}" up -d
    info "Warte auf den Start (Migrationen laufen im app-Container) …"
    for _ in $(seq 1 60); do
        zustand=$(docker inspect -f '{{.State.Health.Status}}' "$("${COMPOSE[@]}" ps -q app)" 2>/dev/null || echo unbekannt)
        [[ "$zustand" == "healthy" ]] && break
        sleep 5
    done
    [[ "${zustand:-}" == "healthy" ]] || abbruch "app-Container nicht gesund – Protokoll: docker compose -f docker-compose.prod.yml logs app"
    "${COMPOSE[@]}" exec -T app python manage.py erp_pruefen
    ok "Update abgeschlossen."
    exit 0
fi

[[ "$(id -u)" == "0" ]] || abbruch "Bitte als root ausführen (sudo $0 …)."
VENV="${ERP_VENV:-$(dirname "$APP_DIR")/venv}"
[[ -x "$VENV/bin/python" ]] || abbruch "Python-Umgebung $VENV nicht gefunden."
als_erp() { sudo -u erp -H "$@"; }

if [[ "$BACKUP" == "1" ]]; then
    info "Sicherung vor dem Update …"
    als_erp "$SKRIPT_DIR/backup.sh" --manuell
fi
code_aktualisieren
chown -R root:erp "$APP_DIR"
chmod 640 "$APP_DIR/.env" 2>/dev/null || true

info "Abhängigkeiten …"
"$VENV/bin/pip" install -q -r "$APP_DIR/requirements.txt"
info "Migrationen, Static-Dateien, Grunddaten …"
cd "$APP_DIR"
als_erp "$VENV/bin/python" manage.py migrate --noinput
als_erp "$VENV/bin/python" manage.py collectstatic --noinput -v0
als_erp "$VENV/bin/python" manage.py erp_grunddaten
als_erp "$VENV/bin/python" manage.py check --deploy --fail-level ERROR

info "Dienst wird neu gestartet …"
systemctl restart erp
for _ in $(seq 1 30); do
    curl -fsS -m 5 http://127.0.0.1:8000/gesundheit/ >/dev/null 2>&1 && break
    sleep 2
done
curl -fsS -m 5 http://127.0.0.1:8000/gesundheit/ >/dev/null || abbruch "Anwendung antwortet nicht – journalctl -u erp -n 100"
als_erp "$VENV/bin/python" manage.py erp_pruefen
ok "Update abgeschlossen."

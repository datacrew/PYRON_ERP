#!/usr/bin/env bash
# =====================================================================================
# ERP Mittelstand – Gesundheitsprüfung für Betrieb und Monitoring
#
# Prüft: Anwendung (/gesundheit/), Datenbank, freien Speicherplatz, Alter der letzten Sicherung.
# Exit-Code: 0 = alles in Ordnung, 1 = Warnung, 2 = Fehler (kompatibel mit Nagios/Icinga/Checkmk).
#
# Umgebung (optional, sonst aus .env):
#   ERP_URL                 Standard http://127.0.0.1:8000/gesundheit/ (Docker: https://DOMAIN/gesundheit/)
#   BACKUP_VERZEICHNIS      Standard /var/backups/erp
#   SPEICHER_WARNUNG        Belegung in % ab der gewarnt wird (85), SPEICHER_FEHLER (95)
#   BACKUP_MAX_STUNDEN      max. Alter der letzten Sicherung (26)
# =====================================================================================
set -uo pipefail
SKRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=SCRIPTDIR/gemeinsam.sh
source "$SKRIPT_DIR/gemeinsam.sh"
case "${1:-}" in -h|--hilfe|--help) sed -n '2,14p' "$0"; exit 0 ;; esac

for kandidat in "${ERP_ENV_DATEI:-}" "$SKRIPT_DIR/../../.env" "$SKRIPT_DIR/../.env"; do
    if [[ -n "$kandidat" && -f "$kandidat" ]]; then env_laden "$kandidat"; break; fi
done
pg_umgebung
if [[ -z "${ERP_URL:-}" && -n "${DOMAIN:-}" && "${DOMAIN}" != \<* && -f "$SKRIPT_DIR/../.env" && ! -f "$SKRIPT_DIR/../../.env" ]]; then
    ERP_URL="https://${DOMAIN}/gesundheit/"   # Docker-Weg: App-Port nicht veröffentlicht
fi
ERP_URL="${ERP_URL:-http://127.0.0.1:8000/gesundheit/}"
BACKUP_VERZEICHNIS="${BACKUP_VERZEICHNIS:-${BACKUP_HOST_VERZEICHNIS:-/var/backups/erp}}"
[[ "$BACKUP_VERZEICHNIS" == ./* ]] && BACKUP_VERZEICHNIS="$SKRIPT_DIR/../${BACKUP_VERZEICHNIS#./}"
SPEICHER_WARNUNG="${SPEICHER_WARNUNG:-85}"
SPEICHER_FEHLER="${SPEICHER_FEHLER:-95}"
BACKUP_MAX_STUNDEN="${BACKUP_MAX_STUNDEN:-26}"

STATUS=0
melde_warnung() { warnung "$*"; (( STATUS < 1 )) && STATUS=1; }
melde_fehler()  { fehler "$*"; STATUS=2; }

# 1) Anwendung
if antwort=$(curl -fsS -m 10 "$ERP_URL" 2>&1); then
    if [[ "$antwort" == *'"status": "ok"'* || "$antwort" == *'"status":"ok"'* ]]; then
        ok "Anwendung antwortet ($ERP_URL)"
    else
        melde_fehler "Anwendung meldet Problem: $antwort"
    fi
else
    melde_fehler "Anwendung nicht erreichbar ($ERP_URL): $antwort"
fi

# 2) Datenbank (direkt, falls Client vorhanden – beim Docker-Weg genügt Punkt 1)
if command -v pg_isready >/dev/null; then
    if pg_isready -q -t 5; then ok "PostgreSQL erreichbar ($PGHOST:$PGPORT)"; else melde_fehler "PostgreSQL nicht erreichbar ($PGHOST:$PGPORT)"; fi
fi

# 3) Speicherplatz
for pfad in / "${ERP_MEDIA_ROOT:-}" "$BACKUP_VERZEICHNIS" /var/lib/postgresql /var/lib/docker; do
    [[ -n "$pfad" && -d "$pfad" ]] || continue
    belegt=$(df -P "$pfad" | awk 'NR==2 {gsub("%","",$5); print $5}')
    if (( belegt >= SPEICHER_FEHLER )); then melde_fehler "Speicher $pfad: ${belegt} % belegt"
    elif (( belegt >= SPEICHER_WARNUNG )); then melde_warnung "Speicher $pfad: ${belegt} % belegt"
    else ok "Speicher $pfad: ${belegt} % belegt"; fi
done

# 4) Letzte Sicherung
if [[ -r "$BACKUP_VERZEICHNIS/letzte_sicherung" ]]; then
    IFS=$'\t' read -r zeit pfad < "$BACKUP_VERZEICHNIS/letzte_sicherung"
    alter=$(( ( $(date +%s) - zeit ) / 3600 ))
    if (( alter > BACKUP_MAX_STUNDEN )); then melde_fehler "Letzte Sicherung ist ${alter} h alt ($pfad)"
    else ok "Letzte Sicherung vor ${alter} h ($(basename "$pfad"))"; fi
else
    melde_fehler "Keine Sicherung gefunden in $BACKUP_VERZEICHNIS"
fi

case $STATUS in
    0) ok "Gesamtstatus: in Ordnung" ;;
    1) warnung "Gesamtstatus: Warnung" ;;
    *) fehler "Gesamtstatus: FEHLER" ;;
esac
exit $STATUS

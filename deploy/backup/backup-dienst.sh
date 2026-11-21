#!/usr/bin/env bash
# Dauerschleife des Sicherungs-Containers: führt backup.sh täglich um BACKUP_UHRZEIT (Standard 02:30) aus.
set -euo pipefail

UHRZEIT="${BACKUP_UHRZEIT:-02:30}"
echo "[backup] Sicherungsdienst gestartet – tägliche Sicherung um ${UHRZEIT} Uhr (TZ=${TZ:-UTC})."

if [[ "${BACKUP_BEIM_START:-0}" == "1" ]]; then
    /skripte/backup.sh || echo "[backup] FEHLER: Sicherung beim Start fehlgeschlagen." >&2
fi

while true; do
    jetzt=$(date +%s)
    ziel=$(date -d "today ${UHRZEIT}" +%s)
    if (( ziel <= jetzt )); then
        ziel=$(date -d "tomorrow ${UHRZEIT}" +%s)
    fi
    echo "[backup] Nächste Sicherung: $(date -d "@${ziel}" '+%d.%m.%Y %H:%M')"
    sleep $(( ziel - jetzt ))
    /skripte/backup.sh || echo "[backup] FEHLER: Sicherung fehlgeschlagen – bitte Protokoll prüfen." >&2
done

#!/usr/bin/env bash
# =====================================================================================
# ERP Mittelstand – Datensicherung
#
# Erstellt eine vollständige Sicherung:
#   db.dump       PostgreSQL-Dump im Custom-Format (pg_dump -Fc) inkl. GoBD-/Lagerjournal-Trigger,
#                 Funktionen und Hash-Ketten (Prüfsummen sind normale Spalten und bleiben erhalten)
#   media.tar.gz  Belegarchiv (festgeschriebene PDFs, GoBD) und übrige Media-Dateien
#   info.txt      Zeitpunkt, Datenbank, Werkzeugversionen
#   SHA256SUMS    Prüfsummen aller Dateien
#
# Ablage: BACKUP_VERZEICHNIS/{taeglich,woechentlich,monatlich,manuell}/JJJJ-MM-TT_HHMMSS/
# Rotation: BACKUP_TAEGLICH (7), BACKUP_WOECHENTLICH (4, sonntags), BACKUP_MONATLICH (12, am 1.),
#           BACKUP_MANUELL (10). Optional externe Kopie per rclone (BACKUP_RCLONE_ZIEL) und/oder
#           rsync (BACKUP_RSYNC_ZIEL).
#
# Aufruf:  backup.sh [--manuell] [--ohne-extern]
#   --manuell      Sicherung unter „manuell/“ ablegen (z. B. vor einem Update)
#   --ohne-extern  keine externe Kopie
#
# Konfiguration über Umgebung oder .env (ERP_ENV_DATEI, sonst automatisch gesucht).
# =====================================================================================
set -euo pipefail

SKRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=SCRIPTDIR/gemeinsam.sh
source "$SKRIPT_DIR/gemeinsam.sh"

TYP="taeglich"
EXTERN=1
while [[ $# -gt 0 ]]; do
    case "$1" in
        --manuell) TYP="manuell" ;;
        --ohne-extern) EXTERN=0 ;;
        -h|--hilfe|--help) sed -n '2,26p' "$0"; exit 0 ;;
        *) abbruch "Unbekannter Schalter: $1 (siehe --hilfe)" ;;
    esac
    shift
done

# .env einlesen: nativ liegt sie im Anwendungsverzeichnis, beim Docker-Weg in deploy/
for kandidat in "${ERP_ENV_DATEI:-}" "$SKRIPT_DIR/../../.env" "$SKRIPT_DIR/../.env"; do
    if [[ -n "$kandidat" && -f "$kandidat" ]]; then env_laden "$kandidat"; break; fi
done
pg_umgebung

BACKUP_VERZEICHNIS="${BACKUP_VERZEICHNIS:-/var/backups/erp}"
MEDIA_VERZEICHNIS="${MEDIA_VERZEICHNIS:-${ERP_MEDIA_ROOT:-}}"
BACKUP_TAEGLICH="${BACKUP_TAEGLICH:-7}"
BACKUP_WOECHENTLICH="${BACKUP_WOECHENTLICH:-4}"
BACKUP_MONATLICH="${BACKUP_MONATLICH:-12}"
BACKUP_MANUELL="${BACKUP_MANUELL:-10}"

for werkzeug in pg_dump pg_restore tar sha256sum; do
    command -v "$werkzeug" >/dev/null || abbruch "Werkzeug „$werkzeug“ fehlt (PostgreSQL-Client 17 installieren)."
done

umask 077
mkdir -p "$BACKUP_VERZEICHNIS"/{taeglich,woechentlich,monatlich,manuell}

# Nur eine Sicherung gleichzeitig
exec 9>"$BACKUP_VERZEICHNIS/.sperre"
if command -v flock >/dev/null && ! flock -n 9; then
    abbruch "Es läuft bereits eine Sicherung (Sperre $BACKUP_VERZEICHNIS/.sperre)."
fi

ZEITSTEMPEL="$(date +%Y-%m-%d_%H%M%S)"
ARBEIT="$BACKUP_VERZEICHNIS/.in_arbeit_$ZEITSTEMPEL"
aufraeumen() { rm -rf "$ARBEIT"; }
trap aufraeumen EXIT
mkdir -p "$ARBEIT"

info "Sicherung $ZEITSTEMPEL – Datenbank $PGDATABASE auf $PGHOST:$PGPORT"
pg_dump -Fc -Z 6 --no-password -f "$ARBEIT/db.dump"
# Lesbarkeit des Dumps prüfen (Inhaltsverzeichnis muss sich lesen lassen)
pg_restore --list "$ARBEIT/db.dump" > "$ARBEIT/inhalt.txt"
trigger_anzahl=$(grep -c ' TRIGGER ' "$ARBEIT/inhalt.txt" || true)
ok "Datenbank gesichert ($(du -h "$ARBEIT/db.dump" | cut -f1), $trigger_anzahl Trigger im Dump)"

if [[ -n "$MEDIA_VERZEICHNIS" && -d "$MEDIA_VERZEICHNIS" ]]; then
    tar -czf "$ARBEIT/media.tar.gz" -C "$MEDIA_VERZEICHNIS" .
    ok "Belegarchiv/Media gesichert ($(du -h "$ARBEIT/media.tar.gz" | cut -f1), $(find "$MEDIA_VERZEICHNIS" -type f | wc -l) Dateien)"
else
    warnung "Media-Verzeichnis nicht gefunden („${MEDIA_VERZEICHNIS:-nicht gesetzt}“) – archivierte Beleg-PDFs NICHT gesichert!"
fi

{
    echo "Sicherung:   $ZEITSTEMPEL ($TYP)"
    echo "Datenbank:   $PGDATABASE@$PGHOST:$PGPORT"
    echo "pg_dump:     $(pg_dump --version)"
    echo "Server:      $(hostname)"
    echo "Media:       ${MEDIA_VERZEICHNIS:-–}"
    echo "Trigger:     $trigger_anzahl"
} > "$ARBEIT/info.txt"
(cd "$ARBEIT" && sha256sum -- * > SHA256SUMS)

ZIEL="$BACKUP_VERZEICHNIS/$TYP/$ZEITSTEMPEL"
mv "$ARBEIT" "$ZIEL"
trap - EXIT
ok "Abgelegt: $ZIEL"

# Wöchentliche (Sonntag) und monatliche (1. Tag) Kopie – als Hardlinks, belegt keinen zusätzlichen Platz
if [[ "$TYP" == "taeglich" ]]; then
    if [[ "$(date +%u)" == "7" ]]; then
        cp -al "$ZIEL" "$BACKUP_VERZEICHNIS/woechentlich/$ZEITSTEMPEL" 2>/dev/null \
            || cp -a "$ZIEL" "$BACKUP_VERZEICHNIS/woechentlich/$ZEITSTEMPEL"
        ok "Wöchentliche Sicherung angelegt"
    fi
    if [[ "$(date +%d)" == "01" ]]; then
        cp -al "$ZIEL" "$BACKUP_VERZEICHNIS/monatlich/$ZEITSTEMPEL" 2>/dev/null \
            || cp -a "$ZIEL" "$BACKUP_VERZEICHNIS/monatlich/$ZEITSTEMPEL"
        ok "Monatliche Sicherung angelegt"
    fi
fi

# Rotation: nur die neuesten N Sicherungen je Stufe behalten
rotieren() {
    local stufe="$1" behalten="$2" alt
    while IFS= read -r alt; do
        [[ -n "$alt" ]] || continue
        rm -rf -- "$alt"
        info "Rotation: $stufe/$(basename "$alt") entfernt"
    done < <(find "$BACKUP_VERZEICHNIS/$stufe" -mindepth 1 -maxdepth 1 -type d -name '20*' | sort -r | tail -n +"$((behalten + 1))")
}
rotieren taeglich "$BACKUP_TAEGLICH"
rotieren woechentlich "$BACKUP_WOECHENTLICH"
rotieren monatlich "$BACKUP_MONATLICH"
rotieren manuell "$BACKUP_MANUELL"

printf '%s\t%s\n' "$(date +%s)" "$ZIEL" > "$BACKUP_VERZEICHNIS/letzte_sicherung"

# Externe Kopie (3-2-1-Regel). „copy“ statt „sync“: am Ziel wird nichts gelöscht –
# die Aufbewahrung dort (z. B. Objektsperre/Versionierung) regelt das Ziel selbst.
if [[ "$EXTERN" == "1" && -n "${BACKUP_RCLONE_ZIEL:-}" ]]; then
    command -v rclone >/dev/null || abbruch "BACKUP_RCLONE_ZIEL gesetzt, aber rclone ist nicht installiert."
    rclone copy "$BACKUP_VERZEICHNIS" "$BACKUP_RCLONE_ZIEL" --exclude '.sperre' --exclude '.in_arbeit_*/**'
    ok "Externe Kopie per rclone nach $BACKUP_RCLONE_ZIEL"
fi
if [[ "$EXTERN" == "1" && -n "${BACKUP_RSYNC_ZIEL:-}" ]]; then
    command -v rsync >/dev/null || abbruch "BACKUP_RSYNC_ZIEL gesetzt, aber rsync ist nicht installiert."
    rsync -a --exclude '.sperre' --exclude '.in_arbeit_*' "$BACKUP_VERZEICHNIS/" "$BACKUP_RSYNC_ZIEL"
    ok "Externe Kopie per rsync nach $BACKUP_RSYNC_ZIEL"
fi

ok "Sicherung abgeschlossen."

#!/usr/bin/env bash
# =====================================================================================
# ERP Mittelstand – Wiederherstellung einer Sicherung (von backup.sh)
#
# Aufruf:  restore.sh [Schalter] <SICHERUNGSVERZEICHNIS | letzte>
#   --ja               ohne Sicherheitsabfrage (für Skripte/Restore-Proben)
#   --ziel-db NAME     in diese Datenbank wiederherstellen (Standard: POSTGRES_DB = Produktivdatenbank!)
#                      Für Restore-Proben z. B. --ziel-db erp_restore_test
#   --media-ziel DIR   Belegarchiv hierhin entpacken (Standard: ERP_MEDIA_ROOT bzw. MEDIA_VERZEICHNIS)
#   --ohne-media       Belegarchiv nicht wiederherstellen
#   --ohne-pruefung    anschließende Integritätsprüfung (manage.py erp_pruefen) überspringen
#
# GoBD-Hinweise:
#   * Es wird IMMER die komplette Datenbank (Schema + Daten + Trigger) in eine LEERE Datenbank geladen.
#     pg_restore lädt zuerst die Daten und legt die Schutz-Trigger danach an – die Trigger werden dabei
#     weder umgangen noch deaktiviert. Niemals „--disable-triggers“ oder reine Daten-Restores verwenden!
#   * Die Hash-Ketten (Prüfsummen) der Belege und Fibu-Buchungen werden 1:1 übernommen und danach mit
#     „manage.py erp_pruefen“ verifiziert. Die Archiv-PDFs gehören zur Kette – Datenbank und Media immer
#     aus DERSELBEN Sicherung wiederherstellen.
#   * Vor der Wiederherstellung der Produktivdatenbank die Anwendung stoppen
#     (nativ: systemctl stop erp; Docker: docker compose -f docker-compose.prod.yml stop app).
# =====================================================================================
set -euo pipefail

SKRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=SCRIPTDIR/gemeinsam.sh
source "$SKRIPT_DIR/gemeinsam.sh"

JA=0; ZIEL_DB=""; MEDIA_ZIEL=""; OHNE_MEDIA=0; OHNE_PRUEFUNG=0; QUELLE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --ja) JA=1 ;;
        --ziel-db) ZIEL_DB="${2:?--ziel-db braucht einen Namen}"; shift ;;
        --media-ziel) MEDIA_ZIEL="${2:?--media-ziel braucht ein Verzeichnis}"; shift ;;
        --ohne-media) OHNE_MEDIA=1 ;;
        --ohne-pruefung) OHNE_PRUEFUNG=1 ;;
        -h|--hilfe|--help) sed -n '2,25p' "$0"; exit 0 ;;
        -*) abbruch "Unbekannter Schalter: $1 (siehe --hilfe)" ;;
        *) QUELLE="$1" ;;
    esac
    shift
done
[[ -n "$QUELLE" ]] || abbruch "Bitte Sicherungsverzeichnis oder „letzte“ angeben (siehe --hilfe)."

for kandidat in "${ERP_ENV_DATEI:-}" "$SKRIPT_DIR/../../.env" "$SKRIPT_DIR/../.env"; do
    if [[ -n "$kandidat" && -f "$kandidat" ]]; then env_laden "$kandidat"; break; fi
done
pg_umgebung
BACKUP_VERZEICHNIS="${BACKUP_VERZEICHNIS:-/var/backups/erp}"
PRODUKTIV_DB="$PGDATABASE"
ZIEL_DB="${ZIEL_DB:-$PRODUKTIV_DB}"
MEDIA_ZIEL="${MEDIA_ZIEL:-${MEDIA_VERZEICHNIS:-${ERP_MEDIA_ROOT:-}}}"
[[ "$ZIEL_DB" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || abbruch "Ungültiger Datenbankname: $ZIEL_DB"

if [[ "$QUELLE" == "letzte" ]]; then
    QUELLE="$(neueste_sicherung "$BACKUP_VERZEICHNIS")"
    [[ -n "$QUELLE" ]] || abbruch "Keine Sicherung in $BACKUP_VERZEICHNIS gefunden."
fi
QUELLE="$(cd "$QUELLE" 2>/dev/null && pwd)" || abbruch "Sicherungsverzeichnis nicht gefunden."
[[ -f "$QUELLE/db.dump" ]] || abbruch "In $QUELLE liegt keine db.dump."

info "Sicherung: $QUELLE"
[[ -f "$QUELLE/info.txt" ]] && sed 's/^/        /' "$QUELLE/info.txt"
info "Prüfsummen werden kontrolliert …"
(cd "$QUELLE" && sha256sum --quiet -c SHA256SUMS) || abbruch "Prüfsummen stimmen nicht – Sicherung beschädigt oder verändert!"
ok "Prüfsummen in Ordnung"

echo
if [[ "$ZIEL_DB" == "$PRODUKTIV_DB" ]]; then
    warnung "Ziel ist die PRODUKTIVDATENBANK „$ZIEL_DB“. Alle aktuellen Daten werden durch die Sicherung ersetzt!"
    warnung "Die Anwendung muss gestoppt sein. Erstellen Sie vorher ggf. eine aktuelle Sicherung (backup.sh --manuell)."
else
    info "Ziel-Datenbank: $ZIEL_DB (wird geleert bzw. neu angelegt)"
fi
if [[ "$OHNE_MEDIA" == "0" ]]; then info "Media-Ziel: ${MEDIA_ZIEL:-– (nicht gesetzt)}"; fi
if [[ "$JA" != "1" ]]; then
    read -r -p "Zum Fortfahren „WIEDERHERSTELLEN“ eingeben: " antwort
    [[ "$antwort" == "WIEDERHERSTELLEN" ]] || abbruch "Abgebrochen – nichts verändert."
fi

# ------------------------------------------------------------------ Datenbank
info "Datenbank $ZIEL_DB wird neu angelegt …"
psql -X -q -v ON_ERROR_STOP=1 -d postgres -v ziel="$ZIEL_DB" <<'SQL' >/dev/null
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :'ziel' AND pid <> pg_backend_pid();
SQL
dropdb --if-exists "$ZIEL_DB"
createdb -O "$PGUSER" -E UTF8 -T template0 "$ZIEL_DB"

info "Dump wird eingespielt (pg_restore, eine Transaktion) …"
pg_restore --no-owner --no-privileges --exit-on-error --single-transaction -d "$ZIEL_DB" "$QUELLE/db.dump"

erwartet=$(pg_restore --list "$QUELLE/db.dump" | grep -c ' TRIGGER ' || true)
vorhanden=$(psql -X -At -d "$ZIEL_DB" -c "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgenabled <> 'D'")
tabellen=$(psql -X -At -d "$ZIEL_DB" -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
[[ "$vorhanden" -ge "$erwartet" ]] || abbruch "Nur $vorhanden von $erwartet Triggern aktiv – Wiederherstellung unvollständig!"
ok "Datenbank wiederhergestellt: $tabellen Tabellen, $vorhanden aktive Trigger (im Dump: $erwartet)"

# ------------------------------------------------------------------ Belegarchiv / Media
if [[ "$OHNE_MEDIA" == "0" ]]; then
    if [[ ! -f "$QUELLE/media.tar.gz" ]]; then
        warnung "Sicherung enthält kein media.tar.gz – Belegarchiv nicht wiederhergestellt."
    elif [[ -z "$MEDIA_ZIEL" ]]; then
        warnung "Kein Media-Ziel bekannt (ERP_MEDIA_ROOT / --media-ziel) – Belegarchiv nicht wiederhergestellt."
    else
        mkdir -p "$MEDIA_ZIEL"
        if [[ -n "$(ls -A "$MEDIA_ZIEL" 2>/dev/null)" ]]; then
            # bisherigen Stand aufheben, nicht löschen
            mkdir -p "$BACKUP_VERZEICHNIS/manuell"
            vorher="$BACKUP_VERZEICHNIS/manuell/media_vor_wiederherstellung_$(date +%Y-%m-%d_%H%M%S).tar.gz"
            tar -czf "$vorher" -C "$MEDIA_ZIEL" .
            info "Bisheriges Media-Verzeichnis gesichert: $vorher"
            find "$MEDIA_ZIEL" -mindepth 1 -delete
        fi
        tar -xzf "$QUELLE/media.tar.gz" -C "$MEDIA_ZIEL"
        # Docker: Dateien dem App-Benutzer (uid 1000) zurückgeben, damit neue PDFs archiviert werden können
        if [[ -n "${MEDIA_BESITZER:-}" ]]; then chown -R "$MEDIA_BESITZER" "$MEDIA_ZIEL"; fi
        ok "Belegarchiv wiederhergestellt nach $MEDIA_ZIEL ($(find "$MEDIA_ZIEL" -type f | wc -l) Dateien)"
    fi
fi

# ------------------------------------------------------------------ Integritätsprüfung
if [[ "$OHNE_PRUEFUNG" == "1" ]]; then
    warnung "Integritätsprüfung übersprungen – bitte manuell ausführen: python manage.py erp_pruefen"
    exit 0
fi
APP_DIR="$(cd "$SKRIPT_DIR/../.." && pwd)"
PYTHON="${ERP_PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
    for p in "$APP_DIR/../venv/bin/python" "$APP_DIR/venv/bin/python" "$(command -v python3 || true)"; do
        if [[ -n "$p" && -x "$p" ]]; then PYTHON="$p"; break; fi
    done
fi
if [[ -f "$APP_DIR/manage.py" && -n "$PYTHON" ]]; then
    info "Integritätsprüfung (GoBD-Trigger, Hash-Ketten) …"
    pruef_args=()
    [[ "$OHNE_MEDIA" == "1" ]] && pruef_args+=(--ohne-pdf)
    (cd "$APP_DIR" && POSTGRES_DB="$ZIEL_DB" ERP_MEDIA_ROOT="${MEDIA_ZIEL:-}" "$PYTHON" manage.py erp_pruefen "${pruef_args[@]}") \
        || abbruch "Integritätsprüfung fehlgeschlagen – Sicherung NICHT produktiv verwenden, Ursache klären!"
else
    warnung "manage.py nicht erreichbar (z. B. im Backup-Container). Prüfung bitte ausführen:"
    warnung "  docker compose -f docker-compose.prod.yml exec app python manage.py erp_pruefen"
fi
ok "Wiederherstellung abgeschlossen."
[[ "$ZIEL_DB" == "$PRODUKTIV_DB" ]] && info "Anwendung wieder starten (systemctl start erp bzw. docker compose … start app)."
exit 0

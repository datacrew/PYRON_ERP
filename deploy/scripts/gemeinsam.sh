#!/usr/bin/env bash
# Gemeinsame Hilfsfunktionen der Betriebsskripte (wird per „source“ eingebunden, nicht direkt ausführen).
# shellcheck disable=SC2034

info()    { printf '\033[1;34m[erp]\033[0m %s\n' "$*"; }
ok()      { printf '\033[1;32m[ok]\033[0m  %s\n' "$*"; }
warnung() { printf '\033[1;33m[WARNUNG]\033[0m %s\n' "$*" >&2; }
fehler()  { printf '\033[1;31m[FEHLER]\033[0m %s\n' "$*" >&2; }
abbruch() { fehler "$*"; exit 1; }

# .env-Datei sicher einlesen (KEY=WERT, ohne Shell-Auswertung). Bereits gesetzte Variablen haben Vorrang.
env_laden() {
    local datei="$1" zeile schluessel wert
    [[ -f "$datei" ]] || return 0
    while IFS= read -r zeile || [[ -n "$zeile" ]]; do
        zeile="${zeile%$'\r'}"
        [[ "$zeile" =~ ^[[:space:]]*# || "$zeile" != *=* ]] && continue
        schluessel="${zeile%%=*}"
        schluessel="${schluessel//[[:space:]]/}"
        wert="${zeile#*=}"
        [[ "$schluessel" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        # umschließende Anführungszeichen entfernen
        if [[ "$wert" =~ ^\"(.*)\"$ || "$wert" =~ ^\'(.*)\'$ ]]; then wert="${BASH_REMATCH[1]}"; fi
        if [[ -z "${!schluessel+x}" ]]; then
            export "$schluessel=$wert"
        fi
    done < "$datei"
}

# libpq-Variablen (PGHOST …) aus den POSTGRES_*-Einstellungen der Anwendung ableiten
pg_umgebung() {
    export PGHOST="${PGHOST:-${POSTGRES_HOST:-localhost}}"
    export PGPORT="${PGPORT:-${POSTGRES_PORT:-5432}}"
    export PGUSER="${PGUSER:-${POSTGRES_USER:-erp}}"
    export PGDATABASE="${PGDATABASE:-${POSTGRES_DB:-erp}}"
    if [[ -z "${PGPASSWORD:-}" && -n "${POSTGRES_PASSWORD:-}" ]]; then
        export PGPASSWORD="$POSTGRES_PASSWORD"
    fi
}

# Neueste Sicherung (Verzeichnis) unterhalb des Backup-Verzeichnisses
neueste_sicherung() {
    local basis="$1"
    find "$basis" -mindepth 2 -maxdepth 2 -type d -name '20*' -printf '%f\t%p\n' 2>/dev/null \
        | sort -r | head -n1 | cut -f2
}

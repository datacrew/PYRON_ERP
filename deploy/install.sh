#!/usr/bin/env bash
# =====================================================================================
# ERP Mittelstand – native Installation auf Ubuntu 22.04 / 24.04 LTS (ohne Docker)
#
# Aufruf (als root, im entpackten Release-Verzeichnis):
#   sudo deploy/install.sh --domain erp.ihre-firma.de --email it@ihre-firma.de
#
# Schalter:
#   --domain NAME          öffentliche Domain (Pflicht, außer mit --ohne-caddy)
#   --email ADRESSE        Kontaktadresse für Let's Encrypt (Pflicht, außer mit --ohne-caddy)
#   --demo                 zusätzlich Demodaten laden (NUR Test-/Schulungssystem – bekannte Passwörter!)
#   --trocken              Trockenlauf: zeigt nur an, was getan würde, verändert nichts
#   --basis DIR            Installationsverzeichnis (Standard /opt/erp)
#   --backup-verzeichnis   Ablage der Sicherungen (Standard /var/backups/erp)
#   --backup-uhrzeit HH:MM Uhrzeit der täglichen Sicherung (Standard 02:30)
#   --ohne-caddy           keinen Reverse Proxy einrichten (eigener Proxy vorhanden)
#
# Das Skript ist idempotent: erneutes Ausführen ergänzt Fehlendes, überschreibt aber weder die .env
# (Geheimnisse!) noch die Datenbank. Ergebnis:
#   /opt/erp/app        Programmcode (.env mit Geheimnissen, Rechte 640 root:erp)
#   /opt/erp/venv       Python-Umgebung
#   /opt/erp/staticfiles  Static-Dateien (von Caddy ausgeliefert)
#   /var/lib/erp/media  Belegarchiv (GoBD-PDFs)
#   /var/log/erp        Protokolle
#   systemd: erp.service (Gunicorn 127.0.0.1:8000), erp-backup.timer (tägliche Sicherung),
#            erp-dms-eingang.timer (DMS-Eingangsordner /var/lib/erp/eingang alle 5 Minuten)
# =====================================================================================
set -euo pipefail

SKRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
QUELLE="$(cd "$SKRIPT_DIR/.." && pwd)"
# shellcheck source=SCRIPTDIR/scripts/gemeinsam.sh
source "$SKRIPT_DIR/scripts/gemeinsam.sh"

DOMAIN=""; EMAIL=""; DEMO=0; TROCKEN=0; CADDY=1
BASIS="/opt/erp"; BACKUP_DIR="/var/backups/erp"; BACKUP_UHRZEIT="02:30"
PG_VERSION=17

while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain) DOMAIN="${2:?--domain braucht einen Wert}"; shift ;;
        --email) EMAIL="${2:?--email braucht einen Wert}"; shift ;;
        --demo) DEMO=1 ;;
        --trocken) TROCKEN=1 ;;
        --basis) BASIS="${2:?--basis braucht ein Verzeichnis}"; shift ;;
        --backup-verzeichnis) BACKUP_DIR="${2:?}"; shift ;;
        --backup-uhrzeit) BACKUP_UHRZEIT="${2:?}"; shift ;;
        --ohne-caddy) CADDY=0 ;;
        -h|--hilfe|--help) sed -n '2,28p' "$0"; exit 0 ;;
        *) abbruch "Unbekannter Schalter: $1 (siehe --hilfe)" ;;
    esac
    shift
done

APP="$BASIS/app"; VENV="$BASIS/venv"; STATIC="$BASIS/staticfiles"
MEDIA="/var/lib/erp/media"; EINGANG="/var/lib/erp/eingang"; LOGDIR="/var/log/erp"; ENV_DATEI="$APP/.env"

# ------------------------------------------------------------------ Hilfsfunktionen (Trockenlauf-fähig)
schritt() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
# Befehl ausführen bzw. im Trockenlauf nur anzeigen
tu() {
    if [[ "$TROCKEN" == "1" ]]; then printf '    [trocken] %s\n' "$*"; else "$@"; fi
}
# Shell-Ausdruck (mit Pipes/Umleitungen) ausführen bzw. anzeigen
tu_sh() {
    if [[ "$TROCKEN" == "1" ]]; then printf '    [trocken] %s\n' "$1"; else bash -c "$1"; fi
}
# Datei mit Inhalt (stdin) schreiben; Rechte und Besitzer setzen
datei_schreiben() {
    local ziel="$1" modus="$2" besitzer="$3" inhalt
    inhalt="$(cat)"
    if [[ "$TROCKEN" == "1" ]]; then
        printf '    [trocken] Datei %s (%s, %s) schreiben:\n' "$ziel" "$modus" "$besitzer"
        # Geheimnisse im Trockenlauf nicht anzeigen
        printf '%s\n' "$inhalt" | sed -E 's/^(DJANGO_SECRET_KEY|POSTGRES_PASSWORD)=.*/\1=********/; s/^/              | /'
        return 0
    fi
    install -d "$(dirname "$ziel")"
    printf '%s\n' "$inhalt" > "$ziel"
    chmod "$modus" "$ziel"
    chown "$besitzer" "$ziel"
}
als_erp() { tu sudo -u erp -H "$@"; }
zufall() { python3 -c "import secrets,sys; print(secrets.token_urlsafe(int(sys.argv[1])))" "$1"; }

# ------------------------------------------------------------------ Vorprüfungen
schritt "Vorprüfungen"
[[ "$TROCKEN" == "1" ]] && warnung "TROCKENLAUF – es wird nichts verändert, nur angezeigt."
if [[ "$(id -u)" != "0" ]]; then
    if [[ "$TROCKEN" == "1" ]]; then
        warnung "Nicht als root gestartet – im Trockenlauf erlaubt."
    else
        abbruch "Bitte als root ausführen: sudo $0 …"
    fi
fi
if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    if [[ "${ID:-}" != "ubuntu" || ! "${VERSION_ID:-}" =~ ^(22\.04|24\.04)$ ]]; then
        warnung "Getestet für Ubuntu 22.04/24.04 – gefunden: ${PRETTY_NAME:-unbekannt}. Fortsetzung auf eigenes Risiko."
    else
        ok "System: $PRETTY_NAME"
    fi
fi
CODENAME="${VERSION_CODENAME:-$(lsb_release -cs 2>/dev/null || echo jammy)}"
[[ -f "$QUELLE/manage.py" ]] || abbruch "Projektverzeichnis nicht gefunden ($QUELLE/manage.py fehlt)."
if [[ "$CADDY" == "1" ]]; then
    [[ -n "$DOMAIN" ]] || abbruch "--domain fehlt (oder --ohne-caddy angeben)."
    [[ -n "$EMAIL" ]] || abbruch "--email fehlt (für Let's Encrypt)."
fi
[[ "$BACKUP_UHRZEIT" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || abbruch "--backup-uhrzeit im Format HH:MM angeben."
if [[ "$DEMO" == "1" ]]; then
    warnung "--demo: Demodaten mit BEKANNTEN Passwörtern werden geladen – niemals im Produktivsystem!"
fi
ok "Quelle: $QUELLE  →  Ziel: $BASIS"

# ------------------------------------------------------------------ Pakete
schritt "Systempakete installieren"
PAKETE=(ca-certificates curl gnupg lsb-release rsync python3 python3-venv python3-dev build-essential libpq-dev
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b fonts-dejavu fonts-liberation tzdata
        tesseract-ocr tesseract-ocr-deu ghostscript poppler-utils unpaper pngquant libzbar0)
# (DMS: Tesseract = OCR deutsch, Ghostscript = PDF/A, zbar = Barcode-Trennblätter)
# ab Ubuntu 24.04 vorhanden (WeasyPrint nutzt es zur Schrift-Teilmengenbildung, optional)
if apt-cache show libharfbuzz-subset0 >/dev/null 2>&1; then PAKETE+=(libharfbuzz-subset0); fi
tu_sh "DEBIAN_FRONTEND=noninteractive apt-get update -q"
tu_sh "DEBIAN_FRONTEND=noninteractive apt-get install -y -q ${PAKETE[*]}"

schritt "PostgreSQL $PG_VERSION (offizielles PGDG-Repository)"
PGDG_LISTE=/etc/apt/sources.list.d/pgdg.list
if [[ -f "$PGDG_LISTE" ]]; then
    ok "PGDG-Repository bereits eingerichtet"
else
    tu install -d /usr/share/postgresql-common/pgdg
    tu curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc
    tu_sh "echo 'deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${CODENAME}-pgdg main' > $PGDG_LISTE"
    tu_sh "apt-get update -q"
fi
if dpkg -s "postgresql-$PG_VERSION" >/dev/null 2>&1; then
    ok "postgresql-$PG_VERSION bereits installiert"
else
    tu_sh "DEBIAN_FRONTEND=noninteractive apt-get install -y -q postgresql-$PG_VERSION postgresql-client-$PG_VERSION"
fi
tu systemctl enable --now postgresql

if [[ "$CADDY" == "1" ]]; then
    schritt "Caddy (offizielles Repository)"
    if dpkg -s caddy >/dev/null 2>&1; then
        ok "Caddy bereits installiert"
    else
        tu_sh "curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg"
        tu_sh "curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list"
        tu_sh "apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q caddy"
    fi
fi

# ------------------------------------------------------------------ Benutzer und Verzeichnisse
schritt "Systembenutzer und Verzeichnisse"
if id erp >/dev/null 2>&1; then
    ok "Systembenutzer erp vorhanden"
else
    tu useradd --system --home-dir "$BASIS" --shell /usr/sbin/nologin --user-group erp
fi
tu install -d -m 755 -o root -g root "$BASIS"
tu install -d -m 755 -o erp -g erp "$STATIC"
tu install -d -m 750 -o erp -g erp /var/lib/erp "$MEDIA" "$LOGDIR" "$BACKUP_DIR"
# DMS-Eingangsordner (Hotfolder): Gruppe erp darf schreiben (z. B. Scanner-/Samba-Benutzer in Gruppe erp)
tu install -d -m 2770 -o erp -g erp "$EINGANG"

schritt "Programmcode nach $APP"
if [[ "$(readlink -f "$QUELLE")" == "$(readlink -f "$APP" 2>/dev/null || echo -)" ]]; then
    ok "Quelle ist bereits das Installationsverzeichnis"
else
    tu rsync -a --delete --exclude '.env' --exclude 'media/' --exclude 'staticfiles/' --exclude 'venv/' \
        --exclude '.venv/' --exclude '.git/' --exclude '__pycache__/' --exclude 'deploy/backups/' \
        "$QUELLE/" "$APP/"
fi
tu chown -R root:erp "$APP"
tu chmod -R g+rX,o-rwx "$APP"
tu chmod 755 "$APP/deploy/scripts/backup.sh" "$APP/deploy/scripts/restore.sh" "$APP/deploy/scripts/update.sh" \
    "$APP/deploy/scripts/erstbenutzer.sh" "$APP/deploy/scripts/gesundheit.sh"

# ------------------------------------------------------------------ Datenbank
schritt "Datenbank und Datenbankbenutzer"
DB_PASSWORT=""
if [[ -f "$ENV_DATEI" ]]; then
    DB_PASSWORT="$(sed -n 's/^POSTGRES_PASSWORD=//p' "$ENV_DATEI" | head -n1)"
fi
rolle_da=$(sudo -u postgres psql -XAtc "SELECT 1 FROM pg_roles WHERE rolname = 'erp'" 2>/dev/null || true)
if [[ "$rolle_da" == "1" && -n "$DB_PASSWORT" ]]; then
    ok "Datenbankbenutzer erp vorhanden, Passwort aus vorhandener .env"
else
    DB_PASSWORT="$(zufall 32)"
    if [[ "$rolle_da" == "1" ]]; then
        warnung "Datenbankbenutzer erp vorhanden, aber keine .env – Passwort wird neu gesetzt."
        tu_sh "sudo -u postgres psql -Xq -v ON_ERROR_STOP=1 -v pw='***' -c \"ALTER ROLE erp WITH LOGIN CREATEDB PASSWORD :'pw'\""
        [[ "$TROCKEN" == "1" ]] || printf "ALTER ROLE erp WITH LOGIN CREATEDB PASSWORD '%s';\n" "$DB_PASSWORT" | sudo -u postgres psql -Xq -v ON_ERROR_STOP=1
    else
        # CREATEDB: wird von restore.sh für Wiederherstellung und Restore-Proben benötigt
        tu_sh "sudo -u postgres psql -c \"CREATE ROLE erp LOGIN CREATEDB PASSWORD '***'\""
        [[ "$TROCKEN" == "1" ]] || printf "CREATE ROLE erp WITH LOGIN CREATEDB PASSWORD '%s';\n" "$DB_PASSWORT" | sudo -u postgres psql -Xq -v ON_ERROR_STOP=1
    fi
fi
db_da=$(sudo -u postgres psql -XAtc "SELECT 1 FROM pg_database WHERE datname = 'erp'" 2>/dev/null || true)
if [[ "$db_da" == "1" ]]; then
    ok "Datenbank erp vorhanden (bleibt unverändert)"
else
    tu sudo -u postgres createdb -O erp -E UTF8 -T template0 erp
fi

# ------------------------------------------------------------------ .env
schritt "Konfiguration $ENV_DATEI"
if [[ -f "$ENV_DATEI" ]]; then
    ok ".env vorhanden – wird nicht überschrieben"
else
    SECRET="$(zufall 50)"
    if [[ -n "$DOMAIN" ]]; then HOSTS="$DOMAIN"; ORIGINS="https://$DOMAIN"; else HOSTS="localhost"; ORIGINS=""; fi
    datei_schreiben "$ENV_DATEI" 640 root:erp <<EOF
# ERP Mittelstand – erzeugt von install.sh am $(date '+%d.%m.%Y %H:%M'). Enthält Geheimnisse!
# Alle Einstellungen sind in deploy/.env.example erläutert.
DOMAIN=${DOMAIN}
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=${SECRET}
DJANGO_ALLOWED_HOSTS=${HOSTS}
DJANGO_CSRF_TRUSTED_ORIGINS=${ORIGINS}
ERP_HTTPS=$([[ "$CADDY" == "1" ]] && echo 1 || echo 0)
DJANGO_HSTS_SEKUNDEN=0
POSTGRES_DB=erp
POSTGRES_USER=erp
POSTGRES_PASSWORD=${DB_PASSWORT}
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
ERP_STATIC_ROOT=${STATIC}
ERP_MEDIA_ROOT=${MEDIA}
ERP_DMS_EINGANG=${EINGANG}
ERP_LOG_DATEI=${LOGDIR}/erp.log
ERP_LOG_LEVEL=INFO
ERP_MINDESTLOHN=13.90
ERP_STANDARD_LOHNSATZ=18.00
BACKUP_VERZEICHNIS=${BACKUP_DIR}
BACKUP_TAEGLICH=7
BACKUP_WOECHENTLICH=4
BACKUP_MONATLICH=12
BACKUP_RCLONE_ZIEL=
BACKUP_RSYNC_ZIEL=
EOF
fi

# ------------------------------------------------------------------ Python-Umgebung
schritt "Python-Umgebung $VENV"
if [[ -x "$VENV/bin/python" ]]; then ok "venv vorhanden"; else tu python3 -m venv "$VENV"; fi
tu "$VENV/bin/pip" install -q --upgrade pip wheel
tu "$VENV/bin/pip" install -q -r "$APP/requirements.txt"

# ------------------------------------------------------------------ Django
schritt "Migrationen, Grunddaten, Static-Dateien"
if [[ "$TROCKEN" == "1" ]]; then
    printf '    [trocken] cd %s\n' "$APP"
else
    cd "$APP"
fi
als_erp "$VENV/bin/python" manage.py migrate --noinput
als_erp "$VENV/bin/python" manage.py erp_grunddaten
if [[ "$DEMO" == "1" ]]; then
    als_erp "$VENV/bin/python" manage.py erp_demodaten
fi
als_erp "$VENV/bin/python" manage.py collectstatic --noinput -v0
tu chmod -R a+rX "$STATIC"
als_erp "$VENV/bin/python" manage.py check --deploy --fail-level ERROR

# ------------------------------------------------------------------ systemd
schritt "systemd-Dienste"
for einheit in erp.service erp-backup.service erp-backup.timer erp-dms-eingang.service erp-dms-eingang.timer; do
    sed -e "s|__BASIS__|$BASIS|g" -e "s|__UHRZEIT__|$BACKUP_UHRZEIT|g" "$QUELLE/deploy/systemd/$einheit" \
        | datei_schreiben "/etc/systemd/system/$einheit" 644 root:root
done
tu systemctl daemon-reload
tu systemctl enable erp.service erp-backup.timer erp-dms-eingang.timer
tu systemctl restart erp.service
tu systemctl start erp-backup.timer erp-dms-eingang.timer

# ------------------------------------------------------------------ Caddy
if [[ "$CADDY" == "1" ]]; then
    schritt "Caddy mit HTTPS für $DOMAIN"
    sed -e "s|__DOMAIN__|$DOMAIN|g" -e "s|__EMAIL__|$EMAIL|g" -e "s|__STATIC__|$STATIC|g" \
        "$QUELLE/deploy/caddy/Caddyfile.nativ" | datei_schreiben /etc/caddy/Caddyfile 644 root:root
    tu install -d -o caddy -g caddy /var/log/caddy
    tu caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
    tu systemctl enable caddy
    tu systemctl reload-or-restart caddy
fi

# ------------------------------------------------------------------ Firewall (nur Hinweis/Freigabe)
schritt "Firewall"
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    tu ufw allow 80/tcp
    tu ufw allow 443/tcp
    tu ufw allow 443/udp
else
    warnung "ufw ist nicht aktiv. Empfehlung: ufw allow OpenSSH && ufw allow 80,443/tcp && ufw enable"
fi

# ------------------------------------------------------------------ Abschluss
schritt "Abschlussprüfung"
if [[ "$TROCKEN" == "1" ]]; then
    printf '    [trocken] curl http://127.0.0.1:8000/gesundheit/\n'
else
    gesund=0
    for _ in $(seq 1 30); do
        if curl -fsS -m 5 http://127.0.0.1:8000/gesundheit/ >/dev/null 2>&1; then gesund=1; break; fi
        sleep 2
    done
    if [[ "$gesund" == "1" ]]; then
        ok "Anwendung läuft (Gunicorn 127.0.0.1:8000)"
    else
        abbruch "Anwendung antwortet nicht – journalctl -u erp -n 100"
    fi
fi

echo
ok "Installation abgeschlossen$([[ "$TROCKEN" == "1" ]] && echo ' (Trockenlauf – nichts verändert)')."
cat <<EOF

Nächste Schritte:
  1. Ersten Administrator anlegen:   sudo $APP/deploy/scripts/erstbenutzer.sh <benutzername> <e-mail>
  2. Anmelden:                       https://${DOMAIN:-<ihre-domain>}/anmelden/
  3. Erste Sicherung + Prüfung:      sudo systemctl start erp-backup.service && sudo $APP/deploy/scripts/gesundheit.sh
  4. Einstellungen prüfen:           $ENV_DATEI (Mindestlohn, GLN, DATEV-Nummern, externe Sicherung)
  Anleitung: $APP/deploy/INSTALLATION.md
EOF

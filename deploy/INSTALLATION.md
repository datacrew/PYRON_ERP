# Installation und Betrieb – ERP Mittelstand

Diese Anleitung beschreibt den Produktivbetrieb auf einem eigenen Linux-Server. Es gibt zwei Wege:

| | **A – Docker (empfohlen)** | **B – Nativ (ohne Docker)** |
|---|---|---|
| Inhalt | `deploy/docker-compose.prod.yml` | `deploy/install.sh` |
| Datenbank | PostgreSQL 17 im Container, Volume `pgdata` | PostgreSQL 17 aus dem PGDG-Repository |
| App-Server | Gunicorn im Container | Gunicorn als systemd-Dienst `erp` |
| HTTPS | Caddy-Container (Let's Encrypt) | Caddy-Paket (Let's Encrypt) |
| Sicherung | Container `backup` (täglich) | systemd-Timer `erp-backup.timer` |
| Updates | `update.sh --docker` | `update.sh` |

> **Stand der Prüfung:** Docker stand in der Entwicklungsumgebung nicht zur Verfügung. Compose-Dateien und
> `install.sh` wurden statisch geprüft (YAML-Parser, `bash -n`, shellcheck, Trockenlauf `--trocken`), aber **nicht
> auf einem frischen Server ausgeführt**. Real getestet wurden: Gunicorn mit Produktionseinstellungen gegen
> PostgreSQL 17, `/gesundheit/`, `backup.sh` und `restore.sh` inkl. Integritätsprüfung der Hash-Ketten.
> Bitte die Erstinstallation zunächst auf einem Testserver durchspielen.

---

## 1. Voraussetzungen

### Hardware (Empfehlung)

| Nutzung | CPU | RAM | Speicher |
|---|---|---|---|
| bis 15 gleichzeitige Benutzer | 2 vCPU | 4 GB | 60 GB SSD |
| bis 50 gleichzeitige Benutzer | 4 vCPU | 8 GB | 120 GB SSD |

Den Speicherbedarf bestimmen vor allem das Belegarchiv (PDFs, 10 Jahre Aufbewahrung) und die lokalen
Sicherungen (7 täglich + 4 wöchentlich + 12 monatlich). Planen Sie für Sicherungen mindestens das 5-Fache der
Datenbank- plus Archivgröße ein – oder einen eigenen Datenträger.

### Software und Netzwerk

- **Ubuntu Server 22.04 oder 24.04 LTS** (andere Debian-Derivate möglich, nicht getestet), Root-Zugang per SSH.
- **Domain** (z. B. `erp.ihre-firma.de`) mit DNS-A-Eintrag (und ggf. AAAA) auf die öffentliche IP des Servers.
  Ohne korrekten DNS-Eintrag kann kein Zertifikat ausgestellt werden.
- **Offene Ports:** 80/tcp (Let's-Encrypt-Prüfung, Weiterleitung) und 443/tcp (+ 443/udp für HTTP/3), sowie SSH.
  Die Datenbank wird **nicht** nach außen geöffnet.
- **Firewall (ufw)** – vor dem Aktivieren unbedingt SSH freigeben, sonst sperren Sie sich aus:

  ```bash
  sudo ufw allow OpenSSH
  sudo ufw allow 80/tcp
  sudo ufw allow 443/tcp
  sudo ufw allow 443/udp
  sudo ufw enable
  sudo ufw status
  ```

  Hinweis Docker: Docker veröffentlicht Ports an ufw vorbei. Im Compose-Setup sind bewusst **nur** 80/443
  (Caddy) veröffentlicht; PostgreSQL und Gunicorn sind nur im internen Docker-Netz erreichbar.

- Ausgehend: Internetzugang für Paketquellen, Let's Encrypt und (optional) externes Sicherungsziel.
  Die Weboberfläche lädt Bootstrap und Chart.js von einem CDN (jsdelivr) – Clients brauchen Internetzugang.

### Release entpacken

```bash
sudo mkdir -p /opt/erp-release && cd /opt/erp-release
sudo tar -xzf ~/erp_system_release.tar.gz      # erzeugt ./erp_system/
cd erp_system
```

(Alternativ per `git clone` aus Ihrem Repository.)

---

## 2. Weg A – Docker Compose (empfohlen)

### 2.1 Docker installieren

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable --now docker
docker compose version          # Compose v2 muss vorhanden sein
```

### 2.2 Konfiguration

```bash
cd erp_system/deploy
cp .env.example .env
chmod 600 .env
nano .env
```

Pflichtwerte (alle mit `<…>` markierten Platzhalter ersetzen):

- `DOMAIN`, `ACME_EMAIL`
- `DJANGO_SECRET_KEY` – erzeugen mit `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`
- `DJANGO_ALLOWED_HOSTS=erp.ihre-firma.de`, `DJANGO_CSRF_TRUSTED_ORIGINS=https://erp.ihre-firma.de`
- `POSTGRES_PASSWORD` – erzeugen mit `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`
- `ERP_DEMODATEN=0` (Standard – im Produktivsystem nie ändern)
- fachlich: `ERP_MINDESTLOHN`, `ERP_EIGENE_GLN`, `ERP_DATEV_BERATERNUMMER`, `ERP_DATEV_MANDANTENNUMMER`

Jede Einstellung ist in `.env.example` auf Deutsch erläutert.

### 2.3 Starten

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps        # alle Dienste „running“, app „healthy“
docker compose -f docker-compose.prod.yml logs -f app caddy
```

Beim Start des `app`-Containers laufen automatisch: Warten auf PostgreSQL → `migrate` → `collectstatic` →
`erp_grunddaten` (Module, Rollen, Kontenrahmen, Nummernkreise – idempotent). Caddy bezieht das Zertifikat
beim ersten Aufruf von `https://DOMAIN` (dauert einige Sekunden).

### 2.4 Ersten Administrator anlegen

```bash
./scripts/erstbenutzer.sh --docker mmueller m.mueller@ihre-firma.de
# Passwort wird abgefragt
```

Dann anmelden unter `https://erp.ihre-firma.de/anmelden/` – weiter mit [Abschnitt 4](#4-erste-anmeldung-und-einrichtung).

### 2.5 Nützliche Befehle

```bash
cd erp_system/deploy
C="docker compose -f docker-compose.prod.yml"
$C exec app python manage.py erp_pruefen          # GoBD-Trigger + Hash-Ketten prüfen
$C exec backup /skripte/backup.sh --manuell       # sofortige Sicherung
$C exec app python manage.py shell                # Django-Shell (Vorsicht!)
$C restart app                                    # Neustart der Anwendung
$C down                                           # stoppen (Volumes/Daten bleiben erhalten)
```

**Niemals** `docker compose down -v` ausführen – `-v` löscht die Volumes und damit Datenbank und Belegarchiv.

Volumes: `erp_pgdata` (Datenbank), `erp_media` (Belegarchiv), `erp_static`, `erp_caddy_data` (Zertifikate),
`erp_caddy_config`. Sicherungen landen auf dem Host unter `deploy/backups/` (anpassbar: `BACKUP_HOST_VERZEICHNIS`).

---

## 3. Weg B – Native Installation mit `install.sh`

### 3.1 Trockenlauf

Zeigt alle Schritte an, ohne etwas zu verändern (Geheimnisse werden maskiert):

```bash
sudo deploy/install.sh --trocken --domain erp.ihre-firma.de --email it@ihre-firma.de
```

### 3.2 Installation

```bash
sudo deploy/install.sh --domain erp.ihre-firma.de --email it@ihre-firma.de
```

Das Skript (idempotent – darf erneut ausgeführt werden):

1. installiert Systempakete inkl. WeasyPrint-Bibliotheken (Pango, HarfBuzz) und Schriften,
2. richtet das offizielle PostgreSQL-Repository (PGDG) ein und installiert **PostgreSQL 17**,
3. installiert **Caddy** aus dem offiziellen Repository,
4. legt den Systembenutzer `erp` und die Verzeichnisse an,
5. kopiert den Code nach `/opt/erp/app`,
6. legt DB-Benutzer `erp` (zufälliges Passwort, Recht CREATEDB für Wiederherstellungen) und Datenbank `erp` an,
7. erzeugt `/opt/erp/app/.env` mit zufälligem `DJANGO_SECRET_KEY` (Rechte 640, root:erp) – eine vorhandene
   `.env` wird **nie** überschrieben,
8. erstellt die Python-Umgebung `/opt/erp/venv`, führt `migrate`, `erp_grunddaten`, `collectstatic` und
   `check --deploy` aus,
9. richtet `erp.service` (Gunicorn auf 127.0.0.1:8000) und `erp-backup.timer` (täglich 02:30) ein,
10. schreibt `/etc/caddy/Caddyfile` (HTTPS für die Domain) und gibt bei aktiver ufw die Ports 80/443 frei.

Weitere Schalter: `--demo` (Demodaten – **nur** Test-/Schulungssystem), `--basis DIR`, `--backup-verzeichnis DIR`,
`--backup-uhrzeit HH:MM`, `--ohne-caddy` (eigener Reverse Proxy), `--hilfe`.

Ablage:

| Pfad | Inhalt |
|---|---|
| `/opt/erp/app` | Programmcode, `.env` |
| `/opt/erp/venv` | Python-Umgebung |
| `/opt/erp/staticfiles` | Static-Dateien (Caddy) |
| `/var/lib/erp/media` | Belegarchiv (GoBD-PDFs) |
| `/var/log/erp/erp.log` | Anwendungsprotokoll (rotierend) |
| `/var/backups/erp` | Sicherungen |

### 3.3 Ersten Administrator anlegen

```bash
sudo /opt/erp/app/deploy/scripts/erstbenutzer.sh mmueller m.mueller@ihre-firma.de
```

### 3.4 Dienste verwalten

```bash
sudo systemctl status erp                 # Anwendung
sudo journalctl -u erp -f                 # Protokoll (Gunicorn)
sudo systemctl restart erp
sudo systemctl list-timers erp-backup     # nächster Sicherungslauf
sudo systemctl start erp-backup.service   # Sicherung sofort
sudo -u erp /opt/erp/venv/bin/python /opt/erp/app/manage.py erp_pruefen   # (im Verzeichnis /opt/erp/app)
```

---

## 4. Erste Anmeldung und Einrichtung

1. Unter `https://DOMAIN/anmelden/` mit dem angelegten Administrator anmelden.
2. **Verwaltung → Firma** (`/verwaltung/firma/`): Firmendaten, Anschrift, USt-IdNr., Steuernummer, Bankverbindung prüfen
   (erscheinen auf allen Belegen; `erp_grunddaten` legt „Musterwerk GmbH“ als Platzhalter an).
3. **Verwaltung → Benutzer/Rollen:** persönliche Konten für alle Mitarbeiter anlegen und Rollen zuweisen.
   Keine Sammelkonten – das Audit-Log ordnet Änderungen Personen zu.
4. Nummernkreise, Kontenrahmen (SKR03/SKR04) und Belegvorlagen mit dem Steuerberater abstimmen,
   **bevor** der erste Beleg festgeschrieben wird (festgeschriebene Belege sind unveränderbar).
5. Erste Sicherung auslösen und `gesundheit.sh` ausführen (siehe Abschnitt 6 und 8).

### Keine Demodaten im Produktivsystem

Die Demodaten (`erp_demodaten`, `--demo`, `ERP_DEMODATEN=1`) enthalten Benutzer mit **öffentlich bekannten
Passwörtern** (`admin / Admin!2026`, übrige `Demo!2026`) und den bekannten Shop-Token
`shop_demo_musterwerk_2026`. Sie dürfen nur auf Test- oder Schulungssystemen geladen werden.
Wurden sie versehentlich geladen: System neu aufsetzen (leere Datenbank) – festgeschriebene Demo-Belege
lassen sich aus GoBD-Gründen nicht löschen. Mindestens aber sofort alle Demo-Passwörter ändern bzw. die
Benutzer deaktivieren und den Shop-Token neu erzeugen.

---

## 5. Sicherheitshinweise

- **`DJANGO_SECRET_KEY`** zufällig, mindestens 40 Zeichen, nur in der `.env`. Ohne gültigen Schlüssel startet die
  Anwendung im Produktivmodus nicht. Bei Verdacht auf Offenlegung neu erzeugen (alle Sitzungen werden beendet).
- **`DJANGO_DEBUG=0`** – sonst werden Fehlerdetails öffentlich angezeigt.
- **`.env`** nur für root/erp lesbar (`chmod 600` bzw. `640 root:erp`), nicht in Git, nicht per E-Mail.
- **HSTS** (`DJANGO_HSTS_SEKUNDEN`) erst setzen, wenn HTTPS stabil läuft – dann z. B. `31536000`.
  Ein gesetztes HSTS lässt sich bei Browsern nicht kurzfristig zurücknehmen.
- **Shop-Token:** für jede Shop-Anbindung einen eigenen, neuen Token erzeugen (**Vertrieb → Shops**, `/vertrieb/shops/`, Schaltfläche „Neues Token“; gespeichert wird nur der Hash);
  den Demo-Token nie verwenden. Token wie Passwörter behandeln und bei Personalwechsel/Verdacht erneuern.
- **API-Tokens** (Django-Admin → Tokens) nur für technische Benutzer mit minimalen Rechten.
- **Mindestlohn:** `ERP_MINDESTLOHN` (Stand 2026: 13,90 €/h; ab 01.01.2027 voraussichtlich 14,60 €/h) bei jeder
  gesetzlichen Änderung anpassen und die Anwendung neu starten – er ist Untergrenze der Akkord-/Zeitlohngarantie.
- Sicherheitsupdates des Betriebssystems einspielen (`sudo apt update && sudo apt upgrade`,
  ggf. `unattended-upgrades`), Docker-Images regelmäßig mit `update.sh --docker` neu bauen.
- SSH nur mit Schlüssel, Passwort-Login deaktivieren; Root-Login per SSH abschalten.
- Das Belegarchiv (`/media/`) wird von Caddy nie direkt ausgeliefert, sondern nur über rechtegeprüfte Ansichten.
- `python manage.py check --deploy` meldet ohne HSTS die Warnung `security.W004` (bewusst, s. o.) sowie
  Hinweise von drf-spectacular zur API-Dokumentation – beides ist unkritisch.

---

## 6. Datensicherung

### 6.1 Was wird gesichert?

`backup.sh` erzeugt je Lauf ein Verzeichnis `JJJJ-MM-TT_HHMMSS` mit:

| Datei | Inhalt |
|---|---|
| `db.dump` | vollständiger PostgreSQL-Dump (`pg_dump -Fc`): Schema, Daten, GoBD- und Lagerjournal-Trigger, Funktionen, Hash-Ketten |
| `media.tar.gz` | Belegarchiv (festgeschriebene PDFs) und übrige Uploads |
| `info.txt`, `inhalt.txt` | Zeitpunkt, Versionen, Inhaltsverzeichnis des Dumps |
| `SHA256SUMS` | Prüfsummen aller Dateien (werden bei der Wiederherstellung kontrolliert) |

Rotation: **7 tägliche**, **4 wöchentliche** (Sonntag), **12 monatliche** (1. des Monats), dazu bis zu 10
manuelle (`--manuell`, z. B. vor Updates). Wöchentliche/monatliche Stände sind Hardlinks und belegen keinen
zusätzlichen Platz. Werte über `BACKUP_TAEGLICH`, `BACKUP_WOECHENTLICH`, `BACKUP_MONATLICH`, `BACKUP_MANUELL`.

### 6.2 3-2-1-Regel

**3** Kopien der Daten, auf **2** verschiedenen Medien, davon **1** außer Haus. Die lokalen Sicherungen auf dem
Server reichen allein **nicht** (Brand, Diebstahl, Verschlüsselungstrojaner, Hardwaredefekt).

Externe Kopie einrichten (eine oder beide Varianten):

- **rclone** (S3, Hetzner Storage Box, Backblaze, SFTP, …): `rclone config` ausführen, Remote anlegen.
  - Docker: `rclone.conf` nach `deploy/rclone/rclone.conf` kopieren, `BACKUP_RCLONE_ZIEL=remote:erp-backups`.
  - Nativ: `sudo -u erp rclone config` (Paket `rclone` installieren), `BACKUP_RCLONE_ZIEL` in `/opt/erp/app/.env`.
- **rsync über SSH** (NAS, zweiter Server): `BACKUP_RSYNC_ZIEL=backup@nas:/pfad/`.
  - Docker: Schlüssel `id_ed25519` + `known_hosts` nach `deploy/ssh/` legen.
  - Nativ: Schlüssel unter `~erp/.ssh/` (Home: `/opt/erp`) ablegen.

Die externe Kopie verwendet `copy` (rclone) bzw. `rsync` **ohne** `--delete`: am Ziel wird nichts gelöscht.
Für unveränderbare Aufbewahrung am Ziel **Objektsperre/WORM** (z. B. S3 Object Lock im Compliance-Modus)
oder schreibgeschützte Snapshots verwenden und die Löschung dort per Lebenszyklusregel steuern.

### 6.3 Wiederherstellung

```bash
# nativ
sudo systemctl stop erp
sudo -u erp /opt/erp/app/deploy/scripts/restore.sh letzte                 # oder Pfad einer Sicherung
sudo systemctl start erp

# Docker (im Verzeichnis deploy/)
docker compose -f docker-compose.prod.yml stop app
docker compose -f docker-compose.prod.yml exec backup /skripte/restore.sh /backups/taeglich/2026-11-20_023000
docker compose -f docker-compose.prod.yml start app
docker compose -f docker-compose.prod.yml exec app python manage.py erp_pruefen
```

`restore.sh` prüft die SHA-256-Prüfsummen, verlangt die Eingabe `WIEDERHERSTELLEN` (oder `--ja`), legt die
Zieldatenbank neu an, spielt den Dump in **einer Transaktion** ein, kontrolliert die Trigger, stellt das
Belegarchiv wieder her (der bisherige Stand wird vorher als Archiv unter `manuell/` aufgehoben) und führt
`manage.py erp_pruefen` aus (nativ automatisch).

**GoBD und Wiederherstellung:**

- Es wird immer die **vollständige** Datenbank in eine **leere** Datenbank geladen. pg_restore legt die
  Schutz-Trigger nach dem Laden der Daten an – sie werden nicht umgangen und sind danach sofort aktiv.
- **Nie** `pg_restore --disable-triggers`, reine Daten-Restores (`--data-only`) oder Teil-Restores einzelner
  Tabellen verwenden – das würde Trigger bzw. Hash-Ketten aushebeln.
- Datenbank und Belegarchiv immer aus **derselben** Sicherung wiederherstellen: die PDF-Prüfsummen sind Teil
  der Kette. `erp_pruefen` meldet fehlende oder veränderte PDFs.
- Eine Wiederherstellung ist ein dokumentationspflichtiger Vorgang (Verfahrensdokumentation: Anlass, Zeitpunkt,
  verwendete Sicherung, Ergebnis der Prüfung). Nach einem Restore fehlen alle Buchungen seit der Sicherung –
  diese müssen aus den Originalunterlagen neu erfasst werden.

### 6.4 Regelmäßige Restore-Probe (mindestens quartalsweise)

Eine Sicherung ist erst dann eine Sicherung, wenn die Wiederherstellung geprobt wurde. Die Probe läuft in eine
separate Datenbank und verändert das Produktivsystem nicht:

```bash
# nativ
sudo -u erp /opt/erp/app/deploy/scripts/restore.sh --ja --ziel-db erp_restore_test \
     --media-ziel /var/lib/erp/restore_test letzte
sudo -u erp dropdb erp_restore_test && sudo rm -rf /var/lib/erp/restore_test

# Docker (Datenbankprobe; Media-Probe in ein temporäres Verzeichnis im Container)
docker compose -f docker-compose.prod.yml exec backup /skripte/restore.sh --ja \
     --ziel-db erp_restore_test --media-ziel /tmp/restore_test letzte
docker compose -f docker-compose.prod.yml exec backup dropdb erp_restore_test
```

Erwartetes Ergebnis: „Prüfsummen in Ordnung“, gleiche Tabellenanzahl, alle Trigger aktiv,
„Integritätsprüfung bestanden“. Beim Docker-Weg die Hash-Kette zusätzlich prüfen mit
`docker compose … exec -e POSTGRES_DB=erp_restore_test app python manage.py erp_pruefen --ohne-pdf`.
Ergebnis mit Datum protokollieren.

---

## 7. Updates

```bash
# nativ – mit neuem Release-Archiv
sudo /opt/erp/app/deploy/scripts/update.sh --paket ~/erp_system_release.tar.gz
# nativ – aus Git (wenn /opt/erp/app ein Git-Repository ist)
sudo /opt/erp/app/deploy/scripts/update.sh

# Docker (im entpackten/geklonten Projekt)
cd erp_system && git pull                 # oder: neues Archiv darüber entpacken, deploy/.env bleibt
deploy/scripts/update.sh --docker         # bzw. --docker --paket ~/erp_system_release.tar.gz
```

Ablauf: Sicherung (`manuell/`) → Code aktualisieren (`.env`, Belegarchiv, venv bleiben unangetastet) →
Abhängigkeiten → Migrationen, `collectstatic`, `erp_grunddaten` → Neustart → Gesundheits- und Integritätsprüfung.
Vor größeren Updates die Release-Hinweise lesen und das Update zuerst auf einem Testsystem (Kopie per
Restore-Probe) durchspielen.

PostgreSQL-Hauptversion (z. B. 17 → 18) **nicht** über `update.sh` wechseln: Sicherung erstellen, neue Instanz
aufsetzen, Sicherung mit `restore.sh` einspielen, prüfen.

---

## 8. Monitoring

- **Health-Endpoint:** `https://DOMAIN/gesundheit/` (ohne Anmeldung) liefert
  `{"status": "ok", "datenbank": "ok", "zeit": "…"}` mit HTTP 200, bei Datenbankproblemen HTTP 503.
  Keine sensiblen Angaben. Geeignet für Uptime-Kuma, Uptime Robot, Zabbix usw.
- **`deploy/scripts/gesundheit.sh`** prüft Anwendung, Datenbank, Speicherplatz (Warnung ab 85 %, Fehler ab 95 %)
  und das Alter der letzten Sicherung (Fehler ab 26 h). Exit-Code 0/1/2 (Nagios/Icinga/Checkmk-kompatibel).
  Beispiel-Cronjob mit E-Mail bei Problemen (benötigt eingerichteten Mailversand):

  ```cron
  */15 * * * * root /opt/erp/app/deploy/scripts/gesundheit.sh >/tmp/erp-gesundheit.txt 2>&1 || mail -s "ERP: Problem" it@ihre-firma.de </tmp/erp-gesundheit.txt
  ```

  Docker: `deploy/scripts/gesundheit.sh` im Verzeichnis `deploy/` aufrufen – es prüft `https://DOMAIN/gesundheit/`
  und `deploy/backups/`.
- **Protokolle:** nativ `journalctl -u erp`, `/var/log/erp/erp.log`, `/var/log/caddy/erp.log`;
  Docker `docker compose -f docker-compose.prod.yml logs app caddy backup` (rotiert, 5 × 20 MB je Dienst).
- **Zertifikat:** Caddy erneuert automatisch; Ablauf trotzdem extern überwachen.
- **Integrität:** monatlich `manage.py erp_pruefen` ausführen (GoBD-Trigger aktiv, Hash-Ketten unverändert).

---

## 9. GoBD-Hinweise zur Aufbewahrung

- **Aufbewahrungsfrist:** Buchungsbelege, Rechnungen, Buchungen und Jahresabschlussunterlagen **10 Jahre**
  (§ 147 AO, § 257 HGB; Handels-/Geschäftsbriefe 6 Jahre). Die Frist beginnt mit Ende des Kalenderjahres.
  Das System löscht keine festgeschriebenen Belege; Datenbank und Belegarchiv müssen über die gesamte Frist
  lesbar und auswertbar bleiben (auch nach einem Systemwechsel – Datenzugriff der Finanzverwaltung, Z1–Z3).
- **Unveränderbarkeit:** Festgeschriebene Belege und Fibu-Buchungen sind per Datenbank-Trigger und
  SHA-256-Hash-Kette geschützt, das Lagerjournal per Trigger unveränderlich. Diese Schutzmechanismen dürfen
  nicht deaktiviert werden (keine `ALTER TABLE … DISABLE TRIGGER`, kein `--disable-triggers`).
- **Sicherungen unveränderbar aufbewahren:** Für die 10-jährige Aufbewahrung zusätzlich zur rollierenden
  Sicherung z. B. einmal jährlich (nach dem Jahresabschluss) eine Sicherung auf ein WORM-Medium bzw. einen
  Speicher mit Objektsperre legen und **nicht** rotieren. Die monatlichen Sicherungen (12) decken nur ein Jahr ab.
- **Verfahrensdokumentation:** Installation, Rollen/Rechte, Sicherungskonzept, Restore-Proben und Updates
  dokumentieren; Änderungen an Einstellungen (z. B. Mindestlohn, Nummernkreise) nachvollziehbar festhalten.
- Diese Hinweise ersetzen keine steuerliche Beratung – Konzept mit dem Steuerberater/Wirtschaftsprüfer abstimmen.

---

## 10. Fehlerbehebung

| Problem | Ursache / Lösung |
|---|---|
| App startet nicht: „DJANGO_SECRET_KEY muss gesetzt sein“ | `.env` prüfen: Schlüssel mind. 40 Zeichen, `DJANGO_DEBUG=0`. |
| „DJANGO_ALLOWED_HOSTS muss die Domain(s) enthalten“ | Domain ohne `https://` eintragen; `*` ist im Produktivmodus nicht erlaubt. |
| HTTP 400 „Bad Request“ | Aufgerufener Hostname fehlt in `DJANGO_ALLOWED_HOSTS`. |
| CSRF-Fehler (403) beim Anmelden | `DJANGO_CSRF_TRUSTED_ORIGINS=https://DOMAIN` setzen (mit `https://`). |
| Endlose Weiterleitung / „zu viele Umleitungen“ | Reverse Proxy setzt `X-Forwarded-Proto` nicht. Caddy macht das automatisch; bei eigenem Proxy Header setzen oder `DJANGO_SSL_REDIRECT=0`. |
| Kein Zertifikat / Caddy-Fehler „challenge failed“ | DNS zeigt nicht auf den Server oder Port 80/443 gesperrt. `docker compose … logs caddy` bzw. `journalctl -u caddy`. Nach mehreren Fehlversuchen greift das Let's-Encrypt-Limit – 1 Stunde warten. |
| `app` bleibt „unhealthy“ | `docker compose … logs app` – meist DB-Zugang (`POSTGRES_PASSWORD` nach erstem Start geändert? Das Passwort wird nur beim ersten Anlegen des Volumes gesetzt; später per `ALTER ROLE` ändern). |
| PDF-Erzeugung schlägt fehl | WeasyPrint-Bibliotheken fehlen (nativ: `libpango-1.0-0 libpangoft2-1.0-0`), Schriften: `fonts-dejavu`. |
| Static-Dateien fehlen (Admin ohne Layout) | `collectstatic` erneut (nativ: `sudo -u erp /opt/erp/venv/bin/python manage.py collectstatic --noinput`), Rechte `/opt/erp/staticfiles` 755. |
| Sicherung fehlt laut `gesundheit.sh` | nativ `systemctl status erp-backup.service`, `journalctl -u erp-backup`; Docker `docker compose … logs backup`. Speicherplatz prüfen. |
| `restore.sh`: „Prüfsummen stimmen nicht“ | Sicherung beschädigt oder verändert – eine andere Sicherung wählen, Ursache klären. |
| `erp_pruefen`: „Archiv-PDF fehlt/verändert“ | Belegarchiv nicht aus derselben Sicherung wie die Datenbank wiederhergestellt – Media aus passender Sicherung einspielen. |
| GoBD-Fehlermeldung „Festgeschriebener Beleg … unveränderbar“ | Gewolltes Verhalten. Korrekturen nur per Storno/Gutschrift bzw. Gegenbuchung. |
| Timeout bei großen Berichten/PDF-Läufen | `GUNICORN_TIMEOUT` erhöhen (Standard 120 s). |
| DMS: Dokument bleibt im Status „Fehler: Tesseract ist nicht installiert" | Pakete aus Abschnitt 11.1 installieren, danach im Stapel „Erneut verarbeiten". |
| DMS: OCRmyPDF meldet Ghostscript-Regression | Ghostscript 10.0.0–10.02.0 ist bei `--skip-text` fehlerhaft; das ERP nutzt deshalb `--force-ocr` (nur für Seiten ohne Textlayer). Bei anderen Fehlern Ghostscript aktualisieren. |

Hilfe zur Diagnose: `python manage.py check --deploy`, `python manage.py erp_pruefen`,
`deploy/scripts/gesundheit.sh`.

---

## 11. Dokumentenmanagement (DMS) und OCR

### 11.1 Systempakete

Die Texterkennung nutzt Tesseract (Sprachpaket Deutsch) über OCRmyPDF; Ghostscript erzeugt das durchsuchbare
PDF/A, zbar liest Barcode-Trennblätter. Im Docker-Image und in `install.sh` sind die Pakete bereits enthalten.
Manuell (Ubuntu/Debian):

```bash
sudo apt-get install -y tesseract-ocr tesseract-ocr-deu ghostscript poppler-utils unpaper pngquant libzbar0
tesseract --list-langs   # muss „deu" enthalten
```

Die Python-Pakete (`ocrmypdf`, `pytesseract`, `pymupdf`, `pikepdf`, `pypdf`, `pyzbar`) stehen in
`requirements.txt`. Fehlt OCRmyPDF/Ghostscript, liest das System Scans hilfsweise nur mit pytesseract
(Volltext ja, aber kein PDF/A); fehlt Tesseract ganz, landen Scans im Status „Fehler". Digitale PDFs,
ZUGFeRD/Factur-X und XRechnung (XML) brauchen **kein** OCR.

### 11.2 Einstellungen (`.env` bzw. `config/settings.py`)

| Variable | Bedeutung | Standard |
|---|---|---|
| `ERP_DMS_OCR_SPRACHE` | Tesseract-Sprache(n), z. B. `deu+eng` | `deu` |
| `ERP_DMS_OCR_TIMEOUT` | Zeitlimit je Seite in Sekunden | `180` |

Dokumente werden unter `MEDIA_ROOT/dms/JJJJ/MM/` abgelegt und sind Teil der normalen Datensicherung
(Abschnitt 6). `python manage.py erp_pruefen` prüft zusätzlich die SHA-256-Siegel aller DMS-Dateien.

### 11.3 Eingangsordner (Hotfolder) und Stapelverarbeitung

Scanner oder Mail-Ablage legen Dateien in den Eingangsordner; `python manage.py dms_eingang` übernimmt sie in
einen Stapel, teilt Sammel-PDFs an Trennblättern und führt OCR/Belegerkennung aus – ohne Celery/Redis.

| Betrieb | Eingangsordner | Zeitplan |
|---|---|---|
| nativ (`install.sh`) | `/var/lib/erp/eingang` (Gruppe `erp` darf schreiben, setgid) | `erp-dms-eingang.timer` alle 5 Minuten |
| Docker | `deploy/eingang/` (`DMS_EINGANG_HOST_VERZEICHNIS`) | Dienst `dms-eingang` (Schleife, 5 Minuten) |

- Dateien im Hauptordner gelten als **Eingangsrechnung** (`--art` ändert die Vorgabe); Unterordner mit dem Namen
  einer Dokumentart (`vertrag/`, `lieferschein/`, `ausgangsrechnung/`, `sonstiges/`) legen die Art fest.
- Übernommene Dateien wandern nach `verarbeitet/JJJJ-MM-TT/`, abgelehnte nach `fehler/` (mit `.txt`-Begründung),
  bereits vorhandene (gleicher SHA-256) nach `doppelt/`. Dateien, die jünger als 10 s sind (Scanner schreibt noch),
  sowie `.part`/`.tmp` werden erst beim nächsten Lauf übernommen. Eine Sperrdatei verhindert parallele Läufe.
- `python manage.py dms_verarbeiten [--limit N]` arbeitet zusätzlich alle wartenden Dokumente ab
  (z. B. aus Uploads, die nicht sofort verarbeitet wurden).
- **Trennblatt:** Im DMS unter „Hochladen“ → „Trennblatt“ (PDF) ausdrucken und vor jedes Dokument eines
  Stapelscans legen. Erkannt wird der Barcode „PYRON-TRENNBLATT“ (zbar) bzw. das Wort „TRENNBLATT“ im Textlayer.
  Alternativ lassen sich Sammel-PDFs im Dokument über „Trennen“ manuell nach Seitenbereichen aufteilen.
- Uploads über die Oberfläche werden im Browser schrittweise verarbeitet (ein Dokument je Anfrage,
  Fortschrittsbalken) – auch das braucht keinen Hintergrunddienst.

Manuell testen (nativ):

```bash
sudo systemctl start erp-dms-eingang.service && journalctl -u erp-dms-eingang -n 20
systemctl list-timers erp-dms-eingang.timer
```

### 11.4 Aufbewahrung

Archivierte Dokumente sind per Datenbank-Trigger unveränderbar und nicht löschbar. Die Frist läuft bis zum
31.12. des Jahres „Dokumentdatum + N Jahre": Eingangs-/Ausgangsrechnungen 8 Jahre (Buchungsbelege, § 147 AO
in der Fassung ab 2025), Verträge 10 Jahre (vorsichtig gewählt), Lieferscheine/Sonstiges 6 Jahre.
Abweichende Fristen über `ERP_DMS_AUFBEWAHRUNG` in den Settings (z. B. `{"VERTRAG": 6}`) – bitte mit dem
Steuerberater abstimmen. Nach Fristende wird **nichts** automatisch gelöscht.

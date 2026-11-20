# ERP-System Mittelstand – Phase 1 (Fundament)

Modulares ERP für einen mittelständischen Industriebetrieb – **Django 5.2 · Django REST Framework · PostgreSQL ·
WeasyPrint**. Phase 1 liefert das Fundament: Mandant, Modul-Registry, Rechte, Nummernkreise, Audit-Log,
Events, Stammdaten, Belegwesen mit GoBD-Festschreibung und PDF-Erzeugung, deutsche Weboberfläche, REST-API.
Die Fachmodule (Einkauf, Produktion, Fibu …) sind als Gerüste registriert und folgen ab Phase 2
(siehe [docs/ROADMAP.md](docs/ROADMAP.md)).

---

## Schnellstart

### Variante A: lokal (Python 3.11+, PostgreSQL 14+)

```bash
# 1. Datenbank anlegen (einmalig, als PostgreSQL-Superuser)
sudo -u postgres psql -c "CREATE USER erp WITH PASSWORD 'erp' CREATEDB;"
sudo -u postgres psql -c "CREATE DATABASE erp OWNER erp;"

# 2. Abhängigkeiten
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # WeasyPrint braucht Pango (Debian/Ubuntu: apt install libpango-1.0-0 libpangoft2-1.0-0)

# 3. Konfiguration
cp .env.example .env                     # Werte anpassen (DB, SECRET_KEY, DEBUG …)

# 4. Datenbank + Daten
python3 manage.py migrate
python3 manage.py erp_grunddaten         # Module, Kontenrahmen, Steuersätze, Nummernkreise, Rollen, Vorlage
python3 manage.py erp_demodaten          # optional: Demo-Benutzer, Artikel, Partner, Stücklisten, Belege

# 5. Starten
python3 manage.py runserver 0.0.0.0:8000
```

Danach: <http://localhost:8000> (Web-UI) · <http://localhost:8000/admin/> (Django-Admin) ·
<http://localhost:8000/api/v1/> (REST-API).

Die Einstellungen werden aus Umgebungsvariablen gelesen (siehe `.env.example`):
`POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`, `DJANGO_SECRET_KEY`,
`DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `ERP_MEDIA_ROOT`, `ERP_LOG_LEVEL`.

### Variante B: Docker Compose

```bash
docker compose up --build
```

Startet PostgreSQL 16 und die Anwendung auf Port 8000. Der Entrypoint führt Migrationen, `collectstatic`
und `erp_grunddaten` aus; mit `ERP_DEMODATEN=1` (in `docker-compose.yml` voreingestellt) zusätzlich
`erp_demodaten`. Für den Produktivbetrieb `ERP_DEMODATEN` entfernen, `DJANGO_SECRET_KEY` setzen und
`DJANGO_DEBUG=0` verwenden.

> Hinweis: Die Compose-Konfiguration wurde in der Entwicklungsumgebung **nicht** ausgeführt (dort steht kein
> Docker zur Verfügung). Der lokale Weg (Variante A) ist getestet.

### Demo-Zugänge (nur nach `erp_demodaten`)

| Benutzer      | Passwort     | Rolle                                     |
|---------------|--------------|-------------------------------------------|
| `admin`       | `Admin!2026` | Superuser + Administrator                 |
| `einkauf`     | `Demo!2026`  | Einkauf                                   |
| `vertrieb`    | `Demo!2026`  | Vertrieb                                  |
| `produktion`  | `Demo!2026`  | Produktion                                |
| `lager`       | `Demo!2026`  | Lager                                     |
| `buchhaltung` | `Demo!2026`  | Buchhaltung (darf freigeben/festschreiben)|
| `gast`        | `Demo!2026`  | Nur Lesen                                 |

**Passwörter vor jedem echten Einsatz ändern** (Verwaltung → Benutzer oder Django-Admin).

### Tests

```bash
python3 manage.py test apps
```

Der DB-Benutzer braucht dafür das Recht `CREATEDB` (Test-Datenbank). Die Tests decken Rechte, Rechte-Matrix,
Nummernkreise (inkl. Rollback und parallele Vergabe), Audit-Log, Events, Beleg-Workflow, GoBD-Sperre im Code
und per DB-Trigger, Storno, PDF-Erzeugung, Weboberfläche, REST-API und Stücklistenauflösung ab.

---

## Architektur

**Modularer Monolith**: eine Django-Instanz, eine PostgreSQL-Datenbank, jedes Fachgebiet als eigene App unter
`apps/`. Module kommunizieren lose über Events, nicht über direkte Importe.

```
erp_system/
├── config/            Einstellungen, URLs, API-Router, WSGI/ASGI
├── apps/
│   ├── core/          Mandant, Modul-Registry, Rollen & Rechte, Nummernkreise, Audit-Log, Events, Verwaltung
│   ├── stammdaten/    Artikel, Geschäftspartner, Stücklisten, Arbeitspläne, Konten, Steuersätze
│   ├── belege/        Belegmodell, Workflow, GoBD-Kette, PDF-Erzeugung, Vorlagen
│   ├── einkauf/ wareneingang/ produktion/ vertrieb/ versand/ fibu/ reporting/   (Gerüste, Phase 2+)
├── templates/         Bootstrap-5-Oberfläche (CDN), PDF-Vorlage belege/pdf/standard.html
├── docker/            Entrypoint
├── docs/              DATENMODELL.md, ROADMAP.md
└── media/             PDF-Archiv (archiv/belege/<Jahr>/<belegart>/…)
```

### Modul-Registry
Jede App erklärt in ihrer `apps.py` ein Attribut `erp_modul` (Code, Name, Icon, Reihenfolge, Start-URL,
Pflichtmodul ja/nein, geplante Funktionen). Nach jedem `migrate` werden die Module in die Tabelle
`core_modul` synchronisiert. Unter **Verwaltung → Module** lassen sich Module an- und abschalten;
abgeschaltete Module verschwinden aus Menü und Dashboard und sind für alle Benutzer gesperrt
(auch für Superuser, auch in der API). Pflichtmodule (z. B. Verwaltung) können nicht abgeschaltet werden.

### Rechte
- **Rolle** × **Modul** × **Aktion** (`lesen`, `anlegen`, `aendern`, `freigeben`, `loeschen`).
  Ein Benutzer kann mehrere Rollen haben, die Rechte werden addiert.
- Pflege über **Verwaltung → Rollen** (Rechte-Matrix mit Checkboxen) oder im Django-Admin.
- Prüfung zentral in `apps/core/permissions.py`:
  - Views: Decorator `@recht_erforderlich("belege", "freigeben")` bzw. `ModulRechtMixin`
  - API: DRF-Permission `ModulRecht` (Methoden werden auf Aktionen abgebildet: GET → lesen, POST → anlegen,
    PUT/PATCH → aendern, DELETE → loeschen; Workflow-Aktionen → freigeben)
  - Templates: `{% hat_recht "stammdaten" "anlegen" as darf %}`
- Superuser haben alle Rechte in aktiven Modulen.
- Standardrollen (aus `erp_grunddaten`): Administrator, Einkauf, Vertrieb, Produktion, Lager, Buchhaltung,
  Nur Lesen.

### Nummernkreise
`apps/core/nummernkreise.py → naechste_nummer(code, datum)` vergibt Nummern **lückenlos**: Die Zeile des
Nummernkreises wird mit `SELECT … FOR UPDATE` gesperrt und die Nummer in derselben Transaktion verbraucht.
Schlägt die Transaktion fehl, wird auch die Nummer zurückgerollt. Jahresbezogene Kreise beginnen zum
Jahreswechsel wieder bei 1 (Format z. B. `RE-2026-00001`). Belegnummern werden erst bei der **Freigabe**
vergeben, Entwürfe haben keine Nummer – so entstehen keine Lücken durch verworfene Entwürfe.
Pflege unter **Verwaltung → Nummernkreise**.

### Audit-Log
Signal-Handler (`apps/core/signals.py`) protokollieren jedes Anlegen, Ändern (mit Alt-/Neu-Werten je Feld),
Löschen und jede M2M-Änderung aller ERP-Modelle sowie der Benutzer (Passwörter maskiert) in `core_auditlog`
– inklusive angemeldetem Benutzer (über `AktuellerBenutzerMiddleware`). Ansicht: **Verwaltung → Audit-Log**.

### Events (modulübergreifende Kommunikation)
```python
from apps.core.events import veroeffentlichen, abonnieren

@abonnieren("beleg.festgeschrieben")
def buchung_vorbereiten(beleg, benutzer, **kwargs): ...

veroeffentlichen("beleg.festgeschrieben", beleg=beleg, benutzer=user)
```
Bereits veröffentlicht: `beleg.freigegeben`, `beleg.festgeschrieben`, `beleg.storniert`.
Beispiel-Abonnent: `apps/fibu/ereignisse.py` (protokolliert festgeschriebene Belege – Ausgangspunkt für die
automatische Buchung in Phase 4).

### Belege, Workflow und GoBD
Einheitliches Belegmodell (Kopf + Positionen) für die Belegarten Angebot, Auftragsbestätigung, Lieferschein,
Rechnung, Gutschrift, Anfrage, Bestellung und Wareneingangsbeleg.

```
Entwurf ──freigeben──▶ Freigegeben ──festschreiben──▶ Festgeschrieben
                           │                               │
                           └────────stornieren────────────┴──▶ Storniert (mit Pflicht-Grund)
```

- **Entwurf**: frei bearbeitbar und löschbar. PDF-Vorschauen tragen bis zur Festschreibung das Wasserzeichen
  „ENTWURF“, stornierte Belege „STORNIERT“.
- **Freigeben** (Recht `belege.freigeben`): Belegnummer aus dem Nummernkreis, Partneradresse wird als
  Snapshot eingefroren, Inhalt gesperrt.
- **Festschreiben** (Recht `belege.freigeben`): SHA-256-Prüfsumme über alle Inhalte inkl. Prüfsumme des
  Vorgängers (**Hash-Kette**), PDF wird erzeugt, unter `media/archiv/belege/<Jahr>/<belegart>/` archiviert und mit eigener
  Prüfsumme gespeichert. Die Kettenreihenfolge wird über einen PostgreSQL-Advisory-Lock serialisiert.
- **Stornieren**: nur mit Begründung; der Beleg bleibt erhalten (kein Löschen).
- **Unveränderbarkeit dreifach abgesichert**:
  1. im Code (`Beleg.save()/delete()` und `BelegPosition` werfen `BelegGesperrt` außerhalb des Workflows),
  2. in der Datenbank (PL/pgSQL-Trigger aus Migration `belege/0002_gobd_trigger` – greift auch bei direktem
     SQL; einzig erlaubt ist Festgeschrieben → Storniert bei unverändertem Inhalt),
  3. nachträglich prüfbar (**Verwaltung → GoBD-Kettenprüfung**: Verkettung, Inhalts- und PDF-Prüfsummen).

> GoBD-Konformität ist am Ende eine Frage des Gesamtverfahrens (Verfahrensdokumentation, Berechtigungen,
> Datensicherung, Aufbewahrung). Die Technik hier ist die Grundlage, ersetzt aber keine Abstimmung mit
> Steuerberatung bzw. Wirtschaftsprüfung.

### PDF-Erzeugung
Django-Template-Engine + WeasyPrint (`apps/belege/pdf.py`). Auswahl der Vorlage:
1. aktive DB-Vorlage der jeweiligen Belegart, sonst
2. aktive DB-Vorlage ohne Belegart (Standard), sonst
3. Datei `templates/belege/pdf/standard.html` (DIN-5008-nah: Anschriftfeld für Fensterumschlag,
   Informationsblock, Positionen, Steueraufstellung, Fußzeile mit Firmen-/Bankdaten).

Vorlagen sind unter **Verwaltung → Belegvorlagen** und im Django-Admin als HTML editierbar. Verfügbare
Variablen: `beleg`, `positionen`, `partner` (Adress-Snapshot), `mandant`, `steuer_aufstellung`, `titel`,
`entwurf`, `storniert`; Filter `euro`, `menge`, `zahl`.

### Stammdaten
Artikel (Rohstoff, Halbzeug, Werkzeug, Betriebsmittel, Fertigartikel, Handelsware; Einheit, Chargenpflicht, Preise,
Steuersatz, EAN, Zolltarifnummer), Geschäftspartner (Kunde und/oder Lieferant, automatische Nummer, Adressen,
Ansprechpartner, USt-ID-Prüfung des Formats, Leitweg-ID, Zahlungsbedingung, Debitoren-/Kreditorenkonto),
mehrstufige Stücklisten (Auflösung mit Zykluserkennung, Rohstoffbedarf), Arbeitspläne mit Arbeitsgängen
(Rüst-/Stückzeit, Lohngruppe, akkordfähig), Kontenrahmen SKR03/SKR04 (**Grundbestand** von 107 Konten –
vor Produktivnutzung mit Steuerberatung abgleichen bzw. vollständigen Kontenrahmen importieren),
Steuersätze, Einheiten, Zahlungsbedingungen.

---

## REST-API

Basis: `/api/v1/` – Authentifizierung per Session, Basic-Auth oder Token. Alle Endpunkte prüfen die
Modulrechte des Benutzers.

| Endpunkt | Beschreibung |
|---|---|
| `POST /api/v1/token/` | Token holen (`username`, `password`) |
| `/api/v1/artikel/` (Filter `?art=ROHSTOFF`), `/partner/`, `/stuecklisten/` | Stammdaten (CRUD) |
| `/api/v1/konten/`, `/steuersaetze/` | Kontenrahmen und Steuersätze (nur lesen) |
| `/api/v1/belege/` | Belege inkl. Positionen (CRUD für Entwürfe) |
| `POST /api/v1/belege/{id}/freigeben/` · `/festschreiben/` · `/stornieren/` (`grund`) | Workflow |
| `GET /api/v1/belege/{id}/pdf/` | PDF herunterladen |

Gesperrte Belege liefern bei Änderungsversuchen **409 Conflict**, fehlende Rechte **403**.

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/v1/token/ -d username=admin -d 'password=Admin!2026' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -H "Authorization: Token $TOKEN" localhost:8000/api/v1/artikel/

curl -X POST -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" localhost:8000/api/v1/belege/ -d '{
  "belegart": "RECHNUNG", "partner": 1, "belegdatum": "2026-11-20",
  "positionen": [{"bezeichnung": "Montagearbeiten", "menge": "4", "einheit": "h", "einzelpreis": "85.00", "steuersatz": "19.00"}]
}'
curl -X POST -H "Authorization: Token $TOKEN" localhost:8000/api/v1/belege/<id>/freigeben/
```

---

## Weitere Dokumentation
- [docs/DATENMODELL.md](docs/DATENMODELL.md) – Datenmodell als Mermaid-ERD
- [docs/ROADMAP.md](docs/ROADMAP.md) – Phasen 2–6 mit groben Aufwänden

## Bekannte Grenzen von Phase 1
- Fachmodule Einkauf, Wareneingang, Produktion, Vertrieb, Versand, Fibu und Reporting sind nur als Gerüst mit
  Platzhalterseite vorhanden.
- Ein Mandant (Firma) pro Installation.
- Kontenrahmen nur als Grundbestand.
- Keine E-Rechnung (ZUGFeRD/XRechnung) – geplant für Phase 3.
- Docker-Compose-Setup ungetestet (siehe oben).

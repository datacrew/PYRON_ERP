# ERP-System Mittelstand – Phase 1 (Fundament) + Phase 2 (Warenfluss) + Phase 3 (Produktion)

Modulares ERP für einen mittelständischen Industriebetrieb – **Django 5.2 · Django REST Framework · PostgreSQL ·
WeasyPrint**. Phase 1 liefert das Fundament: Mandant, Modul-Registry, Rechte, Nummernkreise, Audit-Log,
Events, Stammdaten, Belegwesen mit GoBD-Festschreibung und PDF-Erzeugung, deutsche Weboberfläche, REST-API.
Phase 2 ergänzt den **Warenfluss**: Lager (Bestände, Chargen, Journal, Inventur, gleitender Durchschnittspreis),
Einkauf (Bestellanforderung, Anfrage, Bestellung, Bestellvorschlag, Bestell-PDF) und Wareneingang (gegen Bestellung,
Qualitätsprüfung mit Sperrbestand, WE-Beleg-PDF). Phase 3 bringt die **Produktion**: Fertigungsaufträge aus
Stückliste und Arbeitsplan, Fehlteilliste, Laufkarte mit Barcode, Betriebsdatenerfassung (Kommen/Gehen,
Auftragsstempelung am Terminal), Rückmeldungen, Zeitakkord mit Mindestlohngarantie, Materialentnahme,
Fertigmeldung zu Herstellkosten und Nachkalkulation. Vertrieb, Versand, Fibu und Reporting sind als Gerüste
registriert (siehe [docs/ROADMAP.md](docs/ROADMAP.md)).

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
python3 manage.py erp_demodaten          # optional: Demo-Benutzer, Artikel, Partner, Stücklisten, Belege,
                                         #   Lagerorte, Anfangsbestände, Bestellungen, Teil-Wareneingang

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
| `meister`     | `Demo!2026`  | Fertigungsleitung (inkl. Akkord, Stempelkorrektur) |
| `terminal`    | `Demo!2026`  | Nur Stempel-Terminal (Kiosk)              |
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
Phase 2 ergänzt Tests für Bestandsbuchung und gleitenden Durchschnittspreis, unveränderliches Lagerjournal
(Code + Trigger), Inventur, Melde-/Mindestbestand, Bestellfreigabe/Nummernkreis, Storno, Bestellerfassung über
die Weboberfläche, Bestellvorschlag, Bestell- und WE-PDF, Teil-/Rest-/Überlieferung mit Bestellstatus,
Preisdifferenzen, Chargenpflicht, QS-Sperre/-Freigabe und Reklamation mit Rücksendung sowie Rechte in Web und API
Phase 3 ergänzt 18 Tests für Bedarfsermittlung, Fehlteile und Unteraufträge, Freigabe/Storno, Stempellogik
(keine Überlappung, Unterbrechung, Gehen beendet laufende Stempelung, DB-Constraint), Terminal-Buchung,
Korrektur mit Audit-Log, Akkordberechnung (Leistungsgrad, Verdienst, Mindestlohngarantie, Zeitlohn, CSV),
chargengenaue Materialentnahme, Fertigmeldung zu Herstellkosten inkl. Teilmeldung und Nachkalkulation, Events,
Seiten/PDF, Web-Ablauf, Rechte und REST-API (Stand nach Phase 3: 66 Tests).
Phase 4 ergänzt 20 Tests für die Finanzbuchhaltung (gesamt 86). Phase 5 ergänzt 21 Tests für Vertrieb und
Versand (Kernfluss Angebot → Auftrag → Lieferschein → Rechnung mit Lagerabgang, Fibu-Buchung und OP,
Teillieferung, Gutschrift, ZUGFeRD-/XRechnung-XML inkl. XSD und Summen, PDF/A-3-Einbettung, Shop-API mit Token
und Idempotenz, EDIFACT-Parser/-Generator, Tourenbildung, Kapazitätsprüfung, LVS-Export/-Rückmeldung, Rechte).
**Aktueller Stand: 107 Tests, alle grün** (gezählt mit `python manage.py test apps`).

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
│   ├── lager/         Lagerorte/-plätze, Chargen, Bestände, Bewegungsjournal, Inventur, Bewertung   (Phase 2)
│   ├── einkauf/       Bestellanforderung, Anfrage, Bestellung, Bestellvorschlag, Bestell-PDF          (Phase 2)
│   ├── wareneingang/  Wareneingang gegen Bestellung, Qualitätsprüfung, WE-PDF                        (Phase 2)
│   ├── produktion/ bde/ akkord/   Fertigung, Terminal-Recht, Akkord-Recht (Phase 3)
│   ├── vertrieb/ versand/ fibu/ reporting/   (Gerüste)
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
Bereits veröffentlicht: `beleg.freigegeben`, `beleg.festgeschrieben`, `beleg.storniert` sowie ab Phase 2
`lager.bewegung`, `bestand.unterschritten`, `inventur.gebucht`, `bestellung.freigegeben`, `bestellung.storniert`,
`bestellung.status`, `wareneingang.gebucht` (nach Commit; Daten: `wareneingang`, `bestellung`, `wert`,
`benutzer`) und `wareneingang.qs_entschieden`.
Beispiel-Abonnent: `apps/fibu/ereignisse.py` (protokolliert festgeschriebene Belege und gebuchte Wareneingänge –
Ausgangspunkt für automatische Buchung und WE/RE-Verrechnung in Phase 4; **noch keine Fibu-Logik**).

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
(Rüst-/Stückzeit, Lohngruppe, Arbeitsplatz, Lohnart Akkord/Zeitlohn – seit Phase 3), Kontenrahmen SKR03/SKR04 (**Grundbestand** von 107 Konten –
vor Produktivnutzung mit Steuerberatung abgleichen bzw. vollständigen Kontenrahmen importieren),
Steuersätze, Einheiten, Zahlungsbedingungen.

---

## Phase 2: Warenfluss

### Lager (`apps/lager`, Menü **Lager**)
- **Lagerorte und -plätze** (z. B. RL Rohstofflager mit RL-01…03, FL Fertigwarenlager, WE Wareneingang/QS-Zone).
- **Bestand je Artikel × Lagerort × Lagerplatz × Charge × Bestandsart** (`FREI` oder `GESPERRT` = Sperrbestand QS);
  negative Bestände verhindert eine DB-Check-Constraint.
- **Bewegungsjournal** (Zugang, Abgang, Umbuchung, Inventurkorrektur) mit Wert, Ø-Preis und Gesamtbestand danach,
  Quelle (manuell, Wareneingang, QS, Inventur) und Belegnummer. **Unveränderlich**: im Code (`save()/delete()`
  werfen `LagerFehler`) und per PL/pgSQL-Trigger (`lager/0002_journal_trigger`, verbietet UPDATE/DELETE auch
  per SQL). Korrekturen nur über Gegenbuchungen.
- **Einzige Buchungsstelle** ist `apps/lager/services.py → buchen(...)`; parallele Buchungen eines Artikels werden
  per `SELECT … FOR UPDATE` auf die Dispositionsdaten serialisiert.
- **Bewertung: gleitender Durchschnittspreis** je Artikel (über alle Orte inkl. Sperrbestand):
  Zugang `Ø_neu = (Bestand × Ø_alt + Menge × Preis) / (Bestand + Menge)`, Abgang zum Ø, Umbuchung/QS-Freigabe
  ohne Werteffekt, Inventur-Mehr-/Mindermengen zum Ø.
- **Dispositionsdaten** je Artikel: Mindest-/Meldebestand, Standard-Bestellmenge, Wiederbeschaffungszeit,
  bevorzugter Lieferant, Standard-Lagerort, **QS-Pflicht**. Unterschreitungen erscheinen als Hinweis auf dem
  Dashboard und in der Bestandsübersicht; bei Abgängen wird `bestand.unterschritten` veröffentlicht.
- **Chargen**: Chargennummer, Lieferant, Lieferantencharge, Herstelldatum, MHD (optional); bei Artikeln mit
  `chargenpflicht` ist jede Buchung ohne Charge unzulässig.
- **Inventur**: Zählliste je Lagerort aus den Buchbeständen (Nummernkreis `INVENTUR`, `INV-`), Zählmengen erfassen,
  Buchen erzeugt Inventurkorrekturen gegen den *aktuellen* Buchbestand.

### Einkauf (`apps/einkauf`, Menü **Einkauf**)
- **Bestellanforderung** (BANF, `BA-`): Artikel, Menge, Bedarfstermin, Begründung; Übernahme mehrerer BANFs in
  einen Bestellentwurf.
- **Anfrage** (`EA-`) und **Bestellung** (`EK-`) mit Positionen (Artikel, Lieferanten-Art.-Nr., Menge, Einheit,
  Preis, Rabatt, USt, Liefertermin); leere Felder werden aus dem Artikelstamm vorbelegt. Anfrage → Bestellung per
  Knopfdruck.
- **Status**: Entwurf → Bestellt (bzw. Angefragt) → Teilgeliefert → Geliefert; Storniert (mit Pflicht-Grund,
  nur solange nichts geliefert ist). Die Nummer wird erst bei der **Freigabe** über `naechste_nummer()` vergeben,
  die Lieferantenanschrift dabei eingefroren. Unterlieferungen lassen sich als „Restmenge abgeschlossen“ erledigen.
- **Bestell-PDF** über WeasyPrint mit eigener Vorlage `templates/einkauf/pdf/bestellung.html` (DIN-5008-nah,
  gleiche Kopf-/Fußgestaltung wie die Belege).
- **Liefertermin-Übersicht** offener Positionen (überfällige rot markiert) und **Bestellvorschlag**: Artikel, deren
  freier Bestand + offene Bestellmenge unter dem Meldebestand liegt; Menge = max(Standard-Bestellmenge, Bedarf);
  „Bestellen“ erzeugt je bevorzugtem Lieferanten einen Bestellentwurf.
- Bestellungen sind bewusst ein eigenes Modell (nicht `belege.Beleg`): Sie sind keine buchungsrelevanten
  Belege, werden aber vollständig im Audit-Log protokolliert. Die GoBD-Logik der `belege` bleibt unberührt.

### Wareneingang (`apps/wareneingang`, Menü **Wareneingang**)
- **Buchung gegen eine offene Bestellung** (Nummernkreis `WARENEINGANG`, `WEB-`) mit Lieferschein-Nr. des
  Lieferanten (Pflicht), Lagerort/-platz, Teil-, Rest- und Überlieferung. Über-/Unterlieferung und
  **Preisdifferenzen** (Lieferpreis ≠ Bestellpreis) werden je Position und im Kopf protokolliert.
- **Chargenerfassung** (Charge, Lieferantencharge, MHD, Herstelldatum) – Pflicht bei chargenpflichtigen Artikeln.
- **Qualitätsprüfung**: Artikel mit QS-Pflicht (oder Häkchen „QS prüfen“) gehen in den **Sperrbestand**, Status
  *offen*. **Freigeben** bucht Sperrbestand → frei verfügbar; **Sperren/Reklamieren** verlangt einen Grund und kann
  die Ware zurücksenden (Abgang aus Sperrbestand, gelieferte Menge der Bestellung wird wieder offen).
  QS-Entscheidungen erfordern das Recht `wareneingang.freigeben` (Rolle Lager).
- Je Buchung: Lagerzugang (bewertet zum Lieferpreis, sonst Bestell-Nettopreis), Fortschreibung der
  Bestellposition und des Bestellstatus, Event `wareneingang.gebucht` (nur Event, keine Fibu-Buchung).
- **Wareneingangsbeleg-PDF** (`templates/wareneingang/pdf/wareneingang.html`).

### Rechte (Standardrollen)
| Rolle | Lager | Einkauf | Wareneingang |
|---|---|---|---|
| Einkauf | lesen | alle | lesen, anlegen (buchen) |
| Lager | alle | lesen | alle (inkl. QS-Entscheid) |
| Produktion | lesen, anlegen | – | lesen |
| Buchhaltung | lesen | lesen | lesen |
| Nur Lesen | lesen | lesen | lesen |

`erp_grunddaten` ergänzt bei bestehenden Rollen fehlende Moduleinträge, ohne vorhandene Rechte zu ändern.

### Demo-Ablauf (nach `erp_demodaten`)
1. Als `einkauf` anmelden → **Einkauf → Neue Bestellung**, Lieferant und Positionen erfassen → **Freigeben**
   → **PDF**.
2. Als `lager` anmelden → **Wareneingang** → offene Bestellung wählen → Teilmenge mit Charge buchen.
3. Bei QS-pflichtigen Artikeln (Demo: R-1000 Stahlblech, B-3100 Schweißdraht) erscheint die Position unter
   „Offene QS-Prüfungen“ → **Freigeben** → Bestand ist frei verfügbar (**Lager → Bestandsübersicht**).

Die Demodaten enthalten bereits eine teilgelieferte Bestellung bei Stahlhandel Schmidt mit Charge
`CH-2026-0101` (R-1000) in QS-Sperre, eine offene Bestellung bei Normteile Becker, einen Bestellentwurf, eine
Anfrage, eine BANF sowie einen Bestellvorschlag (R-1010 unter Meldebestand).

---

## Phase 3: Produktion mit Betriebsdatenerfassung und Akkord

Module: `apps/produktion` (Menü **Produktion**), Rechte-Module `bde` (Stempel-Terminal) und `akkord`
(Lohnauswertung, vertraulich). Stammdaten-Ergänzungen unter **Stammdaten → Arbeitsplätze** und
**Produktion → Mitarbeiter**.

### Stammdaten
- **Arbeitsplatz** (Maschine/Handarbeitsplatz): Code, Bezeichnung, Art, Kostenstelle, **Stundensatz €/h**
  (Platzkosten für Kalkulation).
- **Mitarbeiter** (Personal): Personalnummer, Ausweis-/Stempelkartennummer, Lohngruppe, **Stundenlohn**
  (Grundlohn, Basis für Zeitlohn und Garantie), **Akkordrichtsatz €/h**, optional verknüpfter Benutzer.
- **Arbeitsgang** im Arbeitsplan: Position, Arbeitsplatz (FK), Rüstzeit tr (min), Stückzeit te (min/Stk),
  Lohngruppe, **Lohnart** Akkord oder Zeitlohn.

### Fertigungsaufträge
- Anlegen aus Fertigartikel/Halbzeug/Werkzeug mit aktiver Stückliste und aktivem Arbeitsplan, Menge, Start-/
  Endtermin, Ziel-Lagerort. Nummer `FA-2026-00001` aus dem Nummernkreis `FERTIGUNGSAUFTRAG`.
- Status: **Entwurf → Freigegeben → In Arbeit → Rückgemeldet/teilgefertigt → Abgeschlossen**, Storno mit Grund
  (nur ohne Entnahme/Fertigmeldung).
- **Freigabe** kopiert Materialbedarf (Stückliste, eine Ebene) und Arbeitsgänge in den Auftrag. Optional
  (Häkchen „Unteraufträge“) werden für fehlende Eigenfertigungsteile (Halbzeuge) eigene Unteraufträge angelegt;
  die Fehlteilliste (Bedarf gegen **freien** Bestand) zeigt diese Vorschläge mehrstufig an.
- **Laufkarte/Arbeitsbegleitschein (PDF)**: Kopf mit Barcode der Auftragsnummer, Arbeitsgänge mit Vorgaben und
  Code-128-Barcode samt Klartext je Arbeitsgang (`FA-2026-00001-10`), Materialliste mit Fehlmengen, Felder für
  Gut/Ausschuss.

### Stempel-Terminal (Menü **Produktion → Terminal**)
Großflächige Seite für Tastatur und Barcodescanner (Scanner im Tastaturmodus, schließt mit Enter ab):
1. **Personalnummer oder Ausweisnummer** scannen/eintippen → Enter springt ins Feld Arbeitsgang.
2. **Arbeitsgang-Code** von der Laufkarte scannen → Enter bucht automatisch:
   - ohne Code und nicht anwesend → **Kommen**;
   - mit Code → **Start** (Fertigen) an diesem Arbeitsgang; fehlt das Kommen, wird es automatisch gebucht.
     Ist der Mitarbeiter noch an einem anderen Arbeitsgang angestempelt, lehnt das Terminal ab – erst
     „Unterbrechung“ oder „Ende“ buchen (keine überlappenden Auftragsstempelungen, zusätzlich per DB-Constraint).
3. Schaltflächen für gezielte Aktionen: **Kommen**, **Gehen** (unterbricht eine laufende Stempelung),
   **Rüsten**, **Start**, **Unterbrechung**, **Ende**. Bei Unterbrechung/Ende können Gutmenge, Ausschuss (mit
   Ausschussgrund – Pflicht bei Ausschuss) und Nacharbeit gleich mit zurückgemeldet werden.
4. Die Codes `KOMMEN` bzw. `GEHEN` können als Barcode am Terminal hängen und statt eines Arbeitsgangs
   gescannt werden.

Für einen Kiosk-PC einen eigenen Benutzer mit Rolle **Terminal** anlegen (Demo: `terminal`); diese Rolle darf
nur stempeln. Rückmeldungen ohne Stempelung erfasst man unter **Produktion → Rückmeldungen**.
**Korrekturen und Nachträge** von Stempelungen (Produktion → Stempelungen) erfordern das Recht `bde.ändern`
(Rolle Fertigungsleitung), verlangen einen Grund und werden mit Vorher/Nachher im Audit-Log protokolliert.

### Zeitakkord (Menü **Produktion → Akkord**, Recht `akkord`)
Je Mitarbeiter und Zeitraum (optional je Auftrag), nur Arbeitsgänge mit Lohnart Akkord:

| Größe | Formel |
|---|---|
| Vorgabezeit (min) | Σ ( Gutmenge × te ) + tr (Rüstzeit einmal je Arbeitsgang, beim Mitarbeiter mit der ersten Rüst-Stempelung) |
| Ist-Zeit (min) | Σ Dauer der abgeschlossenen Akkord-Stempelungen |
| **Leistungsgrad** | Vorgabezeit ÷ Ist-Zeit × 100 % |
| Minutenfaktor | Akkordrichtsatz ÷ 60 |
| **Akkordverdienst** | Vorgabezeit × Akkordrichtsatz ÷ 60 |
| Garantiesatz | max( Stundenlohn, gesetzlicher Mindestlohn ) |
| Garantie | Ist-Zeit ÷ 60 × Garantiesatz |
| Ausgleich | max( Garantie − Akkordverdienst, 0 ) |
| Akkordlohn | Akkordverdienst + Ausgleich |
| Zeitlohn | Ist-Zeit der Zeitlohn-Arbeitsgänge ÷ 60 × Garantiesatz (separat ausgewiesen) |

Beispiel: Vorgabe 480 min, Ist 450 min, Richtsatz 18 €/h → Leistungsgrad 106,7 %, Verdienst 144,00 €.
Der Mindestlohn steht in `settings.ERP_MINDESTLOHN` (Vorgabe **13,90 €/h**, per Umgebungsvariable änderbar –
bitte den aktuell gültigen gesetzlichen Wert prüfen). Ausgabe als Bildschirmliste, **PDF** (Lohnübersicht,
A4 quer) und **CSV** (Semikolon, Dezimalkomma) für die Lohnbuchhaltung – **keine Lohnabrechnung**.
Hinweis: Zeiterfassung und Akkord sind mitbestimmungspflichtig (Betriebsrat) und datenschutzrelevant; das
Produktions-Dashboard zeigt Leistungsgrade deshalb nur je Arbeitsgang, nicht je Person.

### Material, Fertigmeldung, Kalkulation
- **Materialentnahme** (Produktion → Auftrag → Entnahme): Lagerabgang `ABGANG` zum Auftrag über
  `lager.services.buchen()`, chargengenau (bei Chargenpflicht Bestand/Charge wählen), „Rest entnehmen“ für
  alle nicht chargenpflichtigen Materialien (FIFO über Bestände). Wert = gleitender Durchschnittspreis.
- **Fertigmeldung**: Lagerzugang `ZUGANG` des Artikels mit **Fertigungscharge** `FC-2026-00001` (oder eigener
  Nummer) zu **Herstellkosten** = Material (Entnahmewerte) + Lohn (Ist-Zeit × Richtsatz bzw. Stundenlohn) +
  Arbeitsplatzkosten (Ist-Zeit × Stundensatz). Teilmeldungen aktivieren die bis dahin angefallenen Kosten
  anteilig, die Schlussmeldung den Rest; ohne Ist-Kosten wird zu Soll-Herstellkosten bewertet.
- **Nachkalkulation** Soll/Ist je Kostenart, Material und Arbeitsgang (inkl. Leistungsgrad je AG). Soll für
  Auftragsmenge und Gutmenge; Abweichung bei abgeschlossenen Aufträgen gegen die Gutmenge.
- Events `produktion.freigegeben`, `produktion.material_entnommen`, `produktion.rueckmeldung`,
  `produktion.fertiggemeldet` – die Fibu (`apps/fibu/ereignisse.py`) protokolliert sie nur, Buchungen folgen
  mit Phase 4.

### Rechte (Standardrollen Produktion)
| Rolle | produktion | bde (Terminal) | akkord (Lohn) | lager |
|---|---|---|---|---|
| Produktion | alle | lesen, anlegen (stempeln) | – | lesen, anlegen |
| Fertigungsleitung | alle | alle (inkl. Korrektur) | alle | lesen, anlegen |
| Terminal | – | lesen, anlegen | – | – |
| Buchhaltung | lesen | – | lesen | lesen |
| Nur Lesen | lesen | lesen | – | lesen |

### Demo-Ablauf Produktion
1. Als `produktion` anmelden → **Produktion → Neuer Auftrag**, z. B. H-2000 (Blechzuschnitt), 50 Stk →
   **Freigeben** → **Laufkarte (PDF)**.
2. **Entnahme**: Charge `CH-2026-0001` für R-1000 wählen → entnehmen.
3. **Terminal**: `P1005` + `FA-2026-0000x-10` scannen (Start), danach „Ende“ mit Gutmenge.
4. **Fertigmelden** → Bestand unter Lager, Kosten unter **Nachkalkulation**.
5. Als `meister` → **Akkord**: Leistungsgrad, Verdienst, Garantie; PDF/CSV.

Die Demodaten enthalten Arbeitsplätze, fünf Mitarbeiter (P1001–P1005, darunter P1003 mit Stundenlohn unter
Mindestlohn), Arbeitspläne für F-5000/H-2000/H-2010/F-5010 und vier Fertigungsaufträge (abgeschlossen,
teilgefertigt, in Arbeit mit Nacharbeit, Entwurf) mit Stempelungen über die letzten Werktage.

---

## Phase 4: Finanzbuchhaltung

Menü **Finanzbuchhaltung** (`apps/fibu`, Rechte-Module `fibu`, `fibu_bank`, `fibu_mahnwesen`, `fibu_abschluss`).

> **Wichtig:** Kontenrahmen, Steuerschlüssel, Kontierungsregeln, UStVA-Kennziffern, Bilanz-/GuV-Gliederung und
> DATEV-Export sind fachlich sorgfältig, aber **nicht von einem Steuerberater abgenommen**. Vor dem produktiven
> Einsatz müssen Kontenzuordnung, Steuerlogik und Verfahrensdokumentation (GoBD) durch den Steuerberater bzw.
> Wirtschaftsprüfer geprüft und freigegeben werden. Kein ELSTER-Versand.

### Funktionen
- **Hauptbuch (HGB):** Geschäftsjahre mit 12 Monatsperioden, Periodensperre (schreibt offene Buchungen fest),
  Splitbuchungen mit erzwungenem Soll = Haben, Nummernkreis `FIBU_BUCHUNG`.
- **Steuerschlüssel:** U19/U7 (USt), V19/V7 (VSt), IGL/AUS (steuerfrei), IGE19/IGE7 (innergem. Erwerb) und
  RC19 (§ 13b) – automatische Steuerzeile; igE/§ 13b zweiseitig (VSt und USt, saldoneutral).
- **GoBD:** Festschreibung mit SHA-256-Hash-Kette (Seite *Hash-Kette prüfen*), Code-Sperre im Modell und
  PostgreSQL-Trigger gegen UPDATE/DELETE festgeschriebener Buchungen und Zeilen. Korrektur nur per Storno.
- **Kontenrahmen SKR03/SKR04** (je Mandant wählbar) mit Bilanz-/GuV-Zuordnung, Automatik- und Sammelkonten.
- **Nebenbücher:** Personenkonten (Debitoren 10000 ff., Kreditoren 70000 ff., automatisch je Partner),
  offene Posten, vollständiger/teilweiser Ausgleich, Skonto mit anteiliger Steuerkorrektur.
- **Eingangsrechnungen** (`ER-…`): Erfassung frei oder aus Wareneingang, Rechnungsprüfung gegen Bestellung
  und Wareneingang (Menge, Preis mit Toleranz `ERP_RECHNUNGSPRUEFUNG_TOLERANZ_PROZENT`), bei Abweichung
  ist eine Freigabe nötig.
- **Bankimport** CAMT.053 und MT940: Dubletten werden über Datei-Hash und Umsatz-Schlüssel erkannt. Danach
  bewertete Zuordnungsvorschläge (Belegnummer, Betrag inkl. Skonto, IBAN, Name), automatische Zuordnung
  eindeutiger Treffer, Gebühren auf Nebenkosten des Geldverkehrs, Rest auf Sachkonto oder Verrechnungskonto
  „ungeklärt“ (spätere Umbuchung möglich). Beispiel-Dateien: `docs/beispiele/camt053_demo.xml` und
  `docs/beispiele/mt940_demo.sta`.
- **Mahnwesen:** Stufen (Zahlungserinnerung, 1.–3. Mahnung) mit Fristen, Gebühren und Zinsen (§ 288 BGB:
  Basiszins + 9 Prozentpunkte B2B bzw. + 5 Prozentpunkte B2C; Basiszinssätze als Einstellung). Ablauf:
  Vorschlagslauf → Ausführen (Mahnhistorie an den OPs). Mahnsperre je Partner und je OP, Mahn-PDF.
- **Auswertungen** (Ansicht, PDF, CSV): Summen- und Saldenliste, Kontoblatt, Journal, OP-Listen mit
  Fälligkeitsraster, Bilanz (§ 266) und GuV (§ 275 GKV) in vereinfachter Gliederung, UStVA-Kennziffern,
  DATEV-Buchungsstapel (EXTF 700, cp1252).
- **Jahresabschluss:** Saldenvortrag ins Folgejahr (GuV-Ergebnis → Gewinnvortrag), Geschäftsjahr abschließen.
- **REST-API:** `/api/v1/fibu/konten/` (mit Salden), `/api/v1/buchungen/` (GET/POST, Aktionen `festschreiben`,
  `stornieren`), `/api/v1/offene-posten/`, `/api/v1/bankumsaetze/`.

### Kontierungslogik (Automatikbuchungen, idempotent über Quelle + ID)
| Vorgang (Event) | Buchung (SKR03) |
|---|---|
| Ausgangsrechnung festgeschrieben (`beleg.festgeschrieben`) | Debitor an 8400 Erlöse 19 % + 1776 USt, OP |
| Beleg storniert | Stornobuchung |
| Wareneingang (`wareneingang.gebucht`) | Bestand (3970 RHB / 7000 unfertige / 7110 fertige Erzeugnisse) an 1595 WE/RE |
| Eingangsrechnung mit Bestellbezug | 1595 WE/RE (WE-Wert) + Preisdifferenz auf Aufwand + 1576 VSt an Kreditor, OP |
| Eingangsrechnung ohne Bestellbezug | Aufwandskonto (Position bzw. Regel) + VSt an Kreditor |
| Materialentnahme (`produktion.material_entnommen`) | 3000 Materialaufwand an Bestand |
| Fertigmeldung (`produktion.fertiggemeldet`) | Bestand Erzeugnisse an 8980/8960 Bestandsveränderung |
| Zahlungseingang/-ausgang | Bank an Debitor bzw. Kreditor an Bank; Skonto 8736/3736 mit Steuerkorrektur |

Die Konten werden über **Kontierungsregeln** (Vorgang × Artikelart × Steuerschlüssel, je Kontenrahmen) ermittelt.
Sie sind unter *Finanzbuchhaltung → Einstellungen* pflegbar. Schlägt eine Automatikbuchung fehl (z. B. weil kein
Geschäftsjahr angelegt oder die Periode gesperrt ist), läuft der Vorgang im Vorsystem trotzdem durch. Der Fehler
steht im Automatik-Protokoll und lässt sich über „Nachbuchen“ erneut versuchen.

### Rechte
Die Rolle **Buchhaltung** hat alle Fibu-Rechte. Die Rechte sind getrennt:

| Modul | Recht | Erlaubt |
|---|---|---|
| `fibu` | anlegen | Buchen |
| `fibu` | freigeben | Festschreiben |
| `fibu_abschluss` | anlegen | Periodenabschluss / Saldenvortrag |
| `fibu_mahnwesen` | anlegen | Mahnvorschlag |
| `fibu_mahnwesen` | freigeben | Mahnlauf ausführen |
| `fibu_bank` | anlegen | Bankimport / Zuordnung |

**Nur Lesen** darf ansehen.

### Demodaten Fibu
Inhalt:
- Geschäftsjahr 2026 mit Eröffnungsbilanz.
- Ausgangsrechnungen RE-2026-00001…00005:
  - 00001 bezahlt
  - 00002 überfällig, bereits erinnert
  - 00003 mit Skonto bezahlt
  - 00004 teilbezahlt und überfällig
  - 00005 offen
- Eingangsrechnungen von Becker (bezahlt) und Schmidt (offen).
- Eine Rechnung zum Wareneingang ist erfasst und noch nicht gebucht.
- Der MT940-Auszug Juli–November ist abgearbeitet.
- Die Perioden 01–09/2026 sind gesperrt.

### Demo-Ablauf Fibu (Login `buchhaltung` / `Demo!2026`)
1. **Dashboard Fibu:** offene Forderungen/Verbindlichkeiten, Überfälliges, Bankstand.
2. **Bank → Kontoauszug importieren:** `docs/beispiele/camt053_demo.xml` mit „automatisch zuordnen“ hochladen.
   - RE-2026-00005 (Müller) wird mit 2 % Skonto ausgeglichen, inklusive USt-Korrektur.
   - Die Zahlung an Schmidt gleicht ER-2026-00002 aus.
   - Die Gebühr wird gebucht.
   - Die Gutschrift „Hans Mustermann“ bleibt offen; über den Umsatz auf „ungeklärt“ buchen.
3. **OP-Liste:** RE-2026-00005 ist ausgeglichen (Ausgleichshistorie im OP).
4. **Mahnwesen → Vorschlag:** Stichtag heute ergibt eine Mahnung an Autoteile Weber (Stufe 1. Mahnung, Zinsen
   nach § 288 BGB, 5 € Gebühr) → PDF ansehen → Lauf ausführen.
5. **Eingangsrechnungen → SH-RE-2026-4471:** „Prüfen“ (gegen Wareneingang WEB-2026-00001) → „Buchen“. Danach ist
   das WE/RE-Konto ausgeglichen.
6. **Auswertungen:** Bilanz und GuV 2026, UStVA 11/2026 und DATEV-Export (Zeitraum wählen), jeweils als PDF oder CSV.

---

## Phase 5: Vertrieb und Versand

Neue Module (Registry `erp_modul`, Rechte getrennt): **vertrieb** (CRM, Angebote, Aufträge, Lieferscheine,
Rechnungen), **edi** (EDIFACT), **shopapi** (Onlineshop-Schnittstelle), **versand** (Touren, Fahrzeuge, LVS).

### Funktionen
- **CRM** (Menü **Vertrieb**): Leads mit Status Neu → Qualifiziert → Angebot → Gewonnen/Verloren, Zuständiger,
  Umwandlung in Kunde (Geschäftspartner mit Kundennummer aus dem Nummernkreis), Aktivitäten (Anruf, Besuch,
  E-Mail, Notiz) mit Wiedervorlage, Verkaufschancen mit Wert und Wahrscheinlichkeit, Pipeline-Übersicht.
- **Verkaufsprozess:** Angebot (Beleg) → Auftrag mit Auftragsbestätigung (AB-Beleg, festgeschrieben) →
  Lieferschein(e) mit **Teillieferungen** → Rechnung je Lieferschein oder **Sammelrechnung** → Gutschrift/Storno.
  Rechnungen sind normale Belege: Festschreibung, GoBD-Hash und die Fibu-Automatikbuchung inkl. OP laufen
  unverändert über das Event `beleg.festgeschrieben`.
- **Kundenpreise:** kundenspezifische Preise und einfache Mengenstaffeln (Preis oder Rabatt %), je Kunde oder
  für alle Kunden; Liefertermine je Position.
- **Verfügbarkeitsprüfung** gegen freien Lagerbestand mit Hinweis auf Fertigungsbedarf; optional
  Fertigungsauftrag direkt aus der Auftragsposition.
- **Lieferschein buchen** erzeugt den Lagerabgang chargengenau (FIFO-Vorschlag oder Charge aus LVS-Rückmeldung)
  über das bestehende Lagerjournal.
- **E-Rechnung:** ZUGFeRD 2.x / Factur-X Profil EN 16931 (PDF/A-3b mit eingebettetem `factur-x.xml`) und
  **XRechnung** (UBL 2.1 bzw. CII, Leitweg-ID aus dem Partnerstamm). Prüfung gegen die offiziellen XSD und
  eigene Summenregeln (BR-CO-10/13/14/15). Download auf der Rechnungsseite.
- **Shop-API** (`/api/v1/shop/…`): Artikel und Bestände lesen, Bestellung anlegen (Kunde wird per E-Mail/Name
  gefunden oder angelegt), Statusabfrage mit Versandinformation; Auth per Token je Shop
  (`Authorization: Shop <token>`), **Idempotenz** über die externe Bestellnummer (201 neu, 200 Wiederholung).
- **EDIFACT D.96A:** Import ORDERS → Kundenauftrag; Export ORDRSP (aus AB), DESADV (aus Lieferschein) und
  INVOIC (aus Rechnung). Partner mit GLN/ILN und Kundenartikelnummern-Zuordnung, Nachrichten-Eingang/-Ausgang
  mit Status und Fehlerprotokoll (Menü **Vertrieb → EDI**).
- **Versand** (Menü **Versand**): Regionen (PLZ-Bereiche, Liefertage, Sortierung nach PLZ oder Entfernung über
  hinterlegte Geokoordinaten – kein externer Dienst), Fahrzeuge mit Gewichts-/Palettenkapazität,
  automatische **Tourenbildung** aus gebuchten Lieferscheinen mit Auslastungsprüfung, Stopp-Reihenfolge,
  Versandstatus (Geplant → Freigegeben → Unterwegs → Zugestellt), **Tourenliste/Ladeliste als PDF**.
- **LVS-Schnittstelle** (Adapter): Export von Kommissionieraufträgen als CSV/JSON, Import von Rückmeldungen
  (kommissionierte Mengen, Chargen, Packstücke) per Datei oder REST, Protokoll je Import.
- **Dashboards:** Vertrieb (Pipeline, offene Aufträge, Lieferrückstand, Umsatz Monat), Versand (Touren heute/
  morgen, Auslastung); Kacheln auf der Startseite.

### Rechte (Standardrollen)
| Rolle | vertrieb | edi | shopapi | versand |
|---|---|---|---|---|
| Vertrieb | alle | alle | lesen/anlegen/ändern | lesen |
| Lager | lesen | – | – | alle |
| Gast | lesen | lesen | lesen | lesen |
| Admin | alle | alle | alle | alle |

### Demodaten (`python manage.py erp_demodaten`)
5 Leads in allen Status inkl. Aktivitäten/Wiedervorlagen und Verkaufschancen; Kundenpreise; Aufträge
VA-…-1 (aus Angebot, geliefert und berechnet), VA-…-2 (Müller, Teillieferung + Restlieferschein in der
Kommissionierung beim LVS), VA-…-3 (Fertigungsbedarf), VA-…-4 (aus der **Shop-API**, Neukunde „Werkstatt
Keller“), VA-…-5 (aus **EDIFACT** `docs/beispiele/orders_demo.edi`, Autoteile Weber AG, mit ORDRSP, DESADV,
INVOIC); drei Fahrzeuge, drei Regionen (Stuttgart, Neckar, Bayern) und Touren in mehreren Status;
festgeschriebene Rechnungen RE-2026-00006 bis -00008 mit OP in der Fibu.

### Demo-Ablauf (Login `vertrieb` / `Demo!2026`, Versand mit `lager` / `Demo!2026`)
1. **Vertrieb → Pipeline:** Lead „Kunststofftechnik Rems GmbH“ öffnen, Aktivität erfassen, qualifizieren, in Kunde umwandeln.
2. **Angebote → Neu** für den neuen Kunden, Positionen erfassen (Kundenpreise werden vorgeschlagen), Angebot
   übernehmen → Auftrag; im Auftrag **Verfügbarkeit** prüfen und **bestätigen** (AB-PDF).
3. **Lieferschein erzeugen** (Teilmenge möglich) → **buchen** (Lagerabgang chargengenau).
4. **Versand → Touren bilden** für das Lieferdatum → Tour freigeben, Ladeliste als PDF.
5. **Rechnung erzeugen** (oder Sammelrechnung über mehrere Lieferscheine) → **festschreiben** → ZUGFeRD-PDF und
   XRechnung herunterladen; in **Fibu → Offene Posten** erscheint der Debitoren-OP.
6. **Vertrieb → EDI:** `docs/beispiele/orders_demo.edi` hochladen → Auftrag entsteht; INVOIC aus einer Rechnung
   erzeugen und herunterladen.

Beispiel-PDFs (frisch aus den Demodaten erzeugt): `docs/beispiel_pdfs/` – `rechnung_zugferd.pdf`,
`lieferschein.pdf`, `tourenliste.pdf`, `mahnung.pdf`.

### API-Beispiele (curl)
```bash
# Shop-API – Demo-Token (nur für die Demo! In Produktion je Shop einen eigenen Token hinterlegen, Vertrieb → Shops)
SHOP="Authorization: Shop shop_demo_musterwerk_2026"
curl -H "$SHOP" localhost:3000/api/v1/shop/artikel/
curl -H "$SHOP" localhost:3000/api/v1/shop/bestaende/
curl -X POST -H "$SHOP" -H "Content-Type: application/json" localhost:3000/api/v1/shop/bestellungen/ -d '{
  "externe_nummer": "WEB-100777",
  "kunde": {"name": "Metallbau Beispiel GmbH", "strasse": "Hauptstr. 1", "plz": "70173", "ort": "Stuttgart",
            "email": "einkauf@metallbau-beispiel.de"},
  "positionen": [{"artikelnummer": "F-5000", "menge": "10"}]
}'   # 201 = angelegt; gleicher Aufruf erneut → 200 mit demselben Auftrag (idempotent)
curl -H "$SHOP" localhost:3000/api/v1/shop/bestellungen/WEB-100777/   # Status + Versandinformation

# Interne API (Token wie oben unter „REST-API“)
curl -H "Authorization: Token $TOKEN" localhost:3000/api/v1/vertrieb/auftraege/
curl -H "Authorization: Token $TOKEN" localhost:3000/api/v1/vertrieb/auftraege/1/verfuegbarkeit/
curl -X POST -H "Authorization: Token $TOKEN" -F datei=@docs/beispiele/orders_demo.edi \
     localhost:3000/api/v1/edi/nachrichten/orders/
curl -X POST -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
     localhost:3000/api/v1/versand/touren/bilden/ -d '{"datum": "2026-11-24"}'
curl -H "Authorization: Token $TOKEN" "localhost:3000/api/v1/versand/lvs/kommissionierauftraege/?format=CSV"
curl -X POST -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
     localhost:3000/api/v1/versand/lvs/rueckmeldungen/ -d '{"buchen": false, "rueckmeldungen": [
  {"referenz": "<Kommissionier-Referenz>", "positionen": [{"position_id": 1, "menge": "20",
   "charge": "FC-…", "packstueck": "PAL-1"}]}]}'
```
OpenAPI-Schema: `/api/v1/schema/`, Swagger-UI: `/api/v1/docs/`, ReDoc: `/api/v1/redoc/`.

### EDI-Hinweise
- Unterstützt: EDIFACT **D.96A**, UNA/UNB-Syntax UNOC, Segmente u. a. BGM, DTM, NAD (GLN), LIN (EAN), PIA
  (Kundenartikelnummer), QTY, PRI. Zuordnung des Kunden über die GLN aus NAD+BY im **EDI-Partner** (Demo:
  Autoteile Weber AG, GLN `4298765000007`), Artikel über EAN oder Kundenartikelnummer (Demo: `WEB-LB200`,
  `WEB-AE1`).
- Fehlerhafte Nachrichten (unbekannter Partner/Artikel, Syntaxfehler) werden mit Status **Fehler** und
  Fehlerprotokoll gespeichert, es entsteht kein Auftrag. Doppelte Bestellnummern werden abgewiesen.
- Ausgehende Nachrichten (ORDRSP/DESADV/INVOIC) haben Status **Erzeugt** und werden heruntergeladen; „als
  übertragen markieren“ setzt **Übertragen**. Eine echte Übertragungsstrecke (AS2, X.400, SFTP, Clearingstelle)
  ist **nicht** enthalten – dafür gibt es eine Adapter-Klasse als Einstiegspunkt.

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
| `/api/v1/bestellungen/` (Filter `?status=`, `?typ=`, `?lieferant=`) | Anfragen/Bestellungen inkl. Positionen (CRUD für Entwürfe) |
| `POST /api/v1/bestellungen/{id}/freigeben/` · `/stornieren/` (`grund`) · `GET …/pdf/` · `GET /api/v1/bestellungen/offen/` | Einkaufs-Workflow, PDF, offene Positionen |
| `GET /api/v1/wareneingaenge/` · `POST /api/v1/wareneingaenge/` | Wareneingänge lesen / gegen Bestellung buchen |
| `POST /api/v1/wareneingaenge/{id}/qs/` (`position`, `entscheid` FREIGEGEBEN/GESPERRT, `bemerkung`, `ruecksenden`) · `GET …/pdf/` | QS-Entscheid, WE-PDF |
| `GET /api/v1/bestaende/` (Filter `?artikel=`, `?lagerort=`, `?charge=`, `?bestandsart=`) | Bestände (nur lesen) |
| `GET /api/v1/lagerbewegungen/` · `POST` (manuelle Buchung: `art`, `artikel`, `menge`, `von_lagerort`/`nach_lagerort` (+ Plätze), `bestandsart`, `chargennummer`, `preis`) | Bewegungsjournal |
| `/api/v1/lagerorte/`, `/api/v1/chargen/` | Lagerorte (inkl. Plätze) und Chargen |
| `/api/v1/fertigungsauftraege/` (Filter `?status=`, `?artikel=`) | Fertigungsaufträge inkl. Material und Arbeitsgängen (Anlegen; Ändern nur im Entwurf) |
| `POST /api/v1/fertigungsauftraege/{id}/freigeben/` (`unterauftraege`) · `/stornieren/` (`grund`) · `GET …/fehlteile/` · `…/laufkarte/` · `…/nachkalkulation/` | Auftrags-Workflow, Fehlteile, Laufkarte-PDF, Nachkalkulation |
| `GET /api/v1/stempelungen/` · `GET …/laufend/` · `POST /api/v1/stempelungen/` (`personalnummer`, `code`, `aktion` auto/kommen/gehen/ruesten/start/unterbrechung/ende, `gut`, `ausschuss`, `grund`, `nacharbeit`) | Stempelungen; POST für externe Terminals (Quelle `API`) |
| `PATCH /api/v1/stempelungen/{id}/korrektur/` (`beginn`, `ende`, `grund`) | Korrektur (Recht `bde.ändern`, Audit-Log) |
| `/api/v1/rueckmeldungen/` (GET, POST) | Rückmeldungen Gut/Ausschuss/Nacharbeit |

Gesperrte Belege und fachlich unzulässige Buchungen (z. B. zu wenig Bestand, fehlende Charge, Bestellung nicht
offen) liefern **409 Conflict** mit `{"fehler": "…"}`, fehlende Rechte **403**.

```bash
# Wareneingang per API buchen
curl -X POST -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" localhost:8000/api/v1/wareneingaenge/ -d '{
  "bestellung": 1, "lieferschein_nr": "LS-4711", "lagerort": 3,
  "positionen": [{"bestellposition": 1, "menge": "500", "chargennummer": "CH-2026-0200", "lieferantencharge": "SH-1"}]
}'
```

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

## Bekannte Grenzen (Stand Phase 5)
- Das Modul Reporting ist nur als Gerüst mit Platzhalterseite vorhanden.
- E-Rechnung: Prüfung nur per XSD (Factur-X EN 16931 / CII D16B, UBL 2.1) und eigenen Summenregeln; kein
  Schematron/KoSIT-Validator, keine veraPDF-Prüfung des PDF/A-3. Kein Empfang von E-Rechnungen im Einkauf.
- EDIFACT/LVS: nur Datei-Upload/-Download und REST, keine Übertragungsstrecke (AS2/X.400/SFTP), Adapter vorbereitet.
- Tourenplanung ohne Routenoptimierung/Kartendienst (Sortierung nach PLZ bzw. Luftlinie), keine Versanddienstleister-Etiketten.
- Fibu: keine Steuerberater-Abnahme, kein ELSTER-Versand, keine Anlagenbuchhaltung/AfA, keine Kostenrechnung,
  Bilanz/GuV in vereinfachter Gliederung, nur Euro, Mahnwesen ohne Serienversand per E-Mail.
- Produktion: keine Kapazitätsplanung/Plantafel, keine Materialreservierung (Verfügbarkeit gegen freien Bestand
  zum Prüfzeitpunkt), nur Zeitakkord (kein Geldakkord/Gruppenakkord), Akkordauswertung ist keine
  Lohnabrechnung.
- Lieferantenspezifische Preise/Artikelnummern werden je Bestellposition erfasst, es gibt noch keine
  Lieferanten-Preisliste; Bestellvorschlag nur aus Meldebestand (noch nicht aus Stücklistenbedarf).
- Bestandsbewertung nur gleitender Durchschnitt (kein FIFO/Niederstwert).
- Ein Mandant (Firma) pro Installation.
- Kontenrahmen SKR03/SKR04 als erweiterter Grundbestand (kein vollständiger DATEV-Kontenrahmen).
- Docker-Compose-Setup ungetestet (siehe oben).

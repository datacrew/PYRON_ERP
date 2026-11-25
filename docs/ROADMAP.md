# Roadmap – Phasen 2 bis 6 (alle erledigt)

Die Aufwände sind **grobe Schätzungen** in Personentagen (PT) für eine erfahrene Django-Entwicklerin bzw.
einen erfahrenen Django-Entwickler. Enthalten sind Umsetzung, Tests und Doku, **nicht** enthalten sind
Fachkonzeption mit den Abteilungen, Datenübernahme aus Altsystemen, Schulung und Abstimmung mit
Steuerberatung. Realistisch sind ±30 %, je nach Tiefe der Anforderungen.

| Phase | Inhalt | Aufwand (grob) |
|---|---|---|
| 1 | Fundament (abgeschlossen): core, Stammdaten, Belege/GoBD/PDF, UI, API | – |
| 2 | Einkauf + Wareneingang + Lagerbestand (**abgeschlossen**) | – |
| 3 | Produktion mit Zeiterfassung und Akkord (**abgeschlossen**, vorgezogen) | – |
| 4 | Finanzbuchhaltung (**abgeschlossen**) | – |
| 5 | Vertrieb, CRM, E-Rechnung, EDIFACT, Versand (**abgeschlossen**) | – |
| 6 | Reporting und Feinschliff (**abgeschlossen**) | – |
| | Betrieb/Härtung für den Produktivbetrieb (siehe unten) | 15–25 PT |
| | Produktion-Ausbau (Kapazitätsplanung, Reservierung, Lohnexport-Formate) | 10–20 PT |

Die Reihenfolge ist ein Vorschlag: Einkauf/Lager zuerst, weil Produktion und Versand Bestände brauchen;
Die Produktion wurde auf Wunsch vor Fibu und Vertrieb gezogen und ist als **Phase 3** umgesetzt. Die bisherigen
Abschnitte „Phase 3 – Vertrieb …“ und „Phase 5 – Produktion“ sind entsprechend umnummeriert.

---

## Phase 2 – Einkauf, Wareneingang, Lager ✅ abgeschlossen
Umgesetzt (Details im README, Abschnitt „Phase 2: Warenfluss“):
- [x] Lagerorte/Lagerplätze, Bestand je Artikel × Ort × Platz × Charge × Bestandsart (frei/gesperrt)
- [x] Bewegungsjournal (Zugang, Abgang, Umbuchung, Inventurkorrektur), unveränderlich per Code und DB-Trigger
- [x] Gleitender Durchschnittspreis, Bestandswert, Mindest-/Meldebestand mit Hinweis und Event `bestand.unterschritten`
- [x] Chargenverwaltung (Pflicht bei `chargenpflicht`, Lieferant, Lieferantencharge, MHD, Herstelldatum)
- [x] Einfache Inventur (Zählliste je Lagerort, Differenzbuchung)
- [x] Einkauf: Bestellanforderung, Anfrage → Bestellung, Nummernkreise, Status, Storno, Bestell-PDF,
      Liefertermin-Übersicht, Bestellvorschlag aus Meldebestand
- [x] Wareneingang gegen Bestellung (Teil-/Rest-/Überlieferung, Preisdifferenzen), Chargenerfassung,
      Qualitätsprüfung mit Sperrbestand, Freigabe/Reklamation/Rücksendung, WE-PDF
- [x] Events `wareneingang.gebucht`, `wareneingang.qs_entschieden`, `bestellung.*`, `lager.bewegung`, `inventur.gebucht`
- [x] Web-UI, Rechte je Modul/Aktion, Dashboard-Kennzahlen, REST-API, Demodaten, 23 neue Tests

Bewusst verschoben:
- Eingangsrechnungsprüfung (Bestellung ↔ Wareneingang ↔ Rechnung) und WE/RE-Verrechnung → **Phase 4** (Fibu
  abonniert `wareneingang.gebucht`)
- Lieferanten-Preislisten/-Artikelnummern als Stammdaten, Bestellvorschlag aus Stücklisten-Rohstoffbedarf
  → offen (Produktion-Ausbau)
- Rückverfolgbarkeit Charge → Verwendung in Fertigungsaufträgen: Daten liegen seit Phase 3 vor
  (Materialentnahme je Charge, Fertigungscharge); eine eigene Rückverfolgungsansicht fehlt noch

## Phase 3 – Produktion mit Betriebsdatenerfassung und Akkord ✅ abgeschlossen
Umgesetzt (Details im README, Abschnitt „Phase 3: Produktion“):
- [x] Stammdaten: Arbeitsplätze mit Stundensatz/Kostenstelle, Mitarbeiter (Personal-/Ausweisnummer, Lohngruppe,
      Stundenlohn, Akkordrichtsatz, Benutzer), Arbeitsgang mit Arbeitsplatz und Lohnart Akkord/Zeitlohn
- [x] Fertigungsaufträge (Nummernkreis `FA-`), Statusmodell, Freigabe mit Kopie von Material und Arbeitsgängen,
      Fehlteilliste gegen freien Bestand, optional Unteraufträge für Halbzeuge, Storno mit Grund
- [x] Laufkarte/Arbeitsbegleitschein als PDF mit Code-128-Barcodes und Klartext
- [x] BDE: Kommen/Gehen, Auftragsstempelung (Rüsten/Fertigen, Unterbrechung, Ende), keine Überlappung
      (Code + Partial-Unique-Constraint), Stempel-Terminal für Tastatur/Scanner, Korrektur/Nachtrag mit Recht
      und Audit-Log
- [x] Rückmeldungen Gut/Ausschuss (mit Grund)/Nacharbeit
- [x] Zeitakkord: Vorgabezeit, Leistungsgrad, Akkordverdienst, Garantie (Stundenlohn/Mindestlohn), Zeitlohn
      separat; Auswertung je Mitarbeiter/Auftrag/Zeitraum als Liste, PDF und CSV
- [x] Materialentnahme chargengenau, Fertigmeldung mit Fertigungscharge zu Herstellkosten, Nachkalkulation
- [x] Events `produktion.*`, Dashboard/Kacheln, Rechte `produktion`/`bde`/`akkord`, REST-API, Demodaten,
      18 neue Tests (gesamt 66)

Offen / bewusst verschoben:
- Kapazitätsplanung je Arbeitsplatz (Plantafel), Materialreservierung bei Freigabe
- Geld- und Gruppenakkord, Prämienlohn; Exportformate für konkrete Lohnprogramme (z. B. DATEV LODAS)
- RFID-Terminals (heute: Tastatur/Barcodescanner bzw. REST-API für externe Terminals)
- Fibu-Buchung von Materialverbrauch/Fertigmeldung → **Phase 4** (Events sind vorhanden)
- Hinweis: Zeiterfassung und Akkord sind mitbestimmungspflichtig (Betriebsrat, Betriebsvereinbarung) und
  datenschutzrelevant.

## Phase 4 – Finanzbuchhaltung (**erledigt**, siehe README „Phase 4: Finanzbuchhaltung“, 20 neue Tests, gesamt 86)
Offen/Ausbau: Anlagenbuchhaltung, Kostenrechnung, ELSTER, E-Mail-Mahnversand, Steuerberater-Abnahme.

Ursprünglicher Umfang:
- Doppelte Buchführung: Buchungsjournal (unveränderbar, Festschreibung je Periode), Soll/Haben,
  Debitoren/Kreditoren (OP-Verwaltung), Steuerschlüssel, Buchungsperioden und Jahresabschluss-Sperre
- Automatische Buchung festgeschriebener Belege über das bestehende Event `beleg.festgeschrieben`
  (Abonnent `apps/fibu/ereignisse.py` existiert bereits)
- Eingangsrechnungsprüfung (Bestellung ↔ Wareneingang ↔ Rechnung) und WE/RE-Verrechnung über das Event
  `wareneingang.gebucht` (aus Phase 2 verschoben), Bestandsbuchung zum gleitenden Durchschnittspreis
- Buchung von Materialverbrauch und Fertigmeldungen (Bestandsveränderung/Herstellkosten) über die Events
  `produktion.material_entnommen` und `produktion.fertiggemeldet` (aus Phase 3)
- Vollständiger Kontenrahmen SKR03/SKR04 (Import), Kontenzuordnung je Artikel/Partner
- Bankimport **CAMT.053/CAMT.054** und **MT940**, automatischer Zahlungsabgleich (Betrag, Referenz,
  Belegnummer) mit manueller Nachbearbeitung, SEPA-Zahlungs-/Lastschriftdateien (pain.001/pain.008)
- **Mahnwesen**: Mahnstufen, Mahngebühren/-zinsen, Mahnbriefe als PDF-Belege, Mahnsperren
- Auswertungen: Summen-/Saldenliste, BWA, **Bilanz und GuV**, Umsatzsteuer-Voranmeldung (Werte),
  DATEV-Export (Buchungsstapel)
- GoBD: Verfahrensdokumentation (Vorlage), Datenexport für Betriebsprüfung (GDPdU/IDEA-Beschreibungsstandard)

## Phase 5 – Vertrieb, CRM, E-Rechnung, EDIFACT, Versand (**erledigt**, siehe README „Phase 5: Vertrieb und Versand“, 21 neue Tests, gesamt 107)
Offen/Ausbau: KoSIT-/Schematron- und veraPDF-Validierung, E-Rechnungs-Empfang im Einkauf, EDI-Übertragung (AS2/Clearingstelle), Routenoptimierung/Kartenansicht, Versanddienstleister-Etiketten.

Ursprünglicher Umfang:
- CRM: Kontakthistorie, Aktivitäten/Wiedervorlagen, Verkaufschancen
- Belegfluss Angebot → Auftrag → Lieferschein → Rechnung (Übernahme von Positionen, Teilmengen),
  Preislisten, Kundenrabatte, Verfügbarkeitsprüfung
- **E-Rechnung**: ZUGFeRD 2.x / Factur-X (PDF/A-3 mit eingebettetem XML) und XRechnung (UBL/CII) inkl.
  Validierung (KoSIT-Validator); Leitweg-ID ist im Partnerstamm bereits vorhanden. Empfang von E-Rechnungen
  im Einkauf. (Pflicht zum Empfang seit 2025, Ausstellungspflicht B2B gestaffelt ab 2027/2028.)
- **EDIFACT** (ORDERS, DESADV, INVOIC) über Konverter/Clearingstelle; Mapping-Schicht, Protokoll der
  Nachrichten
- REST-API für Kunden-/Shopanbindung (Aufträge anlegen, Status abfragen) – Basis besteht bereits
- **Versand**: Versandaufträge, Packstücke, Tourenplanung (Touren/Fahrzeuge/Reihenfolge, zunächst manuell
  mit Kartenansicht, optional Routenoptimierung über externen Dienst), Etiketten/Versanddienstleister
- **LVS-Schnittstelle** (Lagerverwaltungssystem): Auftrags- und Rückmeldeschnittstelle (Datei/REST),
  Spezifikation abhängig vom eingesetzten LVS

## Phase 6 – Reporting und Feinschliff (**erledigt**, siehe README „Phase 6: Reporting", 12 neue Tests, gesamt 119)
- ✅ Bericht-Registry (`berichte.py` je Modul), 28 Standardberichte aus Vertrieb, Einkauf, Lager, Produktion,
  Fibu und Versand, Rechte: `reporting.lesen` + Leserecht Quellmodul
- ✅ Management-Cockpit mit KPI-Kacheln und Chart.js-Diagrammen (rechteabhängig)
- ✅ Export Excel (Summenformeln, deutsche Formate, Filterkopf), CSV (Semikolon, UTF-8 BOM), PDF (Querformat)
- ✅ Berichtsvarianten je Benutzer mit Favoriten im Dashboard, REST-API `/api/v1/berichte/`
- ✅ Feinschliff: Reporting-Platzhalter ersetzt, globale Suche in der Kopfzeile, Rolle Lager/Fertigungsleitung mit Reporting-Leserecht
- offen: geplante Berichte per E-Mail, Berichtsdesigner

## Empfohlene nächste Schritte für den Produktivbetrieb
1. **Eigener Server mit fester PostgreSQL-Datenbank und Backups** (tägliche Sicherung + regelmäßiger
   Wiederherstellungstest); die Demo-Umgebung ist flüchtig.
2. **Docker-Test**: `docker compose` wurde bisher nicht ausgeführt – auf dem Zielsystem testen.
3. **E-Rechnung validieren**: KoSIT-Validator (XRechnung-Schematron) und veraPDF (PDF/A-3) in die Prüfung aufnehmen.
4. **Steuerberater-Abnahme** der Fibu (Kontierung, USt-VA, DATEV-Export, Verfahrensdokumentation GoBD).
5. **Echte EDI-/LVS-Übertragungswege** (AS2/SFTP, Abstimmung mit Kunden und LVS-Anbieter).
6. **Betriebsrat und Datenschutz** für Zeiterfassung/Akkord (Betriebsvereinbarung, DSFA, Löschfristen).
7. **Lasttest** mit realistischen Datenmengen und Benutzerzahlen; danach Produktiv-Setup (Gunicorn + Nginx, HTTPS,
   Monitoring, Hintergrundjobs).

## Weitere Härtung (optional)
- Mehrmandantenfähigkeit (falls benötigt), Feld-/Datensatzrechte, Zwei-Faktor-Anmeldung, Passwortrichtlinie
- Betrieb: Produktiv-Setup (Gunicorn + Nginx, HTTPS), Backups mit Wiederherstellungstest, Monitoring/Logging,
  Hintergrundjobs (z. B. Celery/RQ für Importe, E-Mails, PDF-Massendruck)
- Lasttests, Sicherheitsprüfung (OWASP), Revisionsfestigkeit der Archivablage (WORM-Speicher o. Ä.)

---

## Querschnitt (in jeder Phase)
- Tests für jede neue Funktion; die bestehende Testsuite muss grün bleiben
- Neue Module registrieren sich über `erp_modul` in `apps.py` und nutzen Rechte, Nummernkreise, Audit-Log,
  Belege und Events aus Phase 1
- Migrationen abwärtskompatibel, Doku (`README.md`, `docs/DATENMODELL.md`) fortschreiben

## SEPA-Schnittstelle (Branch `feature/sepa-schnittstelle`, in Arbeit)

Umgesetzt (Grundlage):
- Prüfung von IBAN (Prüfziffer mod 97, Länderlänge), BIC und Gläubiger-ID (`apps/core/bankdaten.py`), aktiv an Geschäftspartner, Bankkonto und Mandant (neues Feld Gläubiger-ID).
- Datenmodell `apps/fibu_sepa`: SEPA-Mandate (CORE/B2B, automatische Sequenz FRST/RCUR/OOFF/FNAL, Verfall nach 36 Monaten), Zahlungsläufe mit Posten (nach Export im ORM eingefroren), Rücklastschriften.
- XML-Erzeugung pain.001.001.09 und pain.008.001.08 mit SEPA-Zeichensatzkonvertierung, Betrags- und Längengrenzen; XSD-Validierung gegen die gebündelten ISO-20022-Basisschemata (`apps/fibu_sepa/xsd/`). DK-spezifische Einschränkungen (Anlage 3) werden nur im Code geprüft, das DK-XSD von ebics.de ist nicht gebündelt.- Oberfläche unter `/fibu/sepa/` (Modul `fibu_sepa` im Rechtesystem und Menü, Rolle Buchhaltung): SEPA-Mandate (Liste mit Filter, Anlegen/Bearbeiten, Nutzungshistorie, Sperren/Beenden), Zahlungsläufe (Überweisungs-/Lastschriftvorschlag, Posten entfernen, Freigabe und Export mit Recht „freigeben“, XML-Download, Storno mit Bestätigung). IBAN/BIC im Geschäftspartner-Formular, Gläubiger-ID in den Firmendaten.

Offen:
- CAMT-Sammlerabgleich und Rücklastschrift-Verarbeitung in fibu_bank, DB-Trigger für exportierte Läufe.
- Vorabankündigung in Rechnungs-PDF und ZUGFeRD-Zahlungsart 59.
- Demodaten (Mandate, Gläubiger-ID DE98ZZZ09999999999), Doku in README/DATENMODELL.
- Keine EBICS/FinTS-Direktübertragung (Weg: Datei-Upload ins Online-Banking); bankspezifische Tests mit echten Bankportalen.

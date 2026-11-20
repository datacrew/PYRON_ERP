# Roadmap – Phasen 2 bis 6

Die Aufwände sind **grobe Schätzungen** in Personentagen (PT) für eine erfahrene Django-Entwicklerin bzw.
einen erfahrenen Django-Entwickler. Enthalten sind Umsetzung, Tests und Doku, **nicht** enthalten sind
Fachkonzeption mit den Abteilungen, Datenübernahme aus Altsystemen, Schulung und Abstimmung mit
Steuerberatung. Realistisch sind ±30 %, je nach Tiefe der Anforderungen.

| Phase | Inhalt | Aufwand (grob) |
|---|---|---|
| 1 | Fundament (abgeschlossen): core, Stammdaten, Belege/GoBD/PDF, UI, API | – |
| 2 | Einkauf + Wareneingang + Lagerbestand (**abgeschlossen**) | – |
| 3 | Vertrieb, CRM, E-Rechnung, EDIFACT, Versand | 40–55 PT |
| 4 | Finanzbuchhaltung | 45–60 PT |
| 5 | Produktion mit Zeiterfassung und Akkord | 40–55 PT |
| 6 | Reporting, Betrieb, Härtung | 20–30 PT |
| | **Summe offene Phasen 3–6** | **ca. 145–200 PT** |

Die Reihenfolge ist ein Vorschlag: Einkauf/Lager zuerst, weil Produktion und Versand Bestände brauchen;
Fibu vor Produktion, weil festgeschriebene Rechnungen sonst nicht gebucht werden. Produktion kann bei Bedarf
vor die Fibu gezogen werden.

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
  → mit **Phase 5** (Produktion)
- Rückverfolgbarkeit Charge → Verwendung in Fertigungsaufträgen → **Phase 5**; Lieferant → Charge ist vorhanden

## Phase 3 – Vertrieb, CRM, E-Rechnung, EDIFACT, Versand (40–55 PT)
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

## Phase 4 – Finanzbuchhaltung (45–60 PT)
- Doppelte Buchführung: Buchungsjournal (unveränderbar, Festschreibung je Periode), Soll/Haben,
  Debitoren/Kreditoren (OP-Verwaltung), Steuerschlüssel, Buchungsperioden und Jahresabschluss-Sperre
- Automatische Buchung festgeschriebener Belege über das bestehende Event `beleg.festgeschrieben`
  (Abonnent `apps/fibu/ereignisse.py` existiert bereits)
- Eingangsrechnungsprüfung (Bestellung ↔ Wareneingang ↔ Rechnung) und WE/RE-Verrechnung über das Event
  `wareneingang.gebucht` (aus Phase 2 verschoben), Bestandsbuchung zum gleitenden Durchschnittspreis
- Vollständiger Kontenrahmen SKR03/SKR04 (Import), Kontenzuordnung je Artikel/Partner
- Bankimport **CAMT.053/CAMT.054** und **MT940**, automatischer Zahlungsabgleich (Betrag, Referenz,
  Belegnummer) mit manueller Nachbearbeitung, SEPA-Zahlungs-/Lastschriftdateien (pain.001/pain.008)
- **Mahnwesen**: Mahnstufen, Mahngebühren/-zinsen, Mahnbriefe als PDF-Belege, Mahnsperren
- Auswertungen: Summen-/Saldenliste, BWA, **Bilanz und GuV**, Umsatzsteuer-Voranmeldung (Werte),
  DATEV-Export (Buchungsstapel)
- GoBD: Verfahrensdokumentation (Vorlage), Datenexport für Betriebsprüfung (GDPdU/IDEA-Beschreibungsstandard)

## Phase 5 – Produktion (40–55 PT)
- Fertigungsaufträge aus Stückliste + Arbeitsplan (Versionierung besteht bereits), Materialreservierung und
  -entnahme (Bestandsbuchung aus Phase 2), Rückmeldung von Stückzahlen (Gut/Ausschuss)
- **Stempelzeiten**: Kommen/Gehen und Auftrags-/Arbeitsgangstempelung, Terminal-Oberfläche
  (Touch/Barcode/RFID), Korrekturen mit Protokoll
- **Akkord**: Vorgabezeiten aus Arbeitsgängen (Rüst-/Stückzeit, Lohngruppe, Akkordfähigkeit sind bereits im
  Modell), Leistungsgrad, Akkordlohnberechnung, Export an die Lohnabrechnung
- Kapazitätsplanung je Arbeitsplatz (Grobplanung, Plantafel), Nachkalkulation Soll/Ist
- Hinweis: Zeiterfassung und Akkord sind mitbestimmungspflichtig (Betriebsrat, Betriebsvereinbarung) und
  datenschutzrelevant – früh einbinden.

## Phase 6 – Reporting, Betrieb, Härtung (20–30 PT)
- Reporting-Modul: Kennzahlen-Dashboard (Umsatz, Deckungsbeitrag, Bestände, OP, Produktivität),
  Listen mit Filtern und Excel/CSV-Export, geplante Berichte per E-Mail
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

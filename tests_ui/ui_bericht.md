# Oberflächentest (Playwright) – ERP Mittelstand

Automatisch erzeugt von `tests_ui/ui_crawl.py` (Teil A) und `tests_ui/ui_klickpfade.py` (Teil B). Teil C (Korrekturen und offene Punkte) wird von Hand gepflegt.

## Teil A – Automatischer Crawl

Erstellt: 21.11.2026 08:08 · Dauer 150 s · Browser: Chromium headless (Playwright), Viewport 1366×900, zusätzlich 390×844 je Seitentyp

| Rolle | besuchte Seiten | Downloads geprüft | hoch | mittel | niedrig | info |
|---|---|---|---|---|---|---|
| admin | 282 | 99 | 0 | 0 | 7 | 14 |
| einkauf | 85 | 21 | 0 | 0 | 0 | 2 |
| vertrieb | 152 | 54 | 0 | 0 | 3 | 7 |
| produktion | 87 | 3 | 0 | 0 | 0 | 2 |
| lager | 138 | 51 | 0 | 0 | 2 | 8 |
| buchhaltung | 150 | 67 | 0 | 0 | 2 | 8 |
| gast | 178 | 95 | 0 | 0 | 5 | 13 |

### Gefundene Probleme (hoch/mittel/niedrig)

| Schwere | Art | URL | Rolle(n) | Detail |
|---|---|---|---|---|
| niedrig | Horizontales Überlaufen bei 390 px (Mobil) | `/fibu/bank/` | admin, buchhaltung, gast | scrollWidth 416 > 390; a.btn |
| niedrig | Horizontales Überlaufen bei 390 px (Mobil) | `/fibu/bank/?status=OFFEN` | admin, buchhaltung, gast | scrollWidth 416 > 390; a.btn |
| niedrig | Horizontales Überlaufen bei 390 px (Mobil) | `/vertrieb/auftraege/3/` | admin, gast, lager, vertrieb | scrollWidth 393 > 390; div.card-body, thead.table-light, tr. |
| niedrig | Horizontales Überlaufen bei 390 px (Mobil) | `/vertrieb/edi/4/` | admin, gast, vertrieb | scrollWidth 393 > 390; div.card-body |
| niedrig | Horizontales Überlaufen bei 390 px (Mobil) | `/vertrieb/lieferscheine/5/` | admin, gast, lager, vertrieb | scrollWidth 393 > 390; div.card-body, thead.table-light, tr. |
| niedrig | Horizontales Überlaufen bei 390 px (Mobil) | `/verwaltung/rollen/1/` | admin | scrollWidth 393 > 390; thead.table-light, tr., th. |
| niedrig | Horizontales Überlaufen bei 390 px (Mobil) | `/verwaltung/rollen/neu/` | admin | scrollWidth 393 > 390; thead.table-light, tr., th. |

### Hinweise (info): 54 leere Tabellen in Seiten ohne Datenerwartung (z. B. gefilterte Listen, Rollen ohne Daten) – siehe crawl_ergebnis.json

### Screenshots

- admin: [dashboard](screenshots/dashboard.png) – `/`
- admin: [cockpit](screenshots/cockpit.png) – `/reporting/cockpit/`
- admin: [reporting_liste](screenshots/reporting_liste.png) – `/reporting/`
- admin: [reporting_bericht](screenshots/reporting_bericht.png) – `/reporting/bericht/vertrieb.umsatz_kunde/?von=2026-01-01&bis=2026-12-31`
- admin: [stammdaten_liste](screenshots/stammdaten_liste.png) – `/stammdaten/artikel/`
- admin: [stammdaten_formular](screenshots/stammdaten_formular.png) – `/stammdaten/artikel/neu/`
- admin: [belege_liste](screenshots/belege_liste.png) – `/belege/`
- admin: [belege_formular](screenshots/belege_formular.png) – `/belege/neu/`
- admin: [lager_liste](screenshots/lager_liste.png) – `/lager/`
- admin: [lager_formular](screenshots/lager_formular.png) – `/lager/umbuchen/`
- admin: [einkauf_liste](screenshots/einkauf_liste.png) – `/einkauf/bestellungen/`
- admin: [einkauf_formular](screenshots/einkauf_formular.png) – `/einkauf/bestellungen/neu/`
- admin: [wareneingang_liste](screenshots/wareneingang_liste.png) – `/wareneingang/liste/`
- admin: [produktion_liste](screenshots/produktion_liste.png) – `/produktion/auftraege/`
- admin: [produktion_formular](screenshots/produktion_formular.png) – `/produktion/auftraege/neu/`
- admin: [stempelterminal](screenshots/stempelterminal.png) – `/produktion/terminal/`
- admin: [fibu_liste](screenshots/fibu_liste.png) – `/fibu/buchungen/`
- admin: [fibu_formular](screenshots/fibu_formular.png) – `/fibu/buchungen/neu/`
- admin: [vertrieb_liste](screenshots/vertrieb_liste.png) – `/vertrieb/auftraege/`
- admin: [vertrieb_formular](screenshots/vertrieb_formular.png) – `/vertrieb/angebote/neu/`
- admin: [versand_liste](screenshots/versand_liste.png) – `/versand/touren/`
- admin: [versand_formular](screenshots/versand_formular.png) – `/versand/touren/bilden/`
- admin: [verwaltung_liste](screenshots/verwaltung_liste.png) – `/verwaltung/benutzer/`
- admin: [verwaltung_formular](screenshots/verwaltung_formular.png) – `/verwaltung/rollen/neu/`
- admin: [suche](screenshots/suche.png) – `/suche/?q=M%C3%BCller`
- Cockpit-Diagramme (Chart.js-Instanzen): chartUmsatz: gerendert, 12 Werte; chartOp: gerendert, 5 Werte; chartKunden: gerendert, 3 Werte
- admin: [dashboard_mobil](screenshots/dashboard_mobil.png) – `/`
- admin: [cockpit_mobil](screenshots/cockpit_mobil.png) – `/reporting/cockpit/`
- einkauf: [einkauf_dashboard](screenshots/einkauf_dashboard.png) – `/`
- vertrieb: [vertrieb_dashboard](screenshots/vertrieb_dashboard.png) – `/`
- produktion: [produktion_dashboard](screenshots/produktion_dashboard.png) – `/`
- lager: [lager_dashboard](screenshots/lager_dashboard.png) – `/`
- buchhaltung: [buchhaltung_dashboard](screenshots/buchhaltung_dashboard.png) – `/`
- gast: [gast_dashboard](screenshots/gast_dashboard.png) – `/`

## Teil B – Klickpfade (21.11.2026 07:08, 10 s)

Rolle admin, Chromium headless. **26 von 26 Schritten erfolgreich.** Die Klickpfade verändern Daten – anschließend wurden die Demodaten neu geladen.

| Pfad | Schritt | Ergebnis | Details | Screenshot |
|---|---|---|---|---|
| Anmeldung | Anmelden als admin | ✅ ok | Angemeldet als admin | `screenshots/pfad_01_anmelden_als_admin.png` |
| 1 Artikel | Artikel anlegen | ✅ ok | Artikel UI-070813 angelegt (chargenpflichtig) und in der Liste gefunden | `screenshots/pfad_02_artikel_anlegen.png` |
| 2 Bestellung | Bestellung anlegen | ✅ ok | Bestellung (Entwurf) bei GP-00004 Normteile Becker KG mit 100 × UI-070813 angelegt | `screenshots/pfad_03_bestellung_anlegen.png` |
| 2 Bestellung | Bestellung freigeben | ✅ ok | Freigegeben, Nummer EK-2026-00003; Bestellung freigegeben – Nummer EK-2026-00003. | `screenshots/pfad_04_bestellung_freigeben.png` |
| 2 Bestellung | Bestellung als PDF | ✅ ok | PDF 13402 Bytes (pfad_bestellung.pdf) | `screenshots/pfad_05_bestellung_als_pdf.png` |
| 3 Wareneingang | Wareneingang mit Charge buchen | ✅ ok | Wareneingang mit Charge CH-UI-070813 gebucht, QS-Prüfung offen; Wareneingang WEB-2026-00002 gebucht. | `screenshots/pfad_06_wareneingang_mit_charge_buchen.png` |
| 3 Wareneingang | QS freigeben | ✅ ok | QS freigegeben; UI-070813: freigegeben – Bestand ist verfügbar. | `screenshots/pfad_07_qs_freigeben.png` |
| 4 Fertigungsauftrag | FA anlegen | ✅ ok | FA über 5 × F-5000 Lagerbock LB-200 angelegt | `screenshots/pfad_08_fa_anlegen.png` |
| 4 Fertigungsauftrag | FA freigeben | ✅ ok | FA-2026-00005 freigegeben (Material reserviert, Arbeitsgänge erzeugt); FA-2026-00005 freigegeben – Material und Arbeitsgänge übernommen. | `screenshots/pfad_09_fa_freigeben.png` |
| 4 Fertigungsauftrag | Laufkarte als PDF | ✅ ok | Laufkarte-PDF 18200 Bytes, Arbeitsgang-Codes: FA-2026-00005-10, FA-2026-00005-20, FA-2026-00005-30, FA-2026-00005-40; 0 eingebettete Bilder (Barcodes als Vektor/SVG werden hier nicht gezählt); Seite 1 als pfad_laufkarte-1.png gerendert | `screenshots/pfad_10_laufkarte_als_pdf.png` |
| 5 Stempelterminal | Kommen | ✅ ok | Kommen: Willkommen, Tobias Wagner – Kommen 08:08 Uhr. | `screenshots/pfad_11_kommen.png` |
| 5 Stempelterminal | Start Arbeitsgang | ✅ ok | Start FA-2026-00005-10: Tobias Wagner: Fertigen FA-2026-00005-10 „Bereitstellen & Ausrichten“ gestartet. | `screenshots/pfad_12_start_arbeitsgang.png` |
| 5 Stempelterminal | Ende mit Rückmeldung | ✅ ok | Ende mit Rückmeldung 5 Gut: Tobias Wagner: FA-2026-00005-10 ende nach 0 min – gemeldet 5 gut, 0 Ausschuss. | `screenshots/pfad_13_ende_mit_r_ckmeldung.png` |
| 5 Stempelterminal | Gehen | ✅ ok | Gehen: Auf Wiedersehen, Tobias Wagner – Gehen 08:08 Uhr. | `screenshots/pfad_14_gehen.png` |
| 5 Stempelterminal | Rückmeldungsliste | ✅ ok | Rückmeldung zu FA-2026-00005 in der Rückmeldungsliste sichtbar | `screenshots/pfad_15_r_ckmeldungsliste.png` |
| 6 Vertrieb | Angebot anlegen | ✅ ok | Angebot AN-2026-00003 für GP-00001 Müller Maschinenbau GmbH angelegt und freigegeben | `screenshots/pfad_16_angebot_anlegen.png` |
| 6 Vertrieb | Auftrag aus Angebot | ✅ ok | Auftrag aus Angebot erzeugt und bestätigt (AB); Auftragsbestätigung AB-2026-00005 erstellt und festgeschrieben. | `screenshots/pfad_17_auftrag_aus_angebot.png` |
| 6 Vertrieb | Lieferschein und Warenausgang | ✅ ok | Lieferschein angelegt und Warenausgang gebucht; Warenausgang gebucht, Lieferschein LS-2026-00005 festgeschrieben. | `screenshots/pfad_18_lieferschein_und_warenausgang.png` |
| 6 Vertrieb | Rechnung erstellen | ✅ ok | Rechnung erstellt (/vertrieb/rechnungen/25/); Rechnung RE-2026-00009 erstellt und festgeschrieben. | `screenshots/pfad_19_rechnung_erstellen.png` |
| 6 Vertrieb | Rechnung festschreiben | ✅ ok | Rechnung RE-2026-00009 GoBD-konform festgeschrieben (beim Erstellen automatisch); Belegseite zeigt Status Festgeschrieben | `screenshots/pfad_20_rechnung_festschreiben.png` |
| 6 Vertrieb | ZUGFeRD-PDF | ✅ ok | ZUGFeRD-PDF 37134 Bytes, Anhang: factur-x.xml | `screenshots/pfad_21_zugferd_pdf.png` |
| 7 Bank | Kontoauszug CAMT.053 hochladen | ✅ ok | CAMT.053 importiert; BI-2026-0002: 4 Umsätze importiert, 0 Dubletten übersprungen. | `screenshots/pfad_22_kontoauszug_camt_053_hochladen.png` |
| 7 Bank | Umsatz zuordnen | ✅ ok | Umsatz /fibu/bank/umsatz/10/ dem OP zugeordnet (RE-2026-00005 Müller Maschinenbau GmbH 16.12.2026 9.174,90 € 183,50 € 110 Pkt. Belegnummer RE-2026-00005 im Verwendungsz); Zugeordnet und gebucht (FB-2026-000029). | `screenshots/pfad_23_umsatz_zuordnen.png` |
| 8 Mahnwesen | Mahnlauf-Vorschlag | ✅ ok | Vorschlagslauf /fibu/mahnwesen/lauf/2/ mit 3 Zeile(n) (nicht ausgeführt), Mahnungs-PDF 13973 Bytes | `screenshots/pfad_24_mahnlauf_vorschlag.png` |
| 9 Reporting | Bericht mit Filter, Excel- und PDF-Export | ✅ ok | Filter 2026-01-01 bis heute, Kunde GP-00001 Müller Maschinenbau GmbH: 1 Zeile(n); Excel 5607 Bytes, PDF 11343 Bytes | `screenshots/pfad_25_bericht_mit_filter_excel_und_pdf_export.png` |
| 10 Suche | Globale Suche | ✅ ok | „Müller“: 1 verlinkte Treffer; Rechnungsnummer RE-2026-00009 gefunden | `screenshots/pfad_26_globale_suche.png` |

**Serverfehler (HTTP ≥ 500) während der Klickpfade:** keine

**JavaScript-Fehler während der Klickpfade:** keine

## Teil C – Korrekturen und offene Punkte (3 Runden)

**Runde 1 (vor den Korrekturen):** 1 179 besuchte Seiten, 0 hoch, 286 mittel, 1 120 niedrig. Klickpfade 22/23.

| Befund (Runde 1) | Schwere | Korrektur |
|---|---|---|
| 403-Seiten englisch „403 Forbidden“ ohne Layout (247×) | mittel | `templates/403.html`, `404.html` (deutsch, mit Layout), `500.html` (eigenständig) |
| Links trotz fehlendem Recht: „Touren bilden“ (Kachel + Reiter), „Verkaufschance“-Knopf, OP-Links in der Rechnung, Bestell-Links im Wareneingang (10×) | mittel | per `hat_recht` ausgeblendet (versand/views.py, versand/_nav.html, vertrieb/pipeline.html, vertrieb/rechnung.html, wareneingang/index.html + detail.html) |
| **Lieferschein aus Auftrag: leere Mengenfelder → „Keine Liefermengen angegeben“**, obwohl der Hinweis „leer = komplette offene Menge“ lautet (Klickpfad) | hoch (funktional) | vertrieb/views.py + services.py: leeres Feld = offene Menge; ungültige Eingabe → deutsche Fehlermeldung statt 500 |
| Laufkarte-PDF: Spalten „Ausschuss/Kürzel“ ragen über den rechten Rand (optisch geprüft) | mittel | produktion/pdf/laufkarte.html: Breiten von Barcode und Feldern verringert; Barcodes gut lesbar (gerendert und angesehen) |
| Mobil (390 px): feste Sidebar und Kopfzeile überlaufen auf allen Seiten (833×) | niedrig | base.html: Sidebar als Offcanvas-Menü (Menüknopf), Suche im Menü, Name ausgeblendet, Tabellen scrollen intern |
| Leerauswahl „---------“ in Auswahllisten (287×) | niedrig | apps/core/forms.py: „– bitte wählen –“ |
| EDIFACT-Download (`?download=1`), POST-Endpunkt Sammelrechnung (405) | Werkzeug | Erkennung im Crawl ergänzt (kein Fehler der Anwendung) |

**Optisch geprüft:** Cockpit inkl. 3 Diagrammen (Umsatz 12 Monate, OP-Altersstruktur, Top-10-Kunden mit 3 Kunden – korrekt; mobil sind die Achsenbeschriftungen des Top-10-Diagramms eng), Dashboard mobil, Laufkarte-PDF mit 5 Barcodes.

**Endstand (Runde 3):** 1 072 Seiten, **0 hoch, 0 mittel**, 19 niedrig, 54 info. Klickpfade **26/26 ok**, keine Serverfehler und keine JS-Fehler.

**Offen (niedrig/info):**
- Mobil 390 px: auf einzelnen Seiten noch 3–26 px Überlauf (Bank-Seite: Statusknöpfe; Auftrag/Lieferschein/EDI-Detail, Rollenformular: breite Tabellen/Karten).
- „Leere Tabelle“ (info): Inhalt fehlt fachlich zu Recht (z. B. keine Bankumsätze vor dem Import, keine Inventur, keine Touren in den Demodaten).
- Datumsfelder zeigen im headless Chromium das Format „mm/dd/yyyy“ (Browser-Locale, kein Fehler der Anwendung).

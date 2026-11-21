#!/usr/bin/env python3
"""Geskriptete Klickpfade (Playwright) für die zentralen Formular-Abläufe des ERP.

ACHTUNG: Die Klickpfade legen echte Daten an (Artikel, Bestellung, Wareneingang, Fertigungsauftrag,
Stempelungen, Angebot → Rechnung (festgeschrieben!), Bankimport, Mahnvorschlag). Danach die Demodaten
neu laden (siehe README, Abschnitt „Oberflächentest (Playwright)“).

Aufruf (Server muss laufen):  python3 tests_ui/ui_klickpfade.py [--basis http://localhost:3000]
Ergebnis: Teil B in tests_ui/ui_bericht.md, Screenshots tests_ui/screenshots/pfad_*.png
Exit-Code 1, wenn ein Schritt fehlgeschlagen ist.
"""
import argparse
import re
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ui_crawl import PROJEKT, SCREENSHOTS, bericht_zusammenfuegen  # noqa: E402

HEUTE = date.today()
KENN = time.strftime("%H%M%S")


class Abbruch(Exception):
    """Schritt fehlgeschlagen – Folgeschritte des Pfads werden übersprungen."""


class Klickpfade:
    def __init__(self, page, basis):
        self.page, self.basis = page, basis.rstrip("/")
        self.ergebnisse = []  # (pfad, schritt, ok, text, screenshot)
        self.serverfehler, self.jsfehler = [], []
        self.nr = 0
        self.daten = {}
        page.on("dialog", lambda d: d.accept())  # confirm()-Abfragen bestätigen
        page.on("response", lambda r: r.status >= 500 and self.serverfehler.append(f"{r.status} {r.url}"))
        page.on("pageerror", lambda e: self.jsfehler.append(f"{page.url}: {e}"))
        page.on("console", lambda m: m.type == "error" and self.jsfehler.append(f"{page.url}: {m.text}"))

    # ---------- Hilfen ----------
    def goto(self, pfad):
        r = self.page.goto(self.basis + pfad, wait_until="load", timeout=60000)
        if r and r.status >= 400:
            raise Abbruch(f"HTTP {r.status} bei {pfad}")

    def pfad(self):
        return self.page.url.replace(self.basis, "")

    def waehle(self, name, enthaelt=None, index=1):
        """Option eines Selects nach Textteil (oder n-te Option) wählen; gibt den Optionstext zurück."""
        sel = self.page.locator(f"main [name='{name}']").first
        optionen = sel.locator("option").all()
        if enthaelt:
            treffer = [o for o in optionen if enthaelt in o.inner_text()]
            if not treffer:
                raise Abbruch(f"Option „{enthaelt}“ in {name} nicht gefunden")
            o = treffer[0]
        else:
            o = optionen[index]
        sel.select_option(value=o.get_attribute("value"))
        return o.inner_text().strip()

    def klick(self, selektor, text=None):
        loc = self.page.locator(selektor)
        if text:
            loc = loc.filter(has_text=text)
        if loc.count() == 0:
            raise Abbruch(f"Schaltfläche {selektor} {text or ''} nicht gefunden")
        with self.page.expect_navigation(wait_until="load", timeout=60000):
            loc.first.click()

    def meldungen(self):
        txt = []
        for loc in self.page.locator("main .alert, main .invalid-feedback, main .text-danger.small").all():
            t = " ".join(loc.inner_text().split())
            if t:
                txt.append(t)
        return txt

    def pruefe_fehlerfrei(self):
        fehler = [" ".join(x.inner_text().split()) for x in
                  self.page.locator("main .alert-danger, main .invalid-feedback, main .text-danger.small, main .errorlist").all()]
        fehler = [f for f in fehler if f]
        if fehler or self.page.locator("main .is-invalid").count():
            raise Abbruch("Formular-/Ablauffehler: " + (" | ".join(fehler) or "Feld als ungültig markiert")[:300])

    def erfolg(self):
        t = [" ".join(x.inner_text().split()) for x in self.page.locator("main .alert").all()]
        return t[0][:160] if t else ""

    def download(self, url, name, erwartet):
        r = self.page.request.get(self.basis + url, timeout=90000)
        body = r.body()
        if r.status != 200 or not body.startswith(erwartet):
            raise Abbruch(f"Download {url}: HTTP {r.status}, {r.headers.get('content-type')}, Anfang {body[:12]!r}")
        ziel = SCREENSHOTS / name
        ziel.write_bytes(body)
        return ziel, len(body)

    def schritt(self, pfad, schritt, funktion):
        """Führt einen Schritt aus, erstellt einen Screenshot und protokolliert das Ergebnis."""
        self.nr += 1
        bild = f"pfad_{self.nr:02d}_{re.sub(r'[^a-z0-9]+', '_', schritt.lower())[:40].strip('_')}.png"
        sf_vorher = len(self.serverfehler)
        try:
            text = funktion() or "ok"
            ok = len(self.serverfehler) == sf_vorher
            if not ok:
                text += " – Serverfehler: " + "; ".join(self.serverfehler[sf_vorher:])
        except Abbruch as exc:
            ok, text = False, str(exc)
        except Exception as exc:  # noqa: BLE001 – Werkzeugfehler protokollieren statt abbrechen
            ok, text = False, f"Werkzeugfehler: {str(exc).splitlines()[0][:250]}"
        try:
            self.page.screenshot(path=str(SCREENSHOTS / bild), full_page=True)
        except Exception:  # noqa: BLE001
            bild = ""
        self.ergebnisse.append((pfad, schritt, ok, " ".join(str(text).split()), bild))
        print(("OK    " if ok else "FEHLER"), pfad, "–", schritt, "–", text[:160], flush=True)
        if not ok:
            raise Abbruch(text)

    def ablauf(self, name, schritte):
        try:
            for titel, funktion in schritte:
                self.schritt(name, titel, funktion)
        except Abbruch:
            pass

    # ---------- Anmeldung ----------
    def anmelden(self):
        self.goto("/anmelden/")
        self.page.fill("#id_username", "admin")
        self.page.fill("#id_password", "Admin!2026")
        self.klick("main form button")
        if "/anmelden/" in self.page.url:
            raise Abbruch("Anmeldung fehlgeschlagen")
        return "Angemeldet als admin"

    # ---------- 1. Artikel ----------
    def artikel_anlegen(self):
        nr = f"UI-{KENN}"
        self.goto("/stammdaten/artikel/neu/")
        p = self.page
        p.fill("main [name='artikelnummer']", nr)
        p.fill("main [name='bezeichnung']", "Oberflächentest Rohteil")
        self.waehle("art", "Rohstoff")
        self.waehle("einheit", index=1)
        self.waehle("steuersatz", "Regel")
        p.fill("main [name='einkaufspreis']", "12,50")
        p.fill("main [name='verkaufspreis']", "19,90")
        p.check("main [name='chargenpflicht']")
        self.klick("main form button", "Speichern")
        self.pruefe_fehlerfrei()
        if "/neu/" in self.page.url:
            raise Abbruch("Nach dem Speichern noch auf dem Formular")
        p.goto(self.basis + f"/stammdaten/artikel/?q={nr}")
        if nr not in p.inner_text("main"):
            raise Abbruch("Neuer Artikel nicht in der Liste")
        self.daten["artikel"] = nr
        return f"Artikel {nr} angelegt (chargenpflichtig) und in der Liste gefunden"

    # ---------- 2. Bestellung ----------
    def bestellung_anlegen(self):
        self.goto("/einkauf/bestellungen/neu/")
        p = self.page
        self.waehle("typ", "Bestellung")
        lief = self.waehle("lieferant", index=1)
        p.fill("main [name='liefertermin']", str(HEUTE + timedelta(days=7)))
        self.waehle("pos-0-artikel", self.daten.get("artikel", "R-1000"))
        p.fill("main [name='pos-0-menge']", "100")
        p.fill("main [name='pos-0-einzelpreis']", "12,50")
        self.klick("main form button", "Speichern")
        self.pruefe_fehlerfrei()
        m = re.search(r"/einkauf/bestellungen/(\d+)/", self.page.url)
        if not m:
            raise Abbruch(f"Keine Detailseite nach dem Speichern ({self.pfad()})")
        self.daten["bestellung"] = m.group(1)
        return f"Bestellung (Entwurf) bei {lief} mit 100 × {self.daten.get('artikel')} angelegt"

    def bestellung_freigeben(self):
        self.klick("main form[action$='/freigeben/'] button")
        self.pruefe_fehlerfrei()
        nr = re.search(r"EK-\d{4}-\d{5}", self.page.inner_text("main"))
        self.daten["bestellnr"] = nr.group(0) if nr else "?"
        return f"Freigegeben, Nummer {self.daten['bestellnr']}; {self.erfolg()}"

    def bestellung_pdf(self):
        datei, groesse = self.download(f"/einkauf/bestellungen/{self.daten['bestellung']}/pdf/", "pfad_bestellung.pdf", b"%PDF")
        return f"PDF {groesse} Bytes ({datei.name})"

    # ---------- 3. Wareneingang ----------
    def wareneingang(self):
        self.goto(f"/wareneingang/buchen/{self.daten['bestellung']}/")
        p = self.page
        p.fill("main [name='lieferschein_nr']", f"LS-UI-{KENN}")
        self.waehle("lagerort", "RL")
        p.fill("main [name='we-0-menge']", "100")
        p.fill("main [name='we-0-chargennummer']", f"CH-UI-{KENN}")
        p.fill("main [name='we-0-lieferantencharge']", f"L-{KENN}")
        if p.locator("main [name='we-0-lagerplatz'] option").count() > 1:
            self.waehle("we-0-lagerplatz", "RL/")
        p.check("main [name='we-0-qs_pruefen']")
        self.klick("main form button", "Wareneingang buchen")
        self.pruefe_fehlerfrei()
        if f"CH-UI-{KENN}" not in p.inner_text("main"):
            raise Abbruch(f"Charge nicht auf der Folgeseite ({self.pfad()})")
        return f"Wareneingang mit Charge CH-UI-{KENN} gebucht, QS-Prüfung offen; {self.erfolg()}"

    def qs_freigeben(self):
        p = self.page
        btn = p.locator("main button[formaction*='/qs/'][formaction$='/freigeben/']")
        if btn.count() == 0:
            raise Abbruch("Kein QS-Freigabe-Knopf auf der Wareneingangsseite")
        form = btn.first.locator("xpath=ancestor::form")
        bem = form.locator("[name='bemerkung']")
        if bem.count():
            bem.fill("Sichtprüfung i. O. (Oberflächentest)")
        with p.expect_navigation(wait_until="load"):
            btn.first.click()
        self.pruefe_fehlerfrei()
        if "Freigegeben" not in p.inner_text("main"):
            raise Abbruch("QS-Status nicht „Freigegeben“")
        return f"QS freigegeben; {self.erfolg()}"

    # ---------- 4. Fertigungsauftrag ----------
    def fa_anlegen(self):
        self.goto("/produktion/auftraege/neu/")
        p = self.page
        art = self.waehle("artikel", "F-5000")
        p.fill("main [name='menge']", "5")
        p.fill("main [name='termin']", str(HEUTE + timedelta(days=14)))
        self.klick("main form button", "Anlegen")
        self.pruefe_fehlerfrei()
        m = re.search(r"/produktion/auftraege/(\d+)/", self.page.url)
        if not m:
            raise Abbruch(f"Keine FA-Detailseite ({self.pfad()})")
        self.daten["fa"] = m.group(1)
        return f"FA über 5 × {art} angelegt"

    def fa_freigeben(self):
        self.klick("main form[action$='/freigeben/'] button")
        self.pruefe_fehlerfrei()
        nr = re.search(r"FA-\d{4}-\d{5}", self.page.inner_text("main"))
        self.daten["fanr"] = nr.group(0) if nr else ""
        return f"{self.daten['fanr']} freigegeben (Material reserviert, Arbeitsgänge erzeugt); {self.erfolg()}"

    def laufkarte(self):
        datei, groesse = self.download(f"/produktion/auftraege/{self.daten['fa']}/laufkarte/", "pfad_laufkarte.pdf", b"%PDF")
        subprocess.run(["pdftoppm", "-png", "-r", "110", "-f", "1", "-l", "1", str(datei),
                        str(SCREENSHOTS / "pfad_laufkarte")], check=False)
        text = subprocess.run(["pdftotext", "-layout", str(datei), "-"], capture_output=True, text=True).stdout
        codes = re.findall(r"FA-\d{4}-\d{5}-\d+", text)
        if codes:
            self.daten["ag_code"] = codes[0]
        bilder = subprocess.run(["pdfimages", "-list", str(datei)], capture_output=True, text=True).stdout
        n_bilder = max(0, len(bilder.strip().splitlines()) - 2)
        return (f"Laufkarte-PDF {groesse} Bytes, Arbeitsgang-Codes: {', '.join(dict.fromkeys(codes)) or 'keine im Text'}; "
                f"{n_bilder} eingebettete Bilder (Barcodes als Vektor/SVG werden hier nicht gezählt); "
                f"Seite 1 als pfad_laufkarte-1.png gerendert")

    # ---------- 5. Stempelterminal ----------
    def stempeln(self, aktion, code="", gut=""):
        p = self.page
        p.fill("#kennung", self.daten["pnr"])
        p.fill("#code", code)
        if gut:
            p.fill("main [name='gut']", gut)
        self.klick(f"main button[data-aktion='{aktion}']")
        fehler = p.locator("main .alert-danger")
        if fehler.count():
            raise Abbruch(f"{aktion}: " + " ".join(fehler.first.inner_text().split()))
        return self.erfolg()

    def terminal_kommen(self):
        self.goto("/produktion/terminal/")
        self.daten["pnr"] = "P1005"
        return "Kommen: " + self.stempeln("kommen")

    def terminal_start(self):
        code = self.daten.get("ag_code") or f"{self.daten['fanr']}-10"
        self.daten["ag_code"] = code
        return f"Start {code}: " + self.stempeln("start", code)

    def terminal_ende(self):
        return "Ende mit Rückmeldung 5 Gut: " + self.stempeln("ende", self.daten["ag_code"], "5")

    def terminal_gehen(self):
        return "Gehen: " + self.stempeln("gehen")

    def rueckmeldungen(self):
        self.goto("/produktion/rueckmeldungen/")
        if self.daten["fanr"] and self.daten["fanr"] not in self.page.inner_text("main"):
            raise Abbruch(f"Rückmeldung zu {self.daten['fanr']} nicht in der Liste")
        return f"Rückmeldung zu {self.daten['fanr']} in der Rückmeldungsliste sichtbar"

    # ---------- 6. Vertrieb ----------
    def angebot(self):
        self.goto("/vertrieb/angebote/neu/")
        p = self.page
        kunde = self.waehle("partner", "Müller")
        p.fill("main [name='betreff']", "Oberflächentest Angebot")
        self.waehle("pos-0-artikel", "F-5000")
        p.fill("main [name='pos-0-menge']", "2")
        if p.locator("main [name='freigeben']").count():
            p.check("main [name='freigeben']")
        self.klick("main form button", "Speichern")
        self.pruefe_fehlerfrei()
        m = re.search(r"/belege/(\d+)/", self.page.url)
        if not m:
            raise Abbruch(f"Keine Belegseite nach dem Speichern ({self.pfad()})")
        self.daten["angebot"] = m.group(1)
        nr = re.search(r"AN-\d{4}-\d+|AG-\d{4}-\d+", p.inner_text("main"))
        return f"Angebot {nr.group(0) if nr else '(Nummer?)'} für {kunde} angelegt und freigegeben"

    def auftrag(self):
        self.goto("/vertrieb/angebote/")
        self.klick(f"main form[action='/vertrieb/angebote/{self.daten['angebot']}/uebernehmen/'] button")
        self.pruefe_fehlerfrei()
        m = re.search(r"/vertrieb/auftraege/(\d+)/", self.page.url)
        if not m:
            raise Abbruch(f"Keine Auftragsseite nach Übernahme ({self.pfad()})")
        self.daten["auftrag"] = m.group(1)
        if self.page.locator("main form[action$='/bestaetigen/'] button").count():
            self.klick("main form[action$='/bestaetigen/'] button")
            self.pruefe_fehlerfrei()
        return f"Auftrag aus Angebot erzeugt und bestätigt (AB); {self.erfolg()}"

    def lieferschein(self):
        self.klick("main form[action$='/liefern/'] button")
        self.pruefe_fehlerfrei()
        m = re.search(r"/vertrieb/lieferscheine/(\d+)/", self.page.url)
        if not m:
            raise Abbruch(f"Keine Lieferscheinseite ({self.pfad()})")
        self.daten["lieferschein"] = m.group(1)
        self.klick("main form[action$='/buchen/'] button")
        self.pruefe_fehlerfrei()
        return f"Lieferschein angelegt und Warenausgang gebucht; {self.erfolg()}"

    def rechnung(self):
        self.klick("main form[action$='/rechnung/'] button")
        self.pruefe_fehlerfrei()
        m = re.search(r"/(?:vertrieb/rechnungen|belege)/(\d+)/", self.page.url)
        if not m:
            raise Abbruch(f"Keine Rechnungsseite ({self.pfad()})")
        self.daten["rechnung"] = m.group(1)
        return f"Rechnung erstellt ({self.pfad()}); {self.erfolg()}"

    def festschreiben(self):
        self.goto(f"/belege/{self.daten['rechnung']}/")
        if self.page.locator("main form[action$='/freigeben/'] button").count():
            self.klick("main form[action$='/freigeben/'] button")
            self.pruefe_fehlerfrei()
        art = "beim Erstellen automatisch"
        if self.page.locator("main form[action$='/festschreiben/'] button").count():
            self.klick("main form[action$='/festschreiben/'] button")
            self.pruefe_fehlerfrei()
            art = "per Klick"
        text = self.page.inner_text("main")
        nr = re.search(r"RE-\d{4}-\d+", text)
        self.daten["renr"] = nr.group(0) if nr else "?"
        if "Festgeschrieben" not in text and "festgeschrieben" not in text:
            raise Abbruch("Status „Festgeschrieben“ nicht sichtbar")
        return f"Rechnung {self.daten['renr']} GoBD-konform festgeschrieben ({art}); Belegseite zeigt Status Festgeschrieben"

    def zugferd(self):
        self.goto(f"/vertrieb/rechnungen/{self.daten['rechnung']}/")
        datei, groesse = self.download(f"/vertrieb/rechnungen/{self.daten['rechnung']}/datei/zugferd/",
                                       "pfad_zugferd.pdf", b"%PDF")
        anhang = subprocess.run(["pdfdetach", "-list", str(datei)], capture_output=True, text=True).stdout
        if "xml" not in anhang.lower():
            raise Abbruch(f"ZUGFeRD-PDF ohne eingebettete XML: {anhang.strip()[:120]}")
        return f"ZUGFeRD-PDF {groesse} Bytes, Anhang: {' '.join(anhang.split()[-1:])}"

    # ---------- 7. Bank ----------
    def bank_import(self):
        self.goto("/fibu/bank/")
        p = self.page
        p.set_input_files("main input[name='datei']", str(PROJEKT / "docs/beispiele/camt053_demo.xml"))
        box = p.locator("main [name='automatisch']")
        if box.count() and box.is_checked():
            box.uncheck()
        self.klick("main form[action$='/bank/import/'] button")
        self.pruefe_fehlerfrei()
        return f"CAMT.053 importiert; {self.erfolg()}"

    def bank_zuordnen(self):
        self.goto("/fibu/bank/?status=OFFEN")
        p = self.page
        links = p.locator("main a[href*='/fibu/bank/umsatz/']")
        if links.count() == 0:
            raise Abbruch("Keine offenen Umsätze nach dem Import")
        # Umsatz mit Zuordnungsvorschlag suchen
        hrefs = [a.get_attribute("href") for a in links.all()]
        for href in hrefs:
            self.goto(href)
            if p.locator("main tr.table-success input[name='op']").count():
                break
        else:
            raise Abbruch("Für keinen offenen Umsatz gibt es einen OP-Vorschlag")
        info = " ".join(p.locator("main tr.table-success").first.inner_text().split())[:120]
        p.locator("main tr.table-success input[name='op']").first.check()
        self.klick("main form button", "Zuordnen und buchen")
        self.pruefe_fehlerfrei()
        return f"Umsatz {href} dem OP zugeordnet ({info}); {self.erfolg()}"

    # ---------- 8. Mahnwesen ----------
    def mahnlauf(self):
        self.goto("/fibu/mahnwesen/")
        self.page.fill("main [name='stichtag']", str(HEUTE))
        self.klick("main form button", "Vorschlagslauf")
        self.pruefe_fehlerfrei()
        if "/mahnwesen/lauf/" not in self.page.url:
            raise Abbruch(f"Kein Mahnvorschlag geöffnet ({self.pfad()}): {self.erfolg()}")
        zeilen = self.page.locator("main table tbody tr").count()
        pdf = self.page.locator("main a[href*='/pdf']")
        zusatz = ""
        if pdf.count():
            _, g = self.download(pdf.first.get_attribute("href"), "pfad_mahnung.pdf", b"%PDF")
            zusatz = f", Mahnungs-PDF {g} Bytes"
        return f"Vorschlagslauf {self.pfad()} mit {zeilen} Zeile(n) (nicht ausgeführt){zusatz}"

    # ---------- 9. Bericht ----------
    def bericht(self):
        self.goto("/reporting/bericht/vertrieb.umsatz_kunde/")
        p = self.page
        p.fill("main [name='von']", f"{HEUTE.year}-01-01")
        p.fill("main [name='bis']", str(HEUTE))
        kunde = self.waehle("kunde", "Müller")
        self.klick("main form button", "Anzeigen")
        self.pruefe_fehlerfrei()
        zeilen = p.locator("main table tbody tr").count()
        if zeilen == 0:
            raise Abbruch("Bericht ohne Zeilen")
        url = self.pfad()
        trenn = "&" if "?" in url else "?"
        _, gx = self.download(url + trenn + "format=xlsx", "pfad_bericht.xlsx", b"PK")
        _, gp = self.download(url + trenn + "format=pdf", "pfad_bericht.pdf", b"%PDF")
        return f"Filter {HEUTE.year}-01-01 bis heute, Kunde {kunde}: {zeilen} Zeile(n); Excel {gx} Bytes, PDF {gp} Bytes"

    # ---------- 10. Suche ----------
    def suche(self):
        self.goto("/")
        p = self.page
        p.fill("nav input[name='q']", "Müller")
        with p.expect_navigation(wait_until="load"):
            p.press("nav input[name='q']", "Enter")
        text = p.inner_text("main")
        if "Müller" not in text:
            raise Abbruch("Suchbegriff nicht in den Treffern")
        treffer = p.locator("main a[href]").count()
        if self.daten.get("renr"):
            self.goto(f"/suche/?q={self.daten['renr']}")
            if self.daten["renr"] not in p.inner_text("main"):
                raise Abbruch(f"Rechnung {self.daten['renr']} über die Suche nicht gefunden")
        return f"„Müller“: {treffer} verlinkte Treffer; Rechnungsnummer {self.daten.get('renr', '-')} gefunden"

    def alle(self):
        self.ablauf("Anmeldung", [("Anmelden als admin", self.anmelden)])
        self.ablauf("1 Artikel", [("Artikel anlegen", self.artikel_anlegen)])
        self.ablauf("2 Bestellung", [("Bestellung anlegen", self.bestellung_anlegen),
                                     ("Bestellung freigeben", self.bestellung_freigeben),
                                     ("Bestellung als PDF", self.bestellung_pdf)])
        if "bestellung" in self.daten:
            self.ablauf("3 Wareneingang", [("Wareneingang mit Charge buchen", self.wareneingang),
                                           ("QS freigeben", self.qs_freigeben)])
        self.ablauf("4 Fertigungsauftrag", [("FA anlegen", self.fa_anlegen), ("FA freigeben", self.fa_freigeben),
                                            ("Laufkarte als PDF", self.laufkarte)])
        if self.daten.get("fanr"):
            self.ablauf("5 Stempelterminal", [("Kommen", self.terminal_kommen), ("Start Arbeitsgang", self.terminal_start),
                                              ("Ende mit Rückmeldung", self.terminal_ende), ("Gehen", self.terminal_gehen),
                                              ("Rückmeldungsliste", self.rueckmeldungen)])
        self.ablauf("6 Vertrieb", [("Angebot anlegen", self.angebot), ("Auftrag aus Angebot", self.auftrag),
                                   ("Lieferschein und Warenausgang", self.lieferschein),
                                   ("Rechnung erstellen", self.rechnung), ("Rechnung festschreiben", self.festschreiben),
                                   ("ZUGFeRD-PDF", self.zugferd)])
        self.ablauf("7 Bank", [("Kontoauszug CAMT.053 hochladen", self.bank_import),
                               ("Umsatz zuordnen", self.bank_zuordnen)])
        self.ablauf("8 Mahnwesen", [("Mahnlauf-Vorschlag", self.mahnlauf)])
        self.ablauf("9 Reporting", [("Bericht mit Filter, Excel- und PDF-Export", self.bericht)])
        self.ablauf("10 Suche", [("Globale Suche", self.suche)])


def teil_b(kp, dauer):
    ok = sum(1 for e in kp.ergebnisse if e[2])
    z = [f"## Teil B – Klickpfade ({time.strftime('%d.%m.%Y %H:%M')}, {dauer:.0f} s)\n",
         f"Rolle admin, Chromium headless. **{ok} von {len(kp.ergebnisse)} Schritten erfolgreich.** "
         "Die Klickpfade verändern Daten – anschließend wurden die Demodaten neu geladen.\n",
         "| Pfad | Schritt | Ergebnis | Details | Screenshot |", "|---|---|---|---|---|"]
    for pfad, schritt, gut, text, bild in kp.ergebnisse:
        z.append(f"| {pfad} | {schritt} | {'✅ ok' if gut else '❌ Fehler'} | {text.replace('|', '/')} | "
                 f"{f'`screenshots/{bild}`' if bild else '–'} |")
    z.append("")
    z.append("**Serverfehler (HTTP ≥ 500) während der Klickpfade:** " + ("; ".join(kp.serverfehler) or "keine"))
    js = list(dict.fromkeys(kp.jsfehler))
    z.append("\n**JavaScript-Fehler während der Klickpfade:** " + ("; ".join(js[:10]) or "keine"))
    return "\n".join(z) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--basis", default="http://localhost:3000")
    args = ap.parse_args()
    SCREENSHOTS.mkdir(exist_ok=True)
    start = time.time()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 900}, locale="de-DE")
        kp = Klickpfade(page, args.basis)
        kp.alle()
        browser.close()
    bericht_zusammenfuegen(teil_b=teil_b(kp, time.time() - start))
    fehler = [e for e in kp.ergebnisse if not e[2]]
    print(f"{len(kp.ergebnisse) - len(fehler)}/{len(kp.ergebnisse)} Schritte ok")
    sys.exit(1 if fehler or kp.serverfehler else 0)


if __name__ == "__main__":
    main()

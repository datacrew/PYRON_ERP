#!/usr/bin/env python3
"""Automatischer Oberflächentest (Playwright, Chromium headless) über die gesamte Weboberfläche.

Ablauf je Rolle: anmelden → ab dem Dashboard alle internen Links per Breitensuche besuchen (nur GET, keine
destruktiven Aktionen) → je Seite Status, JS-Fehler, fehlgeschlagene Requests, Template-Rückstände, englische
Standardtexte, leere Tabellen, horizontales Überlaufen (1366 px und 390 px) und kaputte Bilder prüfen →
Rechteprüfung (verbotene Seiten liefern 403/Umleitung, Menüpunkte ohne Recht sind unsichtbar).
Downloads (PDF/Excel/CSV) werden per HTTP-Abruf mit derselben Sitzung geprüft.

Aufruf (Server muss laufen, Demodaten geladen):
    python3 tests_ui/ui_crawl.py [--basis http://localhost:3000] [--rollen admin,vertrieb] [--max 400]
Ergebnis: tests_ui/ui_bericht.md (Teil „Crawl“), tests_ui/crawl_ergebnis.json, Screenshots in tests_ui/screenshots/.
"""
import argparse
import json
import os
import re
import sys
import time
from collections import Counter, deque
from multiprocessing import get_context
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

HIER = Path(__file__).resolve().parent
PROJEKT = HIER.parent
SCREENSHOTS = HIER / "screenshots"

ROLLEN = {"admin": "Admin!2026", "einkauf": "Demo!2026", "vertrieb": "Demo!2026", "produktion": "Demo!2026",
          "lager": "Demo!2026", "buchhaltung": "Demo!2026", "gast": "Demo!2026"}

# Links, die im Crawl nie aufgerufen werden (Zustandsänderungen, Abmelden, Django-Admin, API)
AUSSCHLUSS = re.compile(
    r"/(abmelden|admin|api)/|/loeschen/|/storno|/festschreiben/|/umwandeln/|/erledigt/|/uebernehmen/|"
    r"/uebertragen/|/qs/|/bank/automatisch/|/nachbuchen/|/mahnsperre/|/modul/")
# Downloads: nur per HTTP-Abruf prüfen (Status + Dateityp), nicht im Browser öffnen
DOWNLOAD = re.compile(r"/pdf/|/laufkarte/|/csv/|/datev/|/datei/|[?&]format=(xlsx|csv|pdf)|[?&]download=1")
# Aktions-URLs der Form /<id>/<aktion>/ – nur bekannte, lesende Unterseiten sind erlaubt
SICHERE_UNTERSEITEN = {"bearbeiten", "nachkalkulation", "entnahme", "fertigmelden", "korrektur", "aufloesung",
                       "pdf", "laufkarte", "kontoblatt"}
AKTION = re.compile(r"/\d+/([a-z_-]+)/$")

ENGLISCH = ["This field is required", "Enter a valid", "Select a valid choice", "Page not found", "Server Error",
            "Forbidden", "Not Found", "Are you sure", "Previous", "Next", "Submit", "Delete", "Cancel",
            "No results", "Welcome", "Log out", "Log in", "Username", "Add another", "Ensure this value",
            "Bad Request", "Request aborted"]
RUECKSTAENDE = [(re.compile(r"\{\{|\{%|%\}"), "Template-Syntax sichtbar"),
                (re.compile(r"TemplateSyntaxError|Traceback \(most recent|NoReverseMatch|VariableDoesNotExist"),
                 "Django-Fehlermeldung sichtbar"),
                (re.compile(r"(?<![\w-])None(?![\w-])"), "„None“ sichtbar"),
                (re.compile(r"(?<![\w-])(True|False)(?![\w-])"), "„True/False“ sichtbar"),
                (re.compile(r"\bnan\b|\bNaN\b"), "„NaN“ sichtbar"),
                (re.compile(r"Decimal\('"), "Decimal-Darstellung sichtbar")]
# Listen, in denen nach erp_demodaten Daten stehen müssen (leere Tabelle = Fehler mittel)
DATEN_ERWARTET = ["/stammdaten/artikel/", "/stammdaten/partner/", "/belege/", "/lager/", "/lager/journal/",
                  "/lager/charge/", "/einkauf/bestellungen/", "/wareneingang/liste/", "/produktion/auftraege/",
                  "/produktion/rueckmeldungen/", "/produktion/stempelungen/", "/fibu/buchungen/", "/fibu/op/",
                  "/fibu/konten/", "/vertrieb/auftraege/", "/vertrieb/rechnungen/", "/vertrieb/angebote/",
                  "/vertrieb/lieferscheine/", "/versand/touren/", "/reporting/", "/verwaltung/benutzer/",
                  "/verwaltung/audit/"]
# Für Seiten mit abweichendem Recht (Untermodule) wird bei der Rechteprüfung nur „kein 500“ verlangt
SONDERRECHTE = re.compile(r"^/produktion/(terminal|stempelungen|akkord)|^/fibu/(bank|mahnwesen|perioden|auswertungen)"
                          r"|^/vertrieb/(edi|shop)|^/suche/")
PRAEFIX_MODUL = {"stammdaten": "stammdaten", "belege": "belege", "lager": "lager", "einkauf": "einkauf",
                 "produktion": "produktion", "wareneingang": "wareneingang", "fibu": "fibu",
                 "vertrieb": "vertrieb", "versand": "versand", "reporting": "reporting", "verwaltung": "verwaltung"}

SCREENSHOT_SEITEN = [
    ("dashboard", "/"), ("cockpit", "/reporting/cockpit/"), ("reporting_liste", "/reporting/"),
    ("reporting_bericht", "/reporting/bericht/vertrieb.umsatz_kunde/?von=2026-01-01&bis=2026-12-31"),
    ("stammdaten_liste", "/stammdaten/artikel/"), ("stammdaten_formular", "/stammdaten/artikel/neu/"),
    ("belege_liste", "/belege/"), ("belege_formular", "/belege/neu/"),
    ("lager_liste", "/lager/"), ("lager_formular", "/lager/umbuchen/"),
    ("einkauf_liste", "/einkauf/bestellungen/"), ("einkauf_formular", "/einkauf/bestellungen/neu/"),
    ("wareneingang_liste", "/wareneingang/liste/"),
    ("produktion_liste", "/produktion/auftraege/"), ("produktion_formular", "/produktion/auftraege/neu/"),
    ("stempelterminal", "/produktion/terminal/"),
    ("fibu_liste", "/fibu/buchungen/"), ("fibu_formular", "/fibu/buchungen/neu/"),
    ("vertrieb_liste", "/vertrieb/auftraege/"), ("vertrieb_formular", "/vertrieb/angebote/neu/"),
    ("versand_liste", "/versand/touren/"), ("versand_formular", "/versand/touren/bilden/"),
    ("verwaltung_liste", "/verwaltung/benutzer/"), ("verwaltung_formular", "/verwaltung/rollen/neu/"),
    ("suche", "/suche/?q=M%C3%BCller"),
]

JS_PRUEFUNG = """() => {
  const d = document.documentElement;
  const bilder = [...document.images].filter(i => i.complete && i.naturalWidth === 0 && i.src).map(i => i.src);
  const leer = [...document.querySelectorAll('main table')].filter(t => {
      if (t.closest('form') && !t.querySelector('tbody')) return false;
      const rows = [...t.querySelectorAll('tbody tr')];
      if (!t.querySelector('thead')) return false;
      return rows.length === 0 || (rows.length === 1 && rows[0].querySelectorAll('td').length === 1
                                   && rows[0].querySelector('td[colspan]'));
  }).length;
  const breit = [...document.querySelectorAll('main *')].filter(e => {
      const r = e.getBoundingClientRect(); return r.right > d.clientWidth + 2 && !e.closest('.table-responsive')
        && getComputedStyle(e).position !== 'fixed';}).slice(0, 3)
      .map(e => e.tagName.toLowerCase() + (e.className && typeof e.className === 'string' ? '.' + e.className.split(' ').slice(0,2).join('.') : ''));
  return {scroll: d.scrollWidth, client: d.clientWidth, bilder, leer, breit,
          text: document.body ? document.body.innerText : '',
          menue: [...document.querySelectorAll('aside.sidebar a')].map(a => a.getAttribute('href')),
          links: [...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href'))};
}"""


def muster(url):
    """Normalisiert eine URL (Zahlen → {id}, Query nur Schlüssel) für die Begrenzung je Seitentyp."""
    t = urlsplit(url)
    pfad = re.sub(r"/\d+(?=/)", "/{id}", t.path)
    schluessel = sorted({k for k, _ in parse_qsl(t.query)})
    return pfad + ("?" + ",".join(schluessel) if schluessel else "")


def django_vorbereiten():
    """Rechte je Rolle, Modul-URLs und Prüf-URLs für die Rechteprüfung aus der Datenbank ermitteln."""
    sys.path.insert(0, str(PROJEKT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    import django

    django.setup()
    from django.contrib.auth import get_user_model
    from django.db import connections
    from django.urls import NoReverseMatch, URLPattern, URLResolver, get_resolver, reverse

    from apps.core.models import Modul
    from apps.core.permissions import rechte_fuer

    module = {}
    for m in Modul.objects.filter(aktiv=True):
        try:
            url = reverse(m.url_name) if m.url_name else reverse("core:platzhalter", args=[m.code])
        except NoReverseMatch:
            url = reverse("core:platzhalter", args=[m.code])
        module[m.code] = {"name": m.name, "url": url}
    rechte = {}
    U = get_user_model()
    for rolle in ROLLEN:
        u = U.objects.get(username=rolle)
        rechte[rolle] = {k: sorted(v) for k, v in rechte_fuer(u).items()}

    # Prüf-URLs: alle Seiten ohne Parameter + je Detailmuster das erste Objekt
    pruef = set()

    def lauf(muster_liste, ns=""):
        for p in muster_liste:
            if isinstance(p, URLResolver):
                if any(x in str(p.pattern) for x in ("admin", "api")):
                    continue
                lauf(p.url_patterns, (ns + ":" if ns else "") + p.namespace if p.namespace else ns)
            elif isinstance(p, URLPattern) and p.name and not p.pattern.converters:
                try:
                    url = reverse(f"{ns}:{p.name}" if ns else p.name)
                except NoReverseMatch:
                    continue
                if not AUSSCHLUSS.search(url) and not DOWNLOAD.search(url):
                    pruef.add(url)

    lauf(get_resolver().url_patterns)
    from apps.belege.models import Beleg
    from apps.einkauf.models import Bestellung
    from apps.fibu.models import Buchung
    from apps.produktion.models import Fertigungsauftrag
    from apps.stammdaten.models import Artikel
    from apps.vertrieb.models import Auftrag

    for name, model in [("stammdaten:artikel_bearbeiten", Artikel), ("belege:detail", Beleg),
                        ("einkauf:detail", Bestellung), ("produktion:detail", Fertigungsauftrag),
                        ("fibu:buchung", Buchung), ("vertrieb:auftrag", Auftrag)]:
        obj = model.objects.order_by("pk").first()
        if obj:
            pruef.add(reverse(name, args=[obj.pk]))
    connections.close_all()
    return {"module": module, "rechte": rechte, "pruef": sorted(pruef)}


class Crawler:
    def __init__(self, basis, rolle, passwort, info, maximal):
        self.basis, self.rolle, self.passwort, self.info, self.maximal = basis.rstrip("/"), rolle, passwort, info, maximal
        self.rechte = info["rechte"][rolle]
        self.probleme, self.seiten, self.downloads, self.screenshots = [], [], [], []
        self.konsole, self.fehlrequests = [], []

    # -------------------------------------------------------------- Hilfen
    def problem(self, url, art, schwere, detail=""):
        self.probleme.append({"rolle": self.rolle, "url": url, "art": art, "schwere": schwere, "detail": detail[:300]})

    def darf(self, modul, aktion="lesen"):
        return aktion in self.rechte.get(modul, [])

    def intern(self, href, von):
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            return None
        url = urljoin(von, href)
        t = urlsplit(url)
        if f"{t.scheme}://{t.netloc}" != self.basis:
            return None
        return t.path + ("?" + t.query if t.query else "")

    def anmelden(self, page):
        page.goto(self.basis + "/anmelden/")
        page.fill("#id_username", self.rolle)
        page.fill("#id_password", self.passwort)
        page.click("main form button")
        page.wait_for_load_state("load")
        if "/anmelden/" in page.url:
            raise RuntimeError(f"Anmeldung als {self.rolle} fehlgeschlagen")

    # -------------------------------------------------------------- Seitenprüfung
    def pruefe_seite(self, page, pfad, quelle):
        self.konsole.clear()
        self.fehlrequests.clear()
        start = time.time()
        try:
            antwort = page.goto(self.basis + pfad, wait_until="load", timeout=45000)
        except Exception as exc:  # noqa: BLE001 – Zeitüberschreitung o. Ä. als Tool-Fehler vermerken
            self.problem(pfad, "Seite nicht geladen (Werkzeugfehler/Zeitüberschreitung)", "mittel", str(exc))
            return None
        status = antwort.status if antwort else 0
        endpfad = urlsplit(page.url).path
        eintrag = {"url": pfad, "status": status, "ms": int((time.time() - start) * 1000), "quelle": quelle}
        self.seiten.append(eintrag)
        if status >= 500:
            self.problem(pfad, f"HTTP {status}", "hoch", f"verlinkt von {quelle}")
            return None
        if status == 404:
            self.problem(pfad, "Kaputter Link (404)", "mittel", f"verlinkt von {quelle}")
            return None
        if status == 403:
            self.problem(pfad, "Link sichtbar, Seite verboten (403)", "mittel", f"verlinkt von {quelle}")
        if endpfad.startswith("/anmelden/"):
            self.problem(pfad, "Unerwartete Umleitung zur Anmeldung", "mittel", f"verlinkt von {quelle}")
            return None
        time.sleep(0.15)
        daten = page.evaluate(JS_PRUEFUNG)
        for text in self.konsole:
            self.problem(pfad, "JavaScript-Fehler", "hoch" if text.startswith("pageerror") else "mittel", text)
        for text in self.fehlrequests:
            self.problem(pfad, "Fehlgeschlagener Request", "mittel", text)
        txt = daten["text"]
        for regex, art in RUECKSTAENDE:
            m = regex.search(txt)
            if m:
                schwere = "hoch" if "Django" in art else ("niedrig" if "True" in art else "mittel")
                umfeld = txt[max(0, m.start() - 60): m.end() + 40].replace("\n", " ⏎ ")
                self.problem(pfad, art, schwere, umfeld)
        for wort in ENGLISCH:
            m = re.search(rf"(?<!\w){re.escape(wort)}(?!\w)", txt)
            if m:
                self.problem(pfad, "Englischer Standardtext", "mittel",
                             f"„{wort}“: …{txt[max(0, m.start() - 40): m.end() + 30]}…".replace("\n", " "))
        if re.search(r"(?m)^-{6,}$", txt):
            self.problem(pfad, "Django-Standard-Leerauswahl „---------“", "niedrig")
        for src in daten["bilder"]:
            self.problem(pfad, "Kaputtes Bild", "mittel", src)
        if daten["scroll"] > daten["client"] + 2:
            self.problem(pfad, "Horizontales Überlaufen bei 1366 px", "mittel",
                         f"scrollWidth {daten['scroll']} > {daten['client']}; {', '.join(daten['breit'])}")
        if daten["leer"] and status == 200:
            erwartet = self.rolle == "admin" and urlsplit(pfad).path in DATEN_ERWARTET and "?" not in pfad
            self.problem(pfad, "Leere Tabelle", "mittel" if erwartet else "info",
                         f"{daten['leer']} Tabelle(n) ohne Zeilen")
        eintrag["leer"] = daten["leer"]
        return daten

    def mobil(self, page, pfad):
        page.set_viewport_size({"width": 390, "height": 844})
        time.sleep(0.2)
        d = page.evaluate("""() => {const e=document.documentElement; return {s:e.scrollWidth,c:e.clientWidth,
            w:[...document.querySelectorAll('body *')].filter(x=>x.getBoundingClientRect().right>e.clientWidth+2
            && !x.closest('.table-responsive')).slice(0,3).map(x=>x.tagName.toLowerCase()+'.'+String(x.className).split(' ')[0])}}""")
        page.set_viewport_size({"width": 1366, "height": 900})
        if d["s"] > d["c"] + 2:
            self.problem(pfad, "Horizontales Überlaufen bei 390 px (Mobil)", "niedrig",
                         f"scrollWidth {d['s']} > {d['c']}; {', '.join(d['w'])}")

    def download(self, page, pfad, quelle):
        try:
            r = page.request.get(self.basis + pfad, timeout=60000)
        except Exception as exc:  # noqa: BLE001
            self.problem(pfad, "Download nicht abrufbar (Werkzeugfehler)", "mittel", str(exc))
            return
        typ = r.headers.get("content-type", "")
        koerper = r.body()
        ok = r.status == 200 and "text/html" not in typ
        if ok and "pdf" in typ and not koerper.startswith(b"%PDF"):
            ok = False
        self.downloads.append({"url": pfad, "status": r.status, "typ": typ, "bytes": len(koerper), "ok": ok})
        if r.status >= 500:
            self.problem(pfad, f"Download HTTP {r.status}", "hoch", f"verlinkt von {quelle}")
        elif r.status == 403:
            self.problem(pfad, "Download-Link sichtbar, aber verboten (403)", "mittel", f"verlinkt von {quelle}")
        elif not ok:
            self.problem(pfad, "Download liefert keine Datei", "mittel", f"HTTP {r.status}, {typ}")

    # -------------------------------------------------------------- Ablauf
    def lauf(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1366, "height": 900}, locale="de-DE")
            page = ctx.new_page()
            page.on("console", lambda m: m.type == "error" and self.konsole.append(f"console: {m.text}"[:300]))
            page.on("pageerror", lambda e: self.konsole.append(f"pageerror: {e}"[:300]))
            page.on("requestfailed", lambda r: self.fehlrequests.append(
                f"{r.url[:150]} ({r.failure})") if "favicon" not in r.url else None)
            page.on("response", lambda r: r.status >= 400 and r.request.resource_type != "document"
                    and "favicon" not in r.url and self.fehlrequests.append(f"HTTP {r.status} {r.url[:150]}"))
            self.anmelden(page)
            self.crawl(page)
            self.rechtepruefung(page)
            if self.rolle == "admin":
                self.fotos(page)
            else:
                self.foto(page, f"{self.rolle}_dashboard", "/")
            browser.close()
        return {"rolle": self.rolle, "seiten": self.seiten, "probleme": self.probleme, "downloads": self.downloads,
                "screenshots": self.screenshots}

    def crawl(self, page):
        warteschlange = deque([("/", "Start")])
        gesehen, je_muster, dl_muster = {"/"}, Counter(), Counter()
        mobil_muster = set()
        while warteschlange and len(self.seiten) < self.maximal:
            pfad, quelle = warteschlange.popleft()
            daten = self.pruefe_seite(page, pfad, quelle)
            if not daten:
                continue
            m = muster(pfad)
            if m not in mobil_muster:
                mobil_muster.add(m)
                self.mobil(page, pfad)
            for href in daten["links"]:
                ziel = self.intern(href, self.basis + pfad)
                if not ziel or ziel in gesehen:
                    continue
                gesehen.add(ziel)
                zpfad = urlsplit(ziel).path
                if AUSSCHLUSS.search(zpfad) or zpfad.startswith("/anmelden/"):
                    continue
                zm = muster(ziel)
                if DOWNLOAD.search(ziel):
                    if dl_muster[zm] < 2:
                        dl_muster[zm] += 1
                        self.download(page, ziel, pfad)
                    continue
                a = AKTION.search(zpfad)
                if a and a.group(1) not in SICHERE_UNTERSEITEN:
                    continue
                if je_muster[zm] >= 2:
                    continue
                je_muster[zm] += 1
                warteschlange.append((ziel, pfad))

    def rechtepruefung(self, page):
        # 1) Menü: nur Module mit Leserecht
        page.goto(self.basis + "/")
        menue = set(page.evaluate("() => [...document.querySelectorAll('aside.sidebar a')].map(a => a.getAttribute('href'))"))
        for code, m in self.info["module"].items():
            sichtbar = m["url"] in menue
            if sichtbar and not self.darf(code):
                self.problem("/", "Menüpunkt ohne Recht sichtbar", "hoch", f"Modul {m['name']}")
            if not sichtbar and self.darf(code) and code not in ("bde", "akkord", "edi", "shopapi") \
                    and not code.startswith("fibu_"):
                self.problem("/", "Menüpunkt fehlt trotz Recht", "niedrig", f"Modul {m['name']} ({m['url']})")
        if self.darf("verwaltung") != ("/verwaltung/" in menue or any((h or "").startswith("/verwaltung") for h in menue)):
            pass  # Verwaltung ist ein normales Modul und oben bereits geprüft
        # 2) Direktaufruf aller Seiten: kein 500; ohne Modulrecht 403 bzw. Umleitung zur Anmeldung
        self.rechte_ergebnis = []
        for pfad in self.info["pruef"]:
            try:
                r = page.request.get(self.basis + pfad, max_redirects=0, timeout=45000)
            except Exception as exc:  # noqa: BLE001
                self.problem(pfad, "Rechteprüfung: Abruf fehlgeschlagen (Werkzeugfehler)", "mittel", str(exc))
                continue
            seg = pfad.strip("/").split("/")[0] if pfad.strip("/") else ""
            modul = PRAEFIX_MODUL.get(seg)
            if r.status >= 500:
                self.problem(pfad, f"Direktaufruf HTTP {r.status}", "hoch", "Rechteprüfung")
            elif modul and not SONDERRECHTE.search(pfad) and not self.darf(modul):
                if r.status == 200:
                    self.problem(pfad, "Seite ohne Modulrecht erreichbar", "hoch", f"Modul {modul}")
                elif r.status not in (302, 403, 405):  # 405 = reiner POST-Endpunkt, liefert keine Daten
                    self.problem(pfad, f"Unerwarteter Status {r.status} ohne Recht", "mittel", f"Modul {modul}")
                elif r.status == 403 and ("Forbidden" in r.text() or "<html" not in r.text().lower()):
                    self.problem(pfad, "403-Seite nicht deutsch/ohne Layout", "mittel", r.text()[:120])

    def foto(self, page, name, pfad, ganz=True):
        try:
            page.goto(self.basis + pfad, wait_until="load", timeout=45000)
            if "cockpit" in pfad or "bericht" in pfad:
                page.wait_for_timeout(1500)
            datei = SCREENSHOTS / f"{name}.png"
            page.screenshot(path=str(datei), full_page=ganz)
            self.screenshots.append({"name": name, "url": pfad, "datei": f"screenshots/{datei.name}"})
        except Exception as exc:  # noqa: BLE001
            self.problem(pfad, "Screenshot fehlgeschlagen (Werkzeugfehler)", "niedrig", str(exc))

    def fotos(self, page):
        for name, pfad in SCREENSHOT_SEITEN:
            self.foto(page, name, pfad)
        # Diagramme im Cockpit: Chart-Instanzen und Datenpunkte prüfen
        page.goto(self.basis + "/reporting/cockpit/", wait_until="load")
        page.wait_for_timeout(1500)
        diag = page.evaluate("""() => [...document.querySelectorAll('canvas')].map(c => {
            const ch = window.Chart && Chart.getChart(c);
            return {id: c.id, chart: !!ch, punkte: ch ? ch.data.datasets[0].data.length : 0,
                    summe: ch ? ch.data.datasets[0].data.reduce((a, b) => a + b, 0) : 0, hoehe: c.clientHeight};})""")
        self.cockpit_diagramme = diag
        self.screenshots.append({"name": "cockpit_diagramme", "url": "/reporting/cockpit/", "datei": "",
                                 "diagramme": diag})
        for d in diag:
            if not d["chart"] or not d["punkte"] or d["hoehe"] < 50:
                self.problem("/reporting/cockpit/", "Diagramm nicht gerendert", "mittel", json.dumps(d))
        if len(diag) < 3:
            self.problem("/reporting/cockpit/", "Weniger als 3 Diagramme im Cockpit", "mittel", json.dumps(diag))
        page.set_viewport_size({"width": 390, "height": 844})
        self.foto(page, "dashboard_mobil", "/")
        self.foto(page, "cockpit_mobil", "/reporting/cockpit/")
        page.set_viewport_size({"width": 1366, "height": 900})


def rolle_ausfuehren(arg):
    basis, rolle, info, maximal = arg
    try:
        return Crawler(basis, rolle, ROLLEN[rolle], info, maximal).lauf()
    except Exception as exc:  # noqa: BLE001
        import traceback

        return {"rolle": rolle, "seiten": [], "downloads": [], "screenshots": [],
                "probleme": [{"rolle": rolle, "url": "-", "art": "Crawl abgebrochen (Werkzeugfehler)", "schwere": "hoch",
                              "detail": traceback.format_exc()[-300:]}]}


SCHWERE = {"hoch": 0, "mittel": 1, "niedrig": 2, "info": 3}


def bericht_schreiben(ergebnisse, dauer):
    zeilen = ["## Teil A – Automatischer Crawl", "",
              f"Erstellt: {time.strftime('%d.%m.%Y %H:%M')} · Dauer {dauer:.0f} s · Browser: Chromium headless "
              "(Playwright), Viewport 1366×900, zusätzlich 390×844 je Seitentyp", "",
              "| Rolle | besuchte Seiten | Downloads geprüft | hoch | mittel | niedrig | info |", "|---|---|---|---|---|---|---|"]
    alle = []
    for e in ergebnisse:
        c = Counter(p["schwere"] for p in e["probleme"])
        zeilen.append(f"| {e['rolle']} | {len(e['seiten'])} | {len(e['downloads'])} | {c['hoch']} | {c['mittel']} | "
                      f"{c['niedrig']} | {c['info']} |")
        alle += e["probleme"]
    zeilen += ["", "### Gefundene Probleme (hoch/mittel/niedrig)", ""]
    relevante = [p for p in alle if p["schwere"] != "info"]
    if not relevante:
        zeilen.append("Keine Probleme mit Schweregrad hoch, mittel oder niedrig gefunden.")
    else:
        # gleiche Art + URL über Rollen zusammenfassen
        gruppen = {}
        for p in relevante:
            gruppen.setdefault((p["schwere"], p["art"], p["url"]), []).append(p)
        zeilen += ["| Schwere | Art | URL | Rolle(n) | Detail |", "|---|---|---|---|---|"]
        for (s, art, url), ps in sorted(gruppen.items(), key=lambda x: (SCHWERE[x[0][0]], x[0][1], x[0][2])):
            rollen = ", ".join(sorted({p["rolle"] for p in ps}))
            detail = ps[0]["detail"].replace("|", "\\|").replace("\n", " ")
            zeilen.append(f"| {s} | {art} | `{url}` | {rollen} | {detail} |")
    info = [p for p in alle if p["schwere"] == "info"]
    zeilen += ["", f"### Hinweise (info): {len(info)} leere Tabellen in Seiten ohne Datenerwartung "
               "(z. B. gefilterte Listen, Rollen ohne Daten) – siehe crawl_ergebnis.json", ""]
    zeilen += ["### Screenshots", ""]
    for e in ergebnisse:
        for s in e["screenshots"]:
            if s.get("datei"):
                zeilen.append(f"- {e['rolle']}: [{s['name']}]({s['datei']}) – `{s['url']}`")
            if s.get("diagramme"):
                zeilen.append(f"- Cockpit-Diagramme (Chart.js-Instanzen): " + "; ".join(
                    f"{d['id']}: {'gerendert' if d['chart'] else 'FEHLT'}, {d['punkte']} Werte" for d in s["diagramme"]))
    (HIER / "crawl_ergebnis.json").write_text(json.dumps(ergebnisse, ensure_ascii=False, indent=1, default=str))
    return "\n".join(zeilen) + "\n"


def bericht_zusammenfuegen(teil_a=None, teil_b=None):
    """ui_bericht.md besteht aus Teil A (Crawl) und Teil B (Klickpfade); jeder Lauf ersetzt nur seinen Teil."""
    datei = HIER / "ui_bericht.md"
    alt = datei.read_text() if datei.exists() else ""
    a = teil_a or ("## Teil A" + alt.split("## Teil A", 1)[1].split("## Teil B", 1)[0] if "## Teil A" in alt else "")
    b = teil_b or ("## Teil B" + alt.split("## Teil B", 1)[1].split("## Teil C", 1)[0] if "## Teil B" in alt else "")
    c = ("## Teil C" + alt.split("## Teil C", 1)[1]) if "## Teil C" in alt else ""
    kopf = ("# Oberflächentest (Playwright) – ERP Mittelstand\n\n"
            "Automatisch erzeugt von `tests_ui/ui_crawl.py` (Teil A) und `tests_ui/ui_klickpfade.py` (Teil B). "
            "Teil C (Korrekturen und offene Punkte) wird von Hand gepflegt.\n\n")
    datei.write_text(kopf + a.rstrip() + "\n\n" + b.rstrip() + "\n\n" + c)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--basis", default="http://localhost:3000")
    ap.add_argument("--rollen", default=",".join(ROLLEN))
    ap.add_argument("--max", type=int, default=400, help="maximal besuchte Seiten je Rolle")
    ap.add_argument("--parallel", type=int, default=4)
    args = ap.parse_args()
    SCREENSHOTS.mkdir(exist_ok=True)
    info = django_vorbereiten()
    start = time.time()
    rollen = [r.strip() for r in args.rollen.split(",") if r.strip()]
    with get_context("fork").Pool(min(args.parallel, len(rollen))) as pool:
        ergebnisse = pool.map(rolle_ausfuehren, [(args.basis, r, info, args.max) for r in rollen])
    bericht_zusammenfuegen(teil_a=bericht_schreiben(ergebnisse, time.time() - start))
    gesamt = Counter(p["schwere"] for e in ergebnisse for p in e["probleme"])
    for e in ergebnisse:
        print(f"{e['rolle']:12} Seiten {len(e['seiten']):4}  Probleme {Counter(p['schwere'] for p in e['probleme'])}")
    print("Gesamt:", dict(gesamt))
    sys.exit(1 if gesamt["hoch"] or gesamt["mittel"] else 0)


if __name__ == "__main__":
    main()

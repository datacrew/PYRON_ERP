"""Tests Phase 6 – Reporting: Registry, Rechtefilter, Kennzahlen gegen Demowerte, Exporte, Varianten, API, Suche."""
import csv
import io
from datetime import date
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook
from rest_framework.test import APIClient

from apps.belege.models import Beleg
from apps.fibu.models import OffenerPosten

from . import export, kennzahlen, registry
from .filter import bereinigen
from .models import Berichtsvariante

ERWARTET = {
    "vertrieb.umsatz_monat", "vertrieb.umsatz_kunde", "vertrieb.umsatz_artikel", "vertrieb.umsatz_mitarbeiter",
    "vertrieb.auftragsbestand", "vertrieb.lieferrueckstand", "vertrieb.angebotsquote", "vertrieb.abc_kunden",
    "einkauf.volumen", "einkauf.offene_bestellungen", "einkauf.liefertreue",
    "lager.bestandswert", "lager.meldebestand", "lager.umschlag", "lager.ladenhueter", "lager.mhd",
    "produktion.leistungsgrad_mitarbeiter", "produktion.leistungsgrad_arbeitsplatz", "produktion.ausschuss",
    "produktion.nachkalkulation", "produktion.auslastung",
    "fibu.op_altersstruktur", "fibu.liquiditaet", "fibu.guv_monate", "fibu.deckungsbeitrag",
    "versand.touren_region", "versand.fahrzeugauslastung", "versand.liefertreue",
}
JAHR = {"von": "2026-01-01", "bis": "2026-12-31"}


class ReportingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("erp_demodaten", verbosity=0)
        U = get_user_model()
        cls.admin = U.objects.get(username="admin")
        cls.vertrieb = U.objects.get(username="vertrieb")
        cls.produktion = U.objects.get(username="produktion")
        cls.buchhaltung = U.objects.get(username="buchhaltung")

    def lauf(self, code, **filter):
        b = registry.holen(code)
        werte, fehler = bereinigen(b, filter)
        self.assertIsNone(fehler)
        return b, werte, b.ausfuehren(werte)

    # ------------------------------------------------------------ Registry und Rechte
    def test_registry_vollstaendig(self):
        codes = {b.code for b in registry.alle()}
        self.assertTrue(ERWARTET <= codes, ERWARTET - codes)
        for b in registry.alle():
            werte, fehler = bereinigen(b, {})
            self.assertIsNone(fehler, b.code)
            b.ausfuehren(werte)  # jeder Bericht läuft mit Standardfiltern fehlerfrei

    def test_registrierung_prueft_filter(self):
        with self.assertRaises(ValueError):
            registry.registrieren(registry.Bericht("x.y", "X", "lager", lambda f: [], filter=["gibtsnicht"]))

    def test_rechtefilter(self):
        vertrieb = {b.code for b in registry.fuer_benutzer(self.vertrieb)}
        self.assertIn("vertrieb.umsatz_kunde", vertrieb)
        self.assertNotIn("fibu.op_altersstruktur", vertrieb)
        self.assertNotIn("einkauf.volumen", vertrieb)
        self.assertEqual(registry.fuer_benutzer(self.produktion), [])  # kein reporting.lesen
        self.assertEqual(len(registry.fuer_benutzer(self.admin)), len(registry.alle()))
        self.client.force_login(self.vertrieb)
        self.assertEqual(self.client.get(reverse("reporting:bericht", args=["fibu.op_altersstruktur"])).status_code,
                         403)
        self.assertEqual(self.client.get(reverse("reporting:bericht", args=["gibt.es.nicht"])).status_code, 404)
        self.client.force_login(self.produktion)
        self.assertEqual(self.client.get(reverse("reporting:index")).status_code, 403)

    # ------------------------------------------------------------ Kennzahlen gegen Demowerte
    def test_umsatz_gleich_festgeschriebene_rechnungen(self):
        qs = Beleg.objects.filter(status=Beleg.Status.FESTGESCHRIEBEN, belegdatum__year=2026)
        soll = (qs.filter(belegart="RECHNUNG").aggregate(s=Sum("summe_netto"))["s"] or 0) - \
               (qs.filter(belegart="GUTSCHRIFT").aggregate(s=Sum("summe_netto"))["s"] or 0)
        self.assertEqual(soll, D("43240.00"))
        for code in ("vertrieb.umsatz_kunde", "vertrieb.umsatz_monat", "vertrieb.umsatz_artikel",
                     "vertrieb.umsatz_mitarbeiter", "vertrieb.abc_kunden"):
            self.assertEqual(self.lauf(code, **JAHR)[2]["summen"]["umsatz"], soll, code)

    def test_op_altersstruktur_gleich_op_saldo(self):
        saldo = OffenerPosten.objects.filter(art="D").exclude(offen=0).aggregate(s=Sum("offen"))["s"]
        self.assertEqual(saldo, D("31165.30"))
        _, _, e = self.lauf("fibu.op_altersstruktur", op_art="D")
        s = e["summen"]
        self.assertEqual(s["offen"], saldo)
        self.assertEqual(s["nicht_faellig"] + s["t0_30"] + s["t31_60"] + s["t61_90"] + s["t90"], saldo)
        for z in e["zeilen"]:
            self.assertEqual(sum(z[k] for k in ("nicht_faellig", "t0_30", "t31_60", "t61_90", "t90")), z["offen"])
        _, _, k = self.lauf("fibu.op_altersstruktur", op_art="K")
        self.assertEqual(k["summen"]["offen"], D("7616.00"))

    def test_cockpit_kacheln_rechteabhaengig(self):
        namen = {k["name"]: k for k in kennzahlen.kacheln(self.admin)}
        self.assertEqual(namen["Umsatz Jahr (kumuliert)"]["wert"],
                         self.lauf("vertrieb.umsatz_kunde", von="2026-01-01", bis=str(date.today()))[2]["summen"]["umsatz"])
        self.assertEqual(namen["Offene Forderungen"]["wert"], D("31165.30"))
        self.assertIn("Lagerwert", namen)
        vertrieb = {k["name"] for k in kennzahlen.kacheln(self.vertrieb)}
        self.assertIn("Umsatz Monat", vertrieb)
        self.assertNotIn("Offene Forderungen", vertrieb)
        self.assertNotIn("op_alter", kennzahlen.diagramme(self.vertrieb))
        self.client.force_login(self.admin)
        r = self.client.get(reverse("reporting:cockpit"))
        self.assertContains(r, "chart.js")
        self.assertContains(r, "Überfällige Forderungen")

    # ------------------------------------------------------------ Exporte
    def test_export_xlsx(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("reporting:bericht", args=["vertrieb.umsatz_kunde"]), {**JAHR, "format": "xlsx"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        ws = load_workbook(io.BytesIO(r.content)).active
        self.assertEqual(ws["A1"].value, "Umsatz nach Kunde")
        werte = [[c.value for c in zeile] for zeile in ws.iter_rows()]
        summe = next(z for z in werte if z[0] == "Summe")
        idx = next(i for i, z in enumerate(werte) if z[0] == "Kunde" and z[1] == "Belege")  # Kopfzeile
        spalte = werte[idx].index(next(v for v in werte[idx] if v and str(v).startswith("Umsatz")))
        self.assertTrue(str(summe[spalte]).startswith("=SUM("))
        daten = [z[spalte] for z in werte[idx + 1:] if z[0] not in ("Summe", None)]
        self.assertEqual(sum(D(str(v)) for v in daten), D("43240.00"))
        self.assertIn(["Von", "01.01.2026"], [z[:2] for z in werte[:idx]])  # Filterangaben im Kopf

    def test_export_csv(self):
        b, w, e = self.lauf("vertrieb.umsatz_kunde", **JAHR)
        inhalt = export.als_csv(b, e, w)
        self.assertTrue(inhalt.startswith(b"\xef\xbb\xbf"))
        zeilen = list(csv.reader(io.StringIO(inhalt.decode("utf-8-sig")), delimiter=";"))
        summe = next(z for z in zeilen if z and z[0] == "Summe")
        self.assertIn("43.240,00", summe)

    def test_export_pdf(self):
        self.client.force_login(self.buchhaltung)
        r = self.client.get(reverse("reporting:bericht", args=["fibu.op_altersstruktur"]), {"format": "pdf"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b"%PDF"))
        b, w, e = self.lauf("fibu.op_altersstruktur")
        self.assertTrue(b.quer)
        _, summe = export.tabelle(b, e)
        self.assertIn(("31.165,30 €", True), summe)

    # ------------------------------------------------------------ Web, Varianten, Suche
    def test_seiten_und_variante(self):
        self.client.force_login(self.vertrieb)
        r = self.client.get(reverse("reporting:index"))
        self.assertContains(r, "Umsatz nach Kunde")
        self.assertNotContains(r, "Offene Posten")
        url = reverse("reporting:bericht", args=["vertrieb.umsatz_kunde"])
        self.assertContains(self.client.get(url, JAHR), "43.240,00")
        r = self.client.post(url, {**JAHR, "variante_name": "Jahr 2026", "favorit": "on"})
        self.assertEqual(r.status_code, 302)
        v = Berichtsvariante.objects.get(benutzer=self.vertrieb, name="Jahr 2026")
        self.assertEqual(v.filter["von"], "2026-01-01")
        self.assertContains(self.client.get(reverse("core:dashboard")), "Jahr 2026")
        self.assertContains(self.client.get(reverse("reporting:cockpit")), "Jahr 2026")
        self.client.post(reverse("reporting:variante_loeschen", args=[v.pk]))
        self.assertFalse(Berichtsvariante.objects.filter(pk=v.pk).exists())

    def test_globale_suche(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("core:suche"), {"q": "F-5000"})
        self.assertContains(r, "F-5000")
        self.assertContains(self.client.get(reverse("core:suche"), {"q": "RE-2026-00001"}), "RE-2026-00001")
        self.client.force_login(self.produktion)
        r = self.client.get(reverse("core:suche"), {"q": "RE-2026"})
        self.assertNotContains(r, "RE-2026-00001")  # kein Leserecht auf Belege

    # ------------------------------------------------------------ REST-API
    def test_api(self):
        api = APIClient()
        api.force_authenticate(self.vertrieb)
        liste = api.get("/api/v1/berichte/").json()
        codes = {b["code"] for b in liste}
        self.assertIn("vertrieb.umsatz_kunde", codes)
        self.assertNotIn("fibu.liquiditaet", codes)
        r = api.get("/api/v1/berichte/vertrieb.umsatz_kunde/", JAHR)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(D(r.json()["summen"]["umsatz"]), D("43240.00"))
        self.assertEqual(api.get("/api/v1/berichte/fibu.liquiditaet/").status_code, 403)
        self.assertEqual(api.get("/api/v1/berichte/gibt.es.nicht/").status_code, 404)
        self.assertEqual(api.get("/api/v1/berichte/vertrieb.umsatz_kunde/", {"von": "kein-datum"}).status_code, 400)
        api.force_authenticate(self.produktion)
        self.assertEqual(api.get("/api/v1/berichte/").status_code, 403)

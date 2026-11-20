"""Tests Wareneingang: Teil-/Restlieferung, Bestellstatus, Chargenpflicht, QS-Sperre/Freigabe, Differenzen, Rechte."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.core import events
from apps.einkauf import services as ek
from apps.einkauf.models import Bestellung
from apps.einkauf.tests import bestellung_anlegen
from apps.lager import services as lager
from apps.lager.models import Bestandsart, Lagerort
from apps.stammdaten.models import Geschaeftspartner
from apps.wareneingang import services
from apps.wareneingang.models import Wareneingang, WareneingangFehler, WEPosition

S = Bestellung.Status
FREI, GESPERRT = Bestandsart.FREI, Bestandsart.GESPERRT


class WareneingangTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("erp_demodaten", verbosity=0)
        User = get_user_model()
        cls.lager_user = User.objects.get(username="lager")
        cls.einkauf = User.objects.get(username="einkauf")
        cls.schmidt = Geschaeftspartner.objects.get(name="Stahlhandel Schmidt GmbH")
        cls.we_ort = Lagerort.objects.get(code="WE")

    def _bestellung(self, *positionen):
        return ek.freigeben(bestellung_anlegen(self.schmidt, list(positionen)), self.einkauf)

    def _we(self, b, *zeilen, **kw):
        positionen = [dict(bestellposition=bp, **z) for bp, z in zip(b.positionen.order_by("position"), zeilen)]
        return services.buchen(b, positionen, lieferschein_nr=kw.pop("ls", "LS-1"), lagerort=self.we_ort,
                               benutzer=self.lager_user, **kw)

    def test_teillieferung_und_bestellstatus(self):
        b = self._bestellung(("R-1010", "100", "5.00"))
        frei_vorher = lager.verfuegbar(b.positionen.get().artikel)
        we = self._we(b, {"menge": "40", "chargennummer": "C-1"})
        self.assertTrue(we.nummer.startswith("WEB-"))
        b.refresh_from_db()
        self.assertEqual(b.status, S.TEILGELIEFERT)
        bp = b.positionen.get()
        self.assertEqual((bp.geliefert_menge, bp.offen_menge), (Decimal("40"), Decimal("60")))
        self.assertIn("Unterlieferung", we.positionen.get().hinweis)
        self.assertEqual(lager.verfuegbar(bp.artikel), frei_vorher + 40)   # R-1010 ohne QS-Pflicht → frei
        self._we(b, {"menge": "60", "chargennummer": "C-2"}, ls="LS-2")
        b.refresh_from_db()
        self.assertEqual(b.status, S.GELIEFERT)
        with self.assertRaises(WareneingangFehler):   # geliefert → kein weiterer WE
            self._we(b, {"menge": "1", "chargennummer": "C-3"}, ls="LS-3")

    def test_restmenge_abschliessen_und_ueberlieferung(self):
        b = self._bestellung(("R-1020", "1000", None), ("R-1020", "500", None))
        we = self._we(b, {"menge": "900", "restmenge_abschliessen": True}, {"menge": "520"})
        b.refresh_from_db()
        self.assertEqual(b.status, S.GELIEFERT)
        self.assertIn("Restmenge abgeschlossen", we.hinweise)
        self.assertIn("Überlieferung +20", we.hinweise)

    def test_preisdifferenz_und_durchschnittspreis(self):
        b = self._bestellung(("R-1020", "1000", "0.05"))
        ld = lager.lagerdaten(b.positionen.get().artikel)
        alt_bestand, alt_preis = ld.bestand_gesamt, ld.durchschnittspreis
        we = self._we(b, {"menge": "1000", "lieferpreis": "0.07"})
        pos = we.positionen.get()
        self.assertEqual(pos.preisdifferenz, Decimal("0.0200"))
        self.assertIn("Preisdifferenz", we.hinweise)
        ld.refresh_from_db()
        erwartet = ((alt_bestand * alt_preis + Decimal("70")) / (alt_bestand + 1000)).quantize(Decimal("0.0001"))
        self.assertEqual(ld.durchschnittspreis, erwartet)          # Zugang zum Lieferpreis bewertet
        self.assertEqual(pos.lagerbewegung.wert, Decimal("70.00"))

    def test_chargenpflicht(self):
        b = self._bestellung(("R-1000", "100", None))
        anzahl = Wareneingang.objects.count()
        with self.assertRaisesMessage(WareneingangFehler, "chargenpflichtig"):
            self._we(b, {"menge": "100"})
        self.assertEqual(Wareneingang.objects.count(), anzahl)
        b.refresh_from_db()
        self.assertEqual((b.status, b.positionen.get().geliefert_menge), (S.BESTELLT, 0))
        with self.assertRaisesMessage(WareneingangFehler, "Lieferschein"):
            self._we(b, {"menge": "100", "chargennummer": "X"}, ls=" ")

    def test_qs_sperre_und_freigabe(self):
        b = self._bestellung(("R-1000", "200", None))
        artikel = b.positionen.get().artikel
        vorher = lager.bestand_summen(artikel)
        empfangen = []
        events.abonnieren("wareneingang.gebucht")(lambda **kw: empfangen.append(kw["wareneingang"].nummer))
        with self.captureOnCommitCallbacks(execute=True):
            we = self._we(b, {"menge": "200", "chargennummer": "QS-1", "mhd": "2027-12-31"})
        self.assertEqual(empfangen, [we.nummer])
        pos = we.positionen.get()
        self.assertEqual((pos.qs_status, pos.qs_erforderlich), (WEPosition.QS.OFFEN, True))
        self.assertEqual(pos.charge.lieferant, self.schmidt)
        nach_we = lager.bestand_summen(artikel)
        self.assertEqual(nach_we[GESPERRT], vorher[GESPERRT] + 200)
        self.assertEqual(nach_we[FREI], vorher[FREI])
        services.qs_entscheiden(pos, WEPosition.QS.FREIGEGEBEN, benutzer=self.lager_user)
        nach_qs = lager.bestand_summen(artikel)
        self.assertEqual((nach_qs[FREI], nach_qs[GESPERRT]), (vorher[FREI] + 200, vorher[GESPERRT]))
        with self.assertRaises(WareneingangFehler):
            services.qs_entscheiden(pos, WEPosition.QS.FREIGEGEBEN)

    def test_qs_reklamation_mit_ruecksendung(self):
        b = self._bestellung(("B-3100", "100", None))
        we = self._we(b, {"menge": "100", "chargennummer": "SD-1"})
        pos = we.positionen.get()
        with self.assertRaisesMessage(WareneingangFehler, "Reklamationsgrund"):
            services.qs_entscheiden(pos, WEPosition.QS.GESPERRT)
        gesperrt = lager.bestand_summen(pos.artikel)[GESPERRT]
        services.qs_entscheiden(pos, WEPosition.QS.GESPERRT, bemerkung="Draht korrodiert", ruecksenden=True)
        pos.refresh_from_db()
        b.refresh_from_db()
        self.assertTrue(pos.zuruckgesendet)
        self.assertEqual(lager.bestand_summen(pos.artikel)[GESPERRT], gesperrt - 100)
        self.assertEqual((b.status, b.positionen.get().offen_menge), (S.BESTELLT, Decimal("100")))

    def test_web_buchung_und_rechte(self):
        b = self._bestellung(("R-1000", "300", None))
        bp = b.positionen.get()
        url = reverse("wareneingang:buchen", args=[b.pk])
        daten = {"lieferschein_nr": "LS-WEB", "lagerort": self.we_ort.pk, "datum": "2026-11-20", "bemerkung": "",
                 "we-TOTAL_FORMS": "1", "we-INITIAL_FORMS": "1", "we-MIN_NUM_FORMS": "0", "we-MAX_NUM_FORMS": "1000",
                 "we-0-bestellposition": bp.pk, "we-0-menge": "100", "we-0-chargennummer": "WEB-C1"}
        self.client.force_login(get_user_model().objects.get(username="gast"))
        self.assertEqual(self.client.post(url, daten).status_code, 403)
        self.client.force_login(self.einkauf)   # Einkauf darf buchen, aber keine QS-Entscheidung
        antwort = self.client.post(url, daten)
        self.assertEqual(antwort.status_code, 302, getattr(antwort, "context", None) and
                         [antwort.context["kopf"].errors, antwort.context["formset"].errors])
        pos = WEPosition.objects.get(wareneingang__bestellung=b)
        qs_url = reverse("wareneingang:qs", args=[pos.pk, "freigeben"])
        self.assertEqual(self.client.post(qs_url).status_code, 403)
        api_qs = f"/api/v1/wareneingaenge/{pos.wareneingang_id}/qs/"
        self.assertEqual(self.client.post(api_qs, {"position": pos.pk, "entscheid": "FREIGEGEBEN"}).status_code, 403)
        self.client.force_login(self.lager_user)
        self.assertEqual(self.client.post(qs_url, {"bemerkung": "Maße i. O."}).status_code, 302)
        pos.refresh_from_db()
        self.assertEqual(pos.qs_status, WEPosition.QS.FREIGEGEBEN)
        self.assertEqual(self.client.get(reverse("wareneingang:pdf", args=[pos.wareneingang_id])).content[:4],
                         b"%PDF")

    def test_api_buchung(self):
        b = self._bestellung(("R-1010", "50", None))
        self.client.force_login(self.lager_user)
        antwort = self.client.post("/api/v1/wareneingaenge/", {
            "bestellung": b.pk, "lieferschein_nr": "API-1", "lagerort": self.we_ort.pk,
            "positionen": [{"bestellposition": b.positionen.get().pk, "menge": "50", "chargennummer": "API-C"}]},
            content_type="application/json")
        self.assertEqual(antwort.status_code, 201, antwort.content)
        b.refresh_from_db()
        self.assertEqual(b.status, S.GELIEFERT)
        fehler = self.client.post("/api/v1/wareneingaenge/", {
            "bestellung": b.pk, "lieferschein_nr": "API-2", "lagerort": self.we_ort.pk,
            "positionen": [{"bestellposition": b.positionen.get().pk, "menge": "1", "chargennummer": "X"}]},
            content_type="application/json")
        self.assertEqual(fehler.status_code, 409)

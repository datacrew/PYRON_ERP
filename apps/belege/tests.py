from decimal import Decimal
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import DatabaseError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.belege import services
from apps.belege.models import Beleg, BelegGesperrt, BelegPosition
from apps.belege.pdf import beleg_pdf
from apps.stammdaten.models import Adresse, Artikel, Geschaeftspartner, Stueckliste

MEDIA = tempfile.mkdtemp(prefix="erp-test-")


@override_settings(MEDIA_ROOT=MEDIA)
class BelegWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("erp_demodaten", verbosity=0)
        cls.admin = get_user_model().objects.get(username="admin")
        cls.kunde = Geschaeftspartner.objects.get(name="Müller Maschinenbau GmbH")

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def _beleg(self):
        b = Beleg.objects.create(belegart="RECHNUNG", partner=self.kunde, erstellt_von=self.admin)
        BelegPosition.objects.create(beleg=b, artikel=Artikel.objects.get(artikelnummer="F-5000"),
                                     menge=Decimal("3"), einzelpreis=Decimal("10.005"), steuersatz=Decimal("19"))
        BelegPosition.objects.create(beleg=b, bezeichnung="Fracht", menge=1, einzelpreis=Decimal("20"),
                                     steuersatz=Decimal("7"))
        b.berechne_summen()
        b.save()
        return b

    def test_summen_und_steuer(self):
        b = self._beleg()
        self.assertEqual(b.summe_netto, Decimal("50.02"))      # 30,02 + 20,00
        self.assertEqual(b.summe_steuer, Decimal("7.10"))      # 5,70 + 1,40
        self.assertEqual(b.summe_brutto, Decimal("57.12"))

    def test_kompletter_workflow(self):
        b = self._beleg()
        self.assertIsNone(b.nummer)
        b = services.freigeben(b, self.admin)
        self.assertRegex(b.nummer, r"^RE-\d{4}-\d{5}$")
        self.assertEqual(b.partner_daten["ort"], "Esslingen")
        b = services.festschreiben(b, self.admin)
        self.assertEqual(b.status, Beleg.Status.FESTGESCHRIEBEN)
        self.assertEqual(len(b.pruefsumme), 64)
        self.assertTrue(b.pdf.name.endswith(".pdf"))
        with b.pdf.open("rb") as f:
            self.assertTrue(f.read(5) == b"%PDF-")
        # Kette: Demodaten enthalten bereits eine festgeschriebene Rechnung
        vorgaenger = Beleg.objects.get(ketten_index=b.ketten_index - 1)
        self.assertEqual(b.vorgaenger_pruefsumme, vorgaenger.pruefsumme)
        anzahl, fehler = services.kette_pruefen()
        self.assertEqual(fehler, [])
        self.assertGreaterEqual(anzahl, 2)
        b = services.stornieren(b, self.admin, "Falscher Kunde")
        self.assertEqual(b.status, Beleg.Status.STORNIERT)
        self.assertEqual(services.kette_pruefen()[1], [])

    def test_lueckenlose_nummern_bei_fehler(self):
        b1 = services.freigeben(self._beleg(), self.admin)
        leer = Beleg.objects.create(belegart="RECHNUNG", partner=self.kunde)
        with self.assertRaises(services.WorkflowFehler):
            services.freigeben(leer, self.admin)  # ohne Positionen → keine Nummer verbraucht
        b2 = services.freigeben(self._beleg(), self.admin)
        self.assertEqual(int(b2.nummer[-5:]), int(b1.nummer[-5:]) + 1)

    def test_codesperre_nach_freigabe(self):
        b = services.freigeben(self._beleg(), self.admin)
        b = Beleg.objects.get(pk=b.pk)
        b.betreff = "Manipulation"
        with self.assertRaises(BelegGesperrt):
            b.save()
        with self.assertRaises(BelegGesperrt):
            b.delete()
        with self.assertRaises(BelegGesperrt):
            b.positionen.first().save()
        with self.assertRaises(services.WorkflowFehler):
            services.freigeben(b, self.admin)

    def test_datenbank_trigger_sperrt_festgeschriebene(self):
        b = services.festschreiben(services.freigeben(self._beleg(), self.admin), self.admin)
        with self.assertRaises(DatabaseError), transaction.atomic():
            Beleg.objects.filter(pk=b.pk).update(summe_brutto=Decimal("1.00"))
        with self.assertRaises(DatabaseError), transaction.atomic():
            BelegPosition.objects.filter(beleg=b).update(menge=99)
        with self.assertRaises(DatabaseError), transaction.atomic():
            Beleg.objects.filter(pk=b.pk).delete()
        b.refresh_from_db()
        self.assertEqual(b.summe_brutto, Decimal("57.12"))

    def test_storno_braucht_grund(self):
        b = services.freigeben(self._beleg(), self.admin)
        with self.assertRaises(services.WorkflowFehler):
            services.stornieren(b, self.admin, " ")

    def test_pdf_vorschau_entwurf(self):
        b = self._beleg()
        pdf = beleg_pdf(b)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertGreater(len(pdf), 5000)

    def test_weboberflaeche_workflow(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("belege:neu"), {
            "belegart": "RECHNUNG", "partner": self.kunde.pk, "belegdatum": "2026-11-20", "betreff": "Web",
            "pos-TOTAL_FORMS": "2", "pos-INITIAL_FORMS": "0", "pos-MIN_NUM_FORMS": "0", "pos-MAX_NUM_FORMS": "1000",
            "pos-0-artikel": Artikel.objects.get(artikelnummer="F-5010").pk, "pos-0-menge": "2",
            "pos-0-bezeichnung": "", "pos-0-einzelpreis": "", "pos-0-steuersatz": "", "pos-0-rabatt_prozent": "",
            "pos-0-einheit": "",
            "pos-1-bezeichnung": "Montage", "pos-1-menge": "1,5", "pos-1-einzelpreis": "1.000,00",
            "pos-1-steuersatz": "19", "pos-1-rabatt_prozent": "10", "pos-1-einheit": "Std", "pos-1-artikel": "",
        })
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["formset"].errors)
        b = Beleg.objects.get(betreff="Web")
        self.assertEqual(b.summe_netto, Decimal("1728.00"))  # 2 × 189 + 1,5 × 1000 × 0,9
        self.client.post(reverse("belege:aktion", args=[b.pk, "freigeben"]))
        self.client.post(reverse("belege:aktion", args=[b.pk, "festschreiben"]))
        b.refresh_from_db()
        self.assertEqual(b.status, Beleg.Status.FESTGESCHRIEBEN)
        r = self.client.get(reverse("belege:pdf", args=[b.pk]))
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertEqual(self.client.get(reverse("belege:detail", args=[b.pk])).status_code, 200)

    def test_api_beleg_anlegen_und_freigeben(self):
        self.client.force_login(self.admin)
        r = self.client.post("/api/v1/belege/", {
            "belegart": "AUFTRAG", "partner": self.kunde.pk, "belegdatum": "2026-11-20",
            "positionen": [{"artikel": Artikel.objects.get(artikelnummer="F-5000").pk, "menge": "4"}],
        }, content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["summe_netto"], "194.00")
        pk = r.json()["id"]
        r = self.client.post(f"/api/v1/belege/{pk}/freigeben/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["nummer"].startswith("AB-"))
        r = self.client.patch(f"/api/v1/belege/{pk}/", {"betreff": "x"}, content_type="application/json")
        self.assertEqual(r.status_code, 409)

    def test_vertrieb_darf_nicht_verwalten_gast_nicht_freigeben(self):
        b = self._beleg()
        gast = get_user_model().objects.get(username="gast")
        self.client.force_login(gast)
        self.assertEqual(self.client.get(reverse("belege:detail", args=[b.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse("belege:aktion", args=[b.pk, "freigeben"])).status_code, 403)
        self.client.force_login(get_user_model().objects.get(username="vertrieb"))
        self.assertEqual(self.client.get(reverse("belege:vorlage_liste")).status_code, 403)


class StuecklistenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("erp_demodaten", verbosity=0)

    def test_mehrstufige_aufloesung(self):
        sl = Stueckliste.objects.get(artikel__artikelnummer="F-5010")
        ebenen = {(e, a.artikelnummer) for e, a, _, _ in sl.aufloesen(Decimal(10))}
        self.assertIn((1, "F-5000"), ebenen)
        self.assertIn((2, "H-2000"), ebenen)
        self.assertIn((3, "R-1000"), ebenen)
        bedarf = {a.artikelnummer: m for a, m in sl.rohstoffbedarf(Decimal(10)).items()}
        self.assertEqual(bedarf["R-1020"], Decimal("60.000"))   # 10×2 + 10×4
        self.assertEqual(bedarf["R-1000"], Decimal("14.400"))   # 10 × 2 × 0,72

    def test_adresse_snapshot_quelle(self):
        p = Geschaeftspartner.objects.get(name="Autoteile Weber AG")
        Adresse.objects.create(partner=p, typ="RECHNUNG", strasse="Postfach 1", plz="80001", ort="München")
        self.assertEqual(p.rechnungsadresse.strasse, "Postfach 1")

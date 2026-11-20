import threading

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from apps.core.events import abonnieren, veroeffentlichen
from apps.core.models import AuditLog, Modul, Nummernkreis, Recht, Rolle
from apps.core.nummernkreise import naechste_nummer
from apps.core.permissions import hat_recht, rechte_cache_leeren

User = get_user_model()


class RechteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("erp_grunddaten", verbosity=0)
        cls.einkauf = User.objects.create_user("e1", password="Geheim!12345")
        Rolle.objects.get(name="Einkauf").benutzer.add(cls.einkauf)
        cls.ohne = User.objects.create_user("o1", password="Geheim!12345")
        cls.admin = User.objects.create_superuser("a1", password="Geheim!12345")

    def test_rollenrechte(self):
        u = self.einkauf
        self.assertTrue(hat_recht(u, "stammdaten", "lesen"))
        self.assertTrue(hat_recht(u, "stammdaten", "anlegen"))
        self.assertFalse(hat_recht(u, "stammdaten", "loeschen"))
        self.assertTrue(hat_recht(u, "belege", "freigeben"))
        self.assertFalse(hat_recht(u, "verwaltung", "lesen"))
        self.assertFalse(hat_recht(u, "fibu", "lesen"))

    def test_ohne_rolle_keine_rechte(self):
        self.assertFalse(hat_recht(self.ohne, "stammdaten", "lesen"))

    def test_superuser_alles_ausser_inaktive_module(self):
        self.assertTrue(hat_recht(self.admin, "fibu", "loeschen"))
        Modul.objects.filter(code="fibu").update(aktiv=False)
        rechte_cache_leeren(self.admin)
        self.assertFalse(hat_recht(self.admin, "fibu", "lesen"))

    def test_deaktiviertes_modul_sperrt_rolle(self):
        Modul.objects.filter(code="einkauf").update(aktiv=False)
        self.assertFalse(hat_recht(self.einkauf, "einkauf", "lesen"))

    def test_view_rechtepruefung(self):
        self.client.force_login(self.ohne)
        self.assertEqual(self.client.get(reverse("stammdaten:artikel_liste")).status_code, 403)
        self.client.force_login(self.einkauf)
        self.assertEqual(self.client.get(reverse("stammdaten:artikel_liste")).status_code, 200)
        self.assertEqual(self.client.get(reverse("core:verwaltung")).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("stammdaten:artikel_liste")).status_code, 302)

    def test_api_rechtepruefung(self):
        self.client.force_login(self.einkauf)
        self.assertEqual(self.client.get("/api/v1/artikel/").status_code, 200)
        self.assertEqual(self.client.delete("/api/v1/artikel/999/").status_code, 403)
        self.client.force_login(self.ohne)
        self.assertEqual(self.client.get("/api/v1/belege/").status_code, 403)

    def test_rechte_matrix_speichern(self):
        self.client.force_login(self.admin)
        rolle = Rolle.objects.create(name="Test")
        antwort = self.client.post(reverse("core:rolle_bearbeiten", args=[rolle.pk]), {
            "name": "Test", "beschreibung": "", "r_fibu_lesen": "on", "r_fibu_freigeben": "on",
            "benutzer": [self.ohne.pk]})
        self.assertEqual(antwort.status_code, 302)
        recht = Recht.objects.get(rolle=rolle, modul__code="fibu")
        self.assertTrue(recht.lesen and recht.freigeben and not recht.loeschen)
        self.assertTrue(hat_recht(User.objects.get(pk=self.ohne.pk), "fibu", "freigeben"))

    def test_seiten_laden(self):
        self.client.force_login(self.admin)
        for name in ["core:dashboard", "core:verwaltung", "core:benutzer_liste", "core:rollen_liste",
                     "core:module", "core:audit", "core:nummernkreis_liste", "core:mandant",
                     "stammdaten:index", "stammdaten:partner_liste", "stammdaten:konto_liste",
                     "stammdaten:stueckliste_liste", "stammdaten:arbeitsplan_liste", "belege:liste",
                     "belege:neu", "belege:vorlage_liste", "belege:kette",
                     "lager:uebersicht", "lager:journal", "lager:umbuchung", "lager:inventur_liste",
                     "lager:lagerort_liste", "lager:dispo_liste", "lager:charge_liste",
                     "einkauf:index", "einkauf:liste", "einkauf:neu", "einkauf:termine", "einkauf:vorschlag",
                     "einkauf:banf_liste", "wareneingang:index", "wareneingang:liste"]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("core:platzhalter", args=["produktion"])).status_code, 200)


class NummernkreisTests(TestCase):
    def setUp(self):
        Nummernkreis.objects.create(code="TEST", bezeichnung="Test", praefix="T-", jahresbezogen=True, stellen=4)

    def test_fortlaufend(self):
        a, b = naechste_nummer("TEST"), naechste_nummer("TEST")
        self.assertTrue(a.endswith("-0001") and b.endswith("-0002"))
        self.assertTrue(a.startswith("T-"))

    def test_rollback_erzeugt_keine_luecke(self):
        naechste_nummer("TEST")
        try:
            with transaction.atomic():
                naechste_nummer("TEST")
                raise RuntimeError("Abbruch")
        except RuntimeError:
            pass
        self.assertTrue(naechste_nummer("TEST").endswith("-0002"))

    def test_audit_log(self):
        nk = Nummernkreis.objects.get(code="TEST")
        nk.bezeichnung = "Geändert"
        nk.save()
        eintrag = AuditLog.objects.filter(aktion="aendern", objekt_id=str(nk.pk)).first()
        self.assertEqual(eintrag.aenderungen["bezeichnung"], ["Test", "Geändert"])


class NummernkreisParallelTests(TransactionTestCase):
    def test_parallele_vergabe_ohne_dubletten(self):
        Nummernkreis.objects.create(code="PAR", bezeichnung="Parallel", jahresbezogen=False, stellen=3)
        ergebnisse, fehler = [], []

        def ziehen():
            try:
                for _ in range(5):
                    ergebnisse.append(naechste_nummer("PAR"))
            except Exception as exc:  # pragma: no cover
                fehler.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=ziehen) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertFalse(fehler)
        self.assertEqual(sorted(ergebnisse), [str(i).zfill(3) for i in range(1, 21)])


class EreignisTests(TestCase):
    def test_veroeffentlichen_abonnieren(self):
        empfangen = []

        @abonnieren("test.ereignis")
        def empfaenger(wert, **kwargs):
            empfangen.append(wert)

        veroeffentlichen("test.ereignis", wert=42)
        veroeffentlichen("anderes.ereignis", wert=1)
        self.assertEqual(empfangen, [42])

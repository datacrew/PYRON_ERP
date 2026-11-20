"""Tests Lager: Bestandsbuchung, gleitender Durchschnittspreis, unveränderliches Journal, Inventur, Rechte."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction
from django.test import TestCase

from apps.lager import services
from apps.lager.models import Bestand, Bestandsart, Lagerbewegung, LagerFehler, Lagerort, Lagerplatz
from apps.stammdaten.models import Artikel, Einheit, Steuersatz

Art = Lagerbewegung.Art
FREI, GESPERRT = Bestandsart.FREI, Bestandsart.GESPERRT


class LagerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("erp_demodaten", verbosity=0)
        cls.admin = get_user_model().objects.get(username="admin")
        cls.rl = Lagerort.objects.get(code="RL")
        cls.fl = Lagerort.objects.get(code="FL")
        cls.platz = Lagerplatz.objects.get(code="RL-01")
        cls.artikel = Artikel.objects.create(
            artikelnummer="T-9000", bezeichnung="Testartikel", art="ROHSTOFF", einheit=Einheit.objects.get(code="Stk"),
            steuersatz=Steuersatz.objects.get(code="UST19"), einkaufspreis=Decimal("1.00"))

    def test_durchschnittspreis_zugang_abgang(self):
        a = self.artikel
        services.buchen(Art.ZUGANG, a, 100, nach=(self.rl, None, FREI), preis=Decimal("2.00"))
        b = services.buchen(Art.ZUGANG, a, 100, nach=(self.rl, None, FREI), preis=Decimal("4.00"))
        self.assertEqual(b.durchschnittspreis_neu, Decimal("3.0000"))
        self.assertEqual(b.wert, Decimal("400.00"))
        ab = services.buchen(Art.ABGANG, a, 50, von=(self.rl, None, FREI))
        self.assertEqual(ab.wert, Decimal("-150.00"))           # Abgang zum Ø-Preis
        ld = services.lagerdaten(a)
        self.assertEqual((ld.bestand_gesamt, ld.durchschnittspreis), (Decimal("150"), Decimal("3.0000")))
        self.assertEqual(ld.bestandswert, Decimal("450.00"))
        self.assertEqual(services.verfuegbar(a), Decimal("150"))

    def test_abgang_ohne_bestand_wird_abgelehnt(self):
        services.buchen(Art.ZUGANG, self.artikel, 5, nach=(self.rl, None, FREI), preis=1)
        anzahl = Lagerbewegung.objects.count()
        with self.assertRaisesMessage(LagerFehler, "Nicht genügend Bestand"):
            services.buchen(Art.ABGANG, self.artikel, 6, von=(self.rl, None, FREI))
        self.assertEqual(Lagerbewegung.objects.count(), anzahl)
        self.assertEqual(services.verfuegbar(self.artikel), Decimal("5"))
        with self.assertRaises(LagerFehler):
            services.buchen(Art.ZUGANG, self.artikel, 0, nach=(self.rl, None, FREI))

    def test_umbuchung_ohne_werteffekt_und_sperrbestand(self):
        a = self.artikel
        services.buchen(Art.ZUGANG, a, 10, nach=(self.rl, self.platz, GESPERRT), preis=2)
        self.assertEqual(services.verfuegbar(a), 0)
        u = services.buchen(Art.UMBUCHUNG, a, 10, von=(self.rl, self.platz, GESPERRT), nach=(self.fl, None, FREI))
        self.assertEqual(u.wert, 0)
        self.assertEqual(services.bestand_summen(a), {FREI: Decimal("10"), GESPERRT: Decimal("0")})
        with self.assertRaises(LagerFehler):  # Platz gehört nicht zum Lagerort
            services.buchen(Art.ZUGANG, a, 1, nach=(self.fl, self.platz, FREI), preis=1)

    def test_chargenpflicht(self):
        r1000 = Artikel.objects.get(artikelnummer="R-1000")
        with self.assertRaisesMessage(LagerFehler, "chargenpflichtig"):
            services.buchen(Art.ZUGANG, r1000, 10, nach=(self.rl, None, FREI), preis=1)
        charge = services.charge_holen(r1000, "CH-TEST", anlegen=True)
        services.buchen(Art.ZUGANG, r1000, 10, nach=(self.rl, None, FREI), preis=1, charge=charge)
        self.assertEqual(Bestand.objects.get(artikel=r1000, charge=charge).menge, Decimal("10"))

    def test_journal_unveraenderlich(self):
        b = services.buchen(Art.ZUGANG, self.artikel, 1, nach=(self.rl, None, FREI), preis=1)
        with self.assertRaises(LagerFehler):
            b.save()
        with self.assertRaises(LagerFehler):
            b.delete()
        for sql in ("UPDATE lager_lagerbewegung SET menge = 99 WHERE id = %s",
                    "DELETE FROM lager_lagerbewegung WHERE id = %s"):
            with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as c:
                c.execute(sql, [b.pk])
        b.refresh_from_db()
        self.assertEqual(b.menge, Decimal("1"))

    def test_inventur(self):
        a = self.artikel
        services.buchen(Art.ZUGANG, a, 20, nach=(self.fl, None, FREI), preis=Decimal("2.50"))
        inv = services.inventur_anlegen(self.fl, benutzer=self.admin)
        self.assertTrue(inv.nummer.startswith("INV-"))
        pos = inv.positionen.get(artikel=a)
        pos.istmenge = Decimal("17")
        pos.save()
        self.assertEqual(services.inventur_buchen(inv, self.admin), 1)
        self.assertEqual(services.verfuegbar(a), Decimal("17"))
        bew = Lagerbewegung.objects.filter(artikel=a, art=Art.INVENTUR).get()
        self.assertEqual(bew.wert, Decimal("-7.50"))
        with self.assertRaises(LagerFehler):
            services.inventur_buchen(inv, self.admin)

    def test_unterschreitung_meldebestand(self):
        nummern = {u["artikel"].artikelnummer for u in services.unterschreitungen()}
        self.assertIn("R-1020", nummern)       # Demo: 3.100 Stk frei < Meldebestand 5.000
        self.assertNotIn("B-3100", nummern)

    def test_rechte_web_und_api(self):
        User = get_user_model()
        self.client.force_login(User.objects.get(username="gast"))
        self.assertEqual(self.client.get("/lager/").status_code, 200)
        self.assertEqual(self.client.get("/lager/umbuchen/").status_code, 403)
        antwort = self.client.post("/api/v1/lagerbewegungen/", {}, content_type="application/json")
        self.assertEqual(antwort.status_code, 403)
        self.client.force_login(User.objects.get(username="vertrieb"))
        self.assertEqual(self.client.get("/api/v1/bestaende/").status_code, 403)
        self.client.force_login(User.objects.get(username="lager"))
        self.assertEqual(self.client.get("/lager/umbuchen/").status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/bestaende/?artikel={self.artikel.pk}").status_code, 200)

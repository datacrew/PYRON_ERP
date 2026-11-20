"""Tests Einkauf: Freigabe/Nummernkreis, Storno, Web-Erfassung, Bestellvorschlag, Rechte, Bestell-PDF."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.einkauf import services
from apps.einkauf.models import Bestellung, BestellPosition, EinkaufFehler
from apps.einkauf.pdf import bestellung_pdf
from apps.lager.models import Lagerort
from apps.stammdaten.models import Artikel, Geschaeftspartner


def bestellung_anlegen(lieferant, positionen, typ=Bestellung.Typ.BESTELLUNG, benutzer=None):
    """Hilfsfunktion (auch für Wareneingangstests): positionen = [(Artikelnummer, Menge, Preis|None)]."""
    b = Bestellung.objects.create(typ=typ, lieferant=lieferant, zahlungsbedingung=lieferant.zahlungsbedingung,
                                  lieferort=Lagerort.objects.get(code="RL"), erstellt_von=benutzer)
    for i, (nr, menge, preis) in enumerate(positionen, start=1):
        services.position_vorbelegen(BestellPosition(
            bestellung=b, position=i * 10, artikel=Artikel.objects.get(artikelnummer=nr), menge=Decimal(menge),
            einzelpreis=None if preis is None else Decimal(preis), steuersatz=None)).save()
    b.berechne_summen()
    b.save()
    return b


class EinkaufTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("erp_demodaten", verbosity=0)
        User = get_user_model()
        cls.einkauf = User.objects.get(username="einkauf")
        cls.schmidt = Geschaeftspartner.objects.get(name="Stahlhandel Schmidt GmbH")

    def test_summen_und_freigabe_mit_nummernkreis(self):
        b = bestellung_anlegen(self.schmidt, [("R-1010", "100", "4.80"), ("R-1000", "1000", None)])
        self.assertEqual(b.summe_netto, Decimal("1630.00"))     # 480,00 + 1.150,00
        self.assertEqual(b.summe_brutto, Decimal("1939.70"))
        self.assertEqual((b.nummer, b.status), ("", Bestellung.Status.ENTWURF))
        b = services.freigeben(b, self.einkauf)
        self.assertRegex(b.nummer, r"^EK-\d{4}-\d+$")
        self.assertEqual(b.status, Bestellung.Status.BESTELLT)
        self.assertEqual(b.partner_daten["name"], self.schmidt.name)
        with self.assertRaises(EinkaufFehler):
            services.freigeben(b, self.einkauf)
        anfrage = services.freigeben(bestellung_anlegen(self.schmidt, [("W-3000", "1", "0")],
                                                        typ=Bestellung.Typ.ANFRAGE), self.einkauf)
        self.assertTrue(anfrage.nummer.startswith("EA-"))
        self.assertEqual(anfrage.status, Bestellung.Status.ANGEFRAGT)
        folge = services.anfrage_in_bestellung(anfrage, self.einkauf)
        self.assertEqual((folge.typ, folge.positionen.count(), folge.anfrage), ("BESTELLUNG", 1, anfrage))

    def test_freigabe_ohne_positionen_und_storno(self):
        leer = Bestellung.objects.create(lieferant=self.schmidt)
        with self.assertRaisesMessage(EinkaufFehler, "ohne Positionen"):
            services.freigeben(leer)
        b = services.freigeben(bestellung_anlegen(self.schmidt, [("R-1010", "10", None)]))
        with self.assertRaisesMessage(EinkaufFehler, "Stornogrund"):
            services.stornieren(b, grund=" ")
        b = services.stornieren(b, grund="Lieferant kann nicht liefern")
        self.assertEqual(b.status, Bestellung.Status.STORNIERT)

    def test_bestellung_ueber_weboberflaeche(self):
        self.client.force_login(self.einkauf)
        artikel = Artikel.objects.get(artikelnummer="R-1010")
        daten = {"typ": "BESTELLUNG", "lieferant": self.schmidt.pk, "datum": "2026-11-20",
                 "lieferbedingung": "frei Haus", "pos-TOTAL_FORMS": "2", "pos-INITIAL_FORMS": "0",
                 "pos-MIN_NUM_FORMS": "0", "pos-MAX_NUM_FORMS": "1000",
                 "pos-0-artikel": artikel.pk, "pos-0-menge": "250", "pos-0-liefertermin": "2026-12-01"}
        antwort = self.client.post(reverse("einkauf:neu"), daten)
        self.assertEqual(antwort.status_code, 302, getattr(antwort, "context", None) and
                         [antwort.context["form"].errors, antwort.context["formset"].errors])
        b = Bestellung.objects.latest("pk")
        pos = b.positionen.get()
        self.assertEqual((pos.einzelpreis, pos.einheit, b.summe_netto), (Decimal("4.8000"), "m", Decimal("1200.00")))
        self.assertEqual(b.zahlungsbedingung, self.schmidt.zahlungsbedingung)
        self.assertEqual(self.client.post(reverse("einkauf:aktion", args=[b.pk, "freigeben"])).status_code, 302)
        b.refresh_from_db()
        self.assertEqual(b.status, Bestellung.Status.BESTELLT)
        self.assertEqual(self.client.get(reverse("einkauf:detail", args=[b.pk])).status_code, 200)

    def test_bestell_pdf(self):
        b = services.freigeben(bestellung_anlegen(self.schmidt, [("R-1000", "500", None)]))
        inhalt = bestellung_pdf(b)
        self.assertTrue(inhalt.startswith(b"%PDF"))
        self.client.force_login(self.einkauf)
        antwort = self.client.get(reverse("einkauf:pdf", args=[b.pk]))
        self.assertEqual(antwort["Content-Type"], "application/pdf")
        self.assertTrue(antwort.content.startswith(b"%PDF"))
        self.assertEqual(self.client.get(f"/api/v1/bestellungen/{b.pk}/pdf/").status_code, 200)

    def test_bestellvorschlag_aus_meldebestand(self):
        vorschlaege = {v["artikel"].artikelnummer: v for v in services.bestellvorschlaege()}
        # Demo: R-1010 frei 920 m < Meldebestand 1.000 → Vorschlag = Standard-Bestellmenge 500
        self.assertEqual(vorschlaege["R-1010"]["menge"], Decimal("500"))
        # R-1020 liegt unter Meldebestand, ist aber durch offene Bestellung gedeckt → kein Vorschlag
        self.assertNotIn("R-1020", vorschlaege)
        artikel = Artikel.objects.get(artikelnummer="R-1010")
        bestellungen, uebersprungen = services.vorschlaege_bestellen([artikel.pk], self.einkauf)
        self.assertEqual((len(bestellungen), uebersprungen), (1, []))
        self.assertEqual(bestellungen[0].lieferant, self.schmidt)
        self.assertNotIn("R-1010", {v["artikel"].artikelnummer for v in services.bestellvorschlaege()})

    def test_api_anlegen_und_freigeben(self):
        self.client.force_login(self.einkauf)
        artikel = Artikel.objects.get(artikelnummer="R-1020")
        antwort = self.client.post("/api/v1/bestellungen/", {
            "lieferant": self.schmidt.pk, "positionen": [{"artikel": artikel.pk, "menge": "100"}]},
            content_type="application/json")
        self.assertEqual(antwort.status_code, 201, antwort.content)
        daten = antwort.json()
        self.assertEqual((daten["status"], daten["summe_netto"]), ("ENTWURF", "6.00"))
        self.assertEqual(daten["zahlungsbedingung"], self.schmidt.zahlungsbedingung_id)
        freigabe = self.client.post(f"/api/v1/bestellungen/{daten['id']}/freigeben/")
        self.assertEqual(freigabe.status_code, 200)
        self.assertTrue(freigabe.json()["nummer"].startswith("EK-"))
        self.assertEqual(self.client.post(f"/api/v1/bestellungen/{daten['id']}/freigeben/").status_code, 409)

    def test_rechte(self):
        User = get_user_model()
        b = Bestellung.objects.filter(status=Bestellung.Status.ENTWURF).first()
        self.client.force_login(User.objects.get(username="gast"))
        self.assertEqual(self.client.get(reverse("einkauf:liste")).status_code, 200)
        self.assertEqual(self.client.get(reverse("einkauf:neu")).status_code, 403)
        self.assertEqual(self.client.post(reverse("einkauf:aktion", args=[b.pk, "freigeben"])).status_code, 403)
        self.assertEqual(self.client.post("/api/v1/bestellungen/", {}, content_type="application/json").status_code,
                         403)
        self.client.force_login(User.objects.get(username="lager"))   # Lager: Einkauf nur lesen
        self.assertEqual(self.client.get(reverse("einkauf:neu")).status_code, 403)
        self.client.force_login(User.objects.get(username="vertrieb"))  # kein Einkaufsrecht
        self.assertEqual(self.client.get(reverse("einkauf:liste")).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/bestellungen/").status_code, 403)
        b.refresh_from_db()
        self.assertEqual(b.status, Bestellung.Status.ENTWURF)

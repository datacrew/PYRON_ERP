"""Tests Produktion: Bedarf/Fehlteile, Stempellogik, Akkord, Material/Fertigmeldung, Rechte, PDF, API."""
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import Rolle
from apps.lager import services as lager
from apps.lager.models import Bestand, Lagerbewegung, Lagerort
from apps.stammdaten.models import Artikel, Lohnart, Mitarbeiter

from . import services as fs
from .models import Fertigungsauftrag, ProduktionFehler, Stempelung

User = get_user_model()
S = Fertigungsauftrag.Status


def zeit(tage_zurueck, hhmm):
    h, m = map(int, hhmm.split(":"))
    tag = timezone.localdate() - timedelta(days=tage_zurueck)
    return timezone.make_aware(datetime.combine(tag, time(h, m)), timezone.get_current_timezone())


class ProduktionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("erp_demodaten", verbosity=0)
        cls.admin = User.objects.get(username="admin")
        cls.prod = User.objects.get(username="produktion")
        cls.art = {a.artikelnummer: a for a in Artikel.objects.all()}
        cls.fl = Lagerort.objects.get(code="FL")
        cls.ma = Mitarbeiter.objects.create(personalnummer="T9001", ausweisnummer="X9001", vorname="Test",
                                            nachname="Werker", lohngruppe="LG5", stundenlohn=Decimal("16.00"),
                                            akkordrichtsatz=Decimal("18.00"))
        cls.ma2 = Mitarbeiter.objects.create(personalnummer="T9002", vorname="Mini", nachname="Lohn",
                                             lohngruppe="LG3", stundenlohn=Decimal("12.00"),
                                             akkordrichtsatz=Decimal("13.00"))

    def _fa(self, nr="F-5000", menge=10, freigeben=True, **kw):
        fa = fs.fa_anlegen(self.art[nr], menge, benutzer=self.prod, **kw)
        if freigeben:
            fa, _ = fs.freigeben(fa, self.prod)
        return fa

    # ------------------------------------------------ Demodaten
    def test_demodaten(self):
        self.assertGreaterEqual(Fertigungsauftrag.objects.count(), 3)
        self.assertTrue(Fertigungsauftrag.objects.filter(status=S.ABGESCHLOSSEN).exists())
        self.assertTrue(Stempelung.objects.filter(ende__isnull=True).exists())
        self.assertEqual(Mitarbeiter.objects.filter(personalnummer__startswith="P10").count(), 5)

    # ------------------------------------------------ Bedarf / Fehlteile / Freigabe
    def test_bedarf_und_freigabe_kopiert(self):
        fa = self._fa(freigeben=False, menge=10)
        bedarf = {a.artikelnummer: m for a, m in fs.materialbedarf(fa)}
        self.assertEqual(bedarf, {"H-2000": Decimal("20.000"), "R-1020": Decimal("40.000")})
        fa, unter = fs.freigeben(fa, self.prod)
        self.assertEqual(fa.status, S.FREIGEGEBEN)
        self.assertEqual(fa.materialien.count(), 2)
        self.assertEqual(list(fa.arbeitsgaenge.values_list("position", flat=True)), [10, 20, 30, 40])
        ag = fa.arbeitsgaenge.get(position=20)
        self.assertEqual(ag.code, f"{fa.nummer}-20")
        self.assertEqual(ag.vorgabe_min(), Decimal("30") + Decimal("0.35") * 10)
        self.assertEqual(unter, [])
        with self.assertRaises(ProduktionFehler):
            fs.freigeben(fa, self.prod)

    def test_fehlteile_und_unterauftraege(self):
        fa = self._fa("F-5010", 1000, freigeben=False)
        fehl = {z["artikel"].artikelnummer: z for z in fs.fehlteile(fa, nur_fehlend=True)}
        self.assertIn("H-2010", fehl)                          # kein Bestand → Fehlteil
        self.assertTrue(fehl["H-2010"]["eigenfertigung"])
        frei = lager.verfuegbar(self.art["F-5000"])
        self.assertEqual(fehl["F-5000"]["fehlmenge"], Decimal(1000) - frei)
        fa, unter = fs.freigeben(fa, self.prod, unterauftraege=True)
        artikel = {u.artikel.artikelnummer for u in unter}
        self.assertEqual(artikel, {"H-2010", "F-5000"})
        h = next(u for u in unter if u.artikel.artikelnummer == "H-2010")
        self.assertEqual(h.uebergeordnet, fa)
        self.assertEqual(h.status, S.ENTWURF)
        self.assertEqual(fa.materialien.get(artikel=self.art["H-2010"]).unterauftrag, h)

    def test_nur_eigenfertigungsteile(self):
        with self.assertRaises(ProduktionFehler):
            fs.fa_anlegen(self.art["R-1000"], 5)

    def test_stornieren(self):
        fa = self._fa()
        with self.assertRaises(ProduktionFehler):
            fs.stornieren(fa, self.prod, "")
        fs.stornieren(fa, self.prod, "Kunde storniert")
        fa.refresh_from_db()
        self.assertEqual(fa.status, S.STORNIERT)

    # ------------------------------------------------ Stempellogik
    def test_stempeln_keine_ueberlappung(self):
        fa = self._fa()
        ag10, ag20 = fa.arbeitsgaenge.get(position=10), fa.arbeitsgaenge.get(position=20)
        fs.kommen(self.ma, zeit(1, "06:00"))
        with self.assertRaises(ProduktionFehler):
            fs.kommen(self.ma, zeit(1, "06:01"))          # doppelt Kommen
        fs.stempel_start(self.ma, ag10, zeit=zeit(1, "06:10"))
        with self.assertRaises(ProduktionFehler):        # zweiter Auftrag parallel
            fs.stempel_start(self.ma, ag20, zeit=zeit(1, "06:20"))
        st, rm = fs.stempel_beenden(self.ma, Stempelung.EndeArt.UNTERBRECHUNG, zeit=zeit(1, "07:00"), gut=5)
        self.assertEqual(st.dauer_min, Decimal(50))
        self.assertEqual(rm.gut_menge, 5)
        ag10.refresh_from_db()
        self.assertEqual(ag10.status, ag10.Status.UNTERBROCHEN)
        with self.assertRaises(ProduktionFehler):        # Start innerhalb einer vorhandenen Stempelung
            fs.stempel_start(self.ma, ag20, zeit=zeit(1, "06:30"))
        fs.stempel_start(self.ma, ag10, zeit=zeit(1, "07:10"))
        fs.stempel_beenden(self.ma, Stempelung.EndeArt.ENDE, zeit=zeit(1, "07:40"), gut=5)
        ag10.refresh_from_db()
        self.assertEqual((ag10.status, ag10.gut_menge), (ag10.Status.ERLEDIGT, Decimal(10)))
        fa.refresh_from_db()
        self.assertEqual(fa.status, S.TEILGEFERTIGT)

    def test_gehen_unterbricht_und_db_constraint(self):
        fa = self._fa()
        ag = fa.arbeitsgaenge.get(position=10)
        fs.stempel_start(self.ma, ag, zeit=zeit(1, "06:00"))   # Kommen wird automatisch angelegt
        self.assertIsNotNone(fs.offene_anwesenheit(self.ma))
        a, st = fs.gehen(self.ma, zeit(1, "14:00"))
        st.refresh_from_db()
        self.assertEqual(st.ende_art, Stempelung.EndeArt.UNTERBRECHUNG)
        self.assertEqual(a.dauer_min, Decimal(480))
        Stempelung.objects.create(mitarbeiter=self.ma, arbeitsgang=ag, beginn=zeit(0, "06:00"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Stempelung.objects.create(mitarbeiter=self.ma, arbeitsgang=ag, beginn=zeit(0, "06:05"))

    def test_terminal_buchen(self):
        fa = self._fa()
        code = fa.arbeitsgaenge.get(position=10).code
        ma, text = fs.terminal_buchen("X9001", "")            # Ausweis, ohne Code → Kommen
        self.assertIn("Willkommen", text)
        fs.terminal_buchen("T9001", code.lower())              # auto → Start
        self.assertIsNotNone(fs.offene_stempelung(self.ma))
        with self.assertRaises(ProduktionFehler):
            fs.terminal_buchen("T9001", code)                  # gleicher Code erneut ohne Aktion
        with self.assertRaises(ProduktionFehler):
            fs.terminal_buchen("T9001", "", "ende", ausschuss=2)  # Ausschuss ohne Grund
        _, text = fs.terminal_buchen("T9001", "", "ende", gut=3, ausschuss=1, grund="MASS")
        self.assertIn("gemeldet 3 gut", text)
        _, text = fs.terminal_buchen("T9001", "GEHEN")
        self.assertIn("Auf Wiedersehen", text)
        with self.assertRaises(ProduktionFehler):
            fs.terminal_buchen("UNBEKANNT", code)
        with self.assertRaises(ProduktionFehler):
            fs.arbeitsgang_finden("FA-1999-99999-10")

    def test_korrektur_mit_audit_und_ueberschneidung(self):
        from apps.core.models import AuditLog

        fa = self._fa()
        ag = fa.arbeitsgaenge.get(position=10)
        a = fs.stempelung_nachtragen(self.ma, ag, beginn=zeit(2, "06:00"), ende=zeit(2, "07:00"), grund="vergessen")
        b = fs.stempelung_nachtragen(self.ma, ag, beginn=zeit(2, "08:00"), ende=zeit(2, "09:00"), grund="vergessen")
        with self.assertRaises(ProduktionFehler):
            fs.stempelung_korrigieren(b, beginn=zeit(2, "06:30"), ende=zeit(2, "09:00"), grund="x")
        with self.assertRaises(ProduktionFehler):
            fs.stempelung_korrigieren(b, beginn=zeit(2, "07:30"), ende=zeit(2, "09:00"), grund="")
        fs.stempelung_korrigieren(b, beginn=zeit(2, "07:30"), ende=zeit(2, "09:00"), grund="Uhr falsch",
                                  benutzer=self.admin)
        b.refresh_from_db()
        self.assertTrue(b.korrigiert)
        self.assertIn("Uhr falsch", b.korrektur_grund)
        self.assertTrue(AuditLog.objects.filter(objekt_id=str(b.pk), modell__icontains="stempelung").exists())
        self.assertTrue(a.korrigiert)

    # ------------------------------------------------ Akkord
    def test_akkord_leistungsgrad_verdienst(self):
        fa = self._fa(menge=100)
        ag20 = fa.arbeitsgaenge.get(position=20)                 # Rüst 30, te 0,35
        fs.stempel_start(self.ma, ag20, Stempelung.Taetigkeit.RUESTEN, zeit=zeit(3, "06:00"))
        fs.stempel_beenden(self.ma, zeit=zeit(3, "06:30"))
        fs.stempel_start(self.ma, ag20, zeit=zeit(3, "06:30"))
        fs.stempel_beenden(self.ma, zeit=zeit(3, "07:00"), gut=100)
        tag = timezone.localdate() - timedelta(days=3)
        z = fs.akkord_auswertung(tag, tag, mitarbeiter=self.ma)["zeilen"][0]
        self.assertEqual(z["vorgabe_min"], Decimal("65.00"))      # 100 × 0,35 + 30
        self.assertEqual(z["ist_akkord_min"], Decimal(60))
        self.assertEqual(z["leistungsgrad"], Decimal("108.3"))
        self.assertEqual(z["akkordverdienst"], Decimal("19.50"))  # 65 × 18 / 60
        self.assertEqual(z["garantie"], Decimal("16.00"))          # 1 h × 16,00
        self.assertEqual(z["ausgleich"], 0)
        self.assertEqual(z["gesamt"], Decimal("19.50"))

    def test_akkord_mindestlohngarantie_und_zeitlohn(self):
        fa = self._fa(menge=100)
        ag30, ag40 = fa.arbeitsgaenge.get(position=30), fa.arbeitsgaenge.get(position=40)
        self.assertEqual(ag40.lohnart, Lohnart.ZEITLOHN)
        fs.stempel_start(self.ma2, ag30, zeit=zeit(3, "06:00"))
        fs.stempel_beenden(self.ma2, zeit=zeit(3, "08:00"), gut=20)     # Vorgabe 50 min in 120 min
        fs.stempel_start(self.ma2, ag40, zeit=zeit(3, "08:00"))
        fs.stempel_beenden(self.ma2, zeit=zeit(3, "08:30"), gut=20)
        tag = timezone.localdate() - timedelta(days=3)
        with override_settings(ERP_MINDESTLOHN=Decimal("13.90")):
            a = fs.akkord_auswertung(tag, tag, mitarbeiter=self.ma2)
        z = a["zeilen"][0]
        self.assertEqual(z["vorgabe_min"], Decimal("50.00"))
        self.assertEqual(z["leistungsgrad"], Decimal("41.7"))
        self.assertEqual(z["akkordverdienst"], Decimal("10.83"))       # 50 × 13 / 60
        self.assertEqual(z["garantiesatz"], Decimal("13.90"))          # Grundlohn 12 < Mindestlohn
        self.assertEqual(z["garantie"], Decimal("27.80"))              # 2 h × 13,90
        self.assertEqual(z["ausgleich"], Decimal("16.97"))
        self.assertEqual(z["zeitlohn"], Decimal("6.95"))               # 0,5 h × 13,90
        self.assertEqual(z["gesamt"], Decimal("34.75"))
        self.assertTrue(z["mindestlohn_angehoben"])
        self.assertGreaterEqual(z["stundenverdienst"], Decimal("13.90"))
        csv = fs.akkord_csv(a)
        self.assertIn("T9002;Mini Lohn", csv)
        self.assertIn("16,97", csv)

    # ------------------------------------------------ Material / Fertigmeldung / Kalkulation
    def test_entnahme_chargenpflicht_und_lager(self):
        fa = self._fa("H-2000", 10)
        m = fa.materialien.get()
        self.assertTrue(m.artikel.chargenpflicht)
        with self.assertRaises(ProduktionFehler):
            fs.material_entnehmen(fa, m, Decimal("7.2"))            # ohne Charge
        bestand = fs.entnahme_bestaende(m.artikel).filter(charge__chargennummer="CH-2026-0001").first()
        vorher = bestand.menge
        frei_vorher = lager.verfuegbar(m.artikel)
        e = fs.material_entnehmen(fa, m, Decimal("7.2"), bestand=bestand, benutzer=self.prod)[0]
        bestand.refresh_from_db()
        self.assertEqual(bestand.menge, vorher - Decimal("7.2"))
        self.assertEqual(lager.verfuegbar(m.artikel), frei_vorher - Decimal("7.2"))
        self.assertEqual(e.bewegung.art, Lagerbewegung.Art.ABGANG)
        self.assertEqual(e.bewegung.charge.chargennummer, "CH-2026-0001")
        self.assertGreater(e.wert, 0)
        fa.refresh_from_db()
        self.assertEqual(fa.status, S.IN_ARBEIT)

    def test_fertigmeldung_herstellkosten_und_nachkalkulation(self):
        fa = self._fa("F-5000", 10)
        erledigt, ueber = fs.restmaterial_entnehmen(fa, self.prod)
        self.assertEqual(len(erledigt) + len(ueber), 2, ueber)
        self.assertTrue(erledigt, ueber)
        material = sum(e.wert for e in fa.entnahmen.all())
        ag = fa.arbeitsgaenge.get(position=30)                    # Schweißplatz 38 €/h
        fs.stempel_start(self.ma, ag, zeit=zeit(1, "06:00"))
        fs.stempel_beenden(self.ma, zeit=zeit(1, "07:00"), gut=10)
        ist = fs.ist_kosten(fa)
        self.assertEqual(ist["lohn"], Decimal("18.00"))           # 1 h × Richtsatz 18
        self.assertEqual(ist["platz"], Decimal("38.00"))
        self.assertEqual(ist["material"], material)
        bestand_vorher = lager.verfuegbar(fa.artikel)
        fm = fs.fertigmelden(fa, 4, lagerort=self.fl, benutzer=self.prod)   # Teilmeldung 4 von 10
        self.assertEqual(fm.lohnkosten, Decimal("7.20"))          # anteilig 4/10
        fm2 = fs.fertigmelden(fa, 6, lagerort=self.fl, benutzer=self.prod)
        fa.refresh_from_db()
        self.assertEqual(fa.status, S.ABGESCHLOSSEN)
        self.assertEqual(fm.lohnkosten + fm2.lohnkosten, Decimal("18.00"))
        self.assertEqual(lager.verfuegbar(fa.artikel), bestand_vorher + 10)
        self.assertEqual(fm.bewegung.art, Lagerbewegung.Art.ZUGANG)
        self.assertTrue(fm.charge.chargennummer.startswith("FC-"))
        self.assertEqual(fm.bewegung.preis if hasattr(fm.bewegung, "preis") else fm.herstellkosten_stueck,
                         fm.herstellkosten_stueck)
        k = fs.nachkalkulation(fa)
        gesamt = k["zeilen"][-1]
        self.assertEqual(gesamt["ist"], ist["summe"])
        self.assertEqual(k["aktiviert"].quantize(Decimal("0.01")), ist["summe"])
        with self.assertRaises(ProduktionFehler):
            fs.fertigmelden(fa, 1, lagerort=self.fl)               # abgeschlossen

    def test_events(self):
        from apps.core import events

        empfangen = []
        def empfaenger(sender, **kw):
            if sender == "produktion.fertiggemeldet":
                empfangen.append(kw)

        events._ereignis.connect(empfaenger, weak=False, dispatch_uid="test-fertiggemeldet")
        try:
            fa = self._fa("F-5000", 2)
            fs.fertigmelden(fa, 2, lagerort=self.fl)
        finally:
            events._ereignis.disconnect(dispatch_uid="test-fertiggemeldet")
        self.assertEqual(len(empfangen), 1)
        self.assertEqual(empfangen[0]["auftrag"], fa)

    # ------------------------------------------------ Web, Rechte, PDF, API
    def test_seiten_und_pdf(self):
        self.client.force_login(self.admin)
        fa = Fertigungsauftrag.objects.get(nummer__endswith="00002")
        for name, args in [("produktion:index", []), ("produktion:liste", []), ("produktion:neu", []),
                           ("produktion:detail", [fa.pk]), ("produktion:entnahme", [fa.pk]),
                           ("produktion:fertigmelden", [fa.pk]), ("produktion:nachkalkulation", [fa.pk]),
                           ("produktion:rueckmeldungen", []), ("produktion:terminal", []),
                           ("produktion:stempelungen", []), ("produktion:akkord", []),
                           ("produktion:mitarbeiter_liste", []), ("stammdaten:arbeitsplatz_liste", [])]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, name)
        st = Stempelung.objects.filter(ende__isnull=False).first()
        self.assertEqual(self.client.get(reverse("produktion:stempel_korrektur", args=[st.pk])).status_code, 200)
        r = self.client.get(reverse("produktion:laufkarte", args=[fa.pk]))
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))
        entwurf = Fertigungsauftrag.objects.filter(status=S.ENTWURF).first()
        self.assertTrue(self.client.get(reverse("produktion:laufkarte", args=[entwurf.pk])).content.startswith(b"%PDF"))
        r = self.client.get(reverse("produktion:akkord_pdf"))
        self.assertTrue(r.content.startswith(b"%PDF"))
        r = self.client.get(reverse("produktion:akkord_csv"))
        self.assertIn("Personalnr.", r.content.decode("utf-8-sig"))

    def test_weboberflaeche_ablauf(self):
        self.client.force_login(self.prod)
        r = self.client.post(reverse("produktion:neu"), {"artikel": self.art["F-5000"].pk, "menge": "5"})
        fa = Fertigungsauftrag.objects.order_by("-pk").first()
        self.assertRedirects(r, reverse("produktion:detail", args=[fa.pk]))
        self.client.post(reverse("produktion:aktion", args=[fa.pk, "freigeben"]))
        fa.refresh_from_db()
        self.assertEqual(fa.status, S.FREIGEGEBEN)
        self.client.post(reverse("produktion:entnahme", args=[fa.pk]), {"alles": "1"})
        self.assertTrue(fa.entnahmen.exists())
        code = fa.arbeitsgaenge.get(position=10).code
        r = self.client.post(reverse("produktion:terminal"), {"kennung": "T9001", "code": code, "aktion": "start"})
        self.assertContains(r, "gestartet")
        r = self.client.post(reverse("produktion:terminal"), {"kennung": "T9001", "aktion": "ende", "gut": "5"})
        self.assertContains(r, "gemeldet 5 gut")

    def test_rechte(self):
        fa = Fertigungsauftrag.objects.first()
        # Produktion: Aufträge + Terminal, aber kein Akkord
        self.client.force_login(self.prod)
        self.assertEqual(self.client.get(reverse("produktion:index")).status_code, 200)
        self.assertEqual(self.client.get(reverse("produktion:terminal")).status_code, 200)
        self.assertEqual(self.client.get(reverse("produktion:akkord")).status_code, 403)
        self.assertEqual(self.client.get(reverse("produktion:mitarbeiter_liste")).status_code, 403)
        self.assertEqual(self.client.get(reverse("produktion:stempel_korrektur",
                                                 args=[Stempelung.objects.first().pk])).status_code, 403)
        # Gast: lesen ja, Akkord/Terminal-Buchung nein
        self.client.force_login(User.objects.get(username="gast"))
        self.assertEqual(self.client.get(reverse("produktion:detail", args=[fa.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("produktion:akkord")).status_code, 403)
        self.assertEqual(self.client.post(reverse("produktion:neu"), {}).status_code, 403)
        # Terminal-Rolle: nur Terminal
        t = User.objects.create_user("terminal1", password="x")
        t.erp_rollen.add(Rolle.objects.get(name="Terminal"))
        self.client.force_login(t)
        self.assertEqual(self.client.get(reverse("produktion:terminal")).status_code, 200)
        self.assertEqual(self.client.get(reverse("produktion:index")).status_code, 403)
        self.assertEqual(self.client.get(reverse("produktion:akkord")).status_code, 403)
        # Fertigungsleitung: Akkord und Korrektur
        fl = User.objects.create_user("leitung1", password="x")
        fl.erp_rollen.add(Rolle.objects.get(name="Fertigungsleitung"))
        self.client.force_login(fl)
        self.assertEqual(self.client.get(reverse("produktion:akkord")).status_code, 200)

    def test_api(self):
        from rest_framework.test import APIClient

        api = APIClient()
        api.force_authenticate(self.prod)
        r = api.post("/api/v1/fertigungsauftraege/", {"artikel": self.art["F-5000"].pk, "menge": "3"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        fa_id = r.data["id"]
        api.force_authenticate(User.objects.get(username="gast"))
        self.assertEqual(api.post(f"/api/v1/fertigungsauftraege/{fa_id}/freigeben/").status_code, 403)  # kein Recht
        api.force_authenticate(self.admin)
        r = api.post(f"/api/v1/fertigungsauftraege/{fa_id}/freigeben/")
        self.assertEqual(r.data["status"], "FREIGEGEBEN")
        code = r.data["arbeitsgaenge"][0]["code"]
        self.assertEqual(api.post(f"/api/v1/fertigungsauftraege/{fa_id}/freigeben/").status_code, 409)
        api.force_authenticate(self.prod)
        r = api.post("/api/v1/stempelungen/", {"personalnummer": "T9001", "code": code}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(r.data["angestempelt"])
        self.assertEqual(r.data["stempelung"]["quelle"], "API")
        r = api.post("/api/v1/stempelungen/", {"personalnummer": "T9001", "aktion": "ende", "gut": "3"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(api.post("/api/v1/stempelungen/", {"personalnummer": "T9001", "aktion": "ende"},
                                  format="json").status_code, 409)
        st_id = r.data["stempelung"]["id"]
        self.assertEqual(api.patch(f"/api/v1/stempelungen/{st_id}/korrektur/", {"grund": "x"}).status_code, 403)
        self.assertEqual(api.get(f"/api/v1/rueckmeldungen/?auftrag={fa_id}").data["count"]
                         if isinstance(api.get(f"/api/v1/rueckmeldungen/?auftrag={fa_id}").data, dict)
                         else len(api.get(f"/api/v1/rueckmeldungen/?auftrag={fa_id}").data), 1)
        self.assertEqual(api.get(f"/api/v1/fertigungsauftraege/{fa_id}/fehlteile/").status_code, 200)

"""Tests Versand (Phase 5): Regionen/Liefertage, Tourenbildung mit Kapazitätsprüfung, Sortierung,
Verladen/Zustellung, LVS-Export und -Rückmeldung (Datei und REST), Tourenliste-PDF und Seiten."""
import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.stammdaten.models import Adresse, Artikel, Geschaeftspartner
from apps.vertrieb import services as VS
from apps.vertrieb.models import Lieferung

from . import services as S
from .models import Fahrzeug, LvsProtokoll, Region, Tour
from .views import tour_pdf_bytes

D = Decimal


class VersandBasis(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("erp_demodaten", verbosity=0)
        User = get_user_model()
        cls.admin = User.objects.get(username="admin")
        cls.lg = User.objects.get(username="lager")
        cls.vt = User.objects.get(username="vertrieb")
        cls.f5000 = Artikel.objects.get(artikelnummer="F-5000")
        cls.stgt = Region.objects.get(code="STGT")
        cls.neckar = Region.objects.get(code="NECKAR")
        # Testtag: nächster Liefertag beider Regionen nach den Demotouren (Di oder Do, NECKAR liefert nur Di/Do)
        cls.tag = S.naechster_liefertag(cls.neckar, timezone.localdate() + timedelta(days=8))

    def lieferung(self, plz, menge=2, kg=None, paletten="1", buchen=False, name=None):
        k = Geschaeftspartner.objects.create(name=name or f"Kunde {plz}", ist_kunde=True)
        Adresse.objects.create(partner=k, typ="HAUPT", strasse="Teststraße 1", plz=plz, ort="Testort")
        a = VS.auftrag_bestaetigen(VS.auftrag_anlegen(k, [{"artikel": self.f5000, "menge": menge}], self.vt), self.vt)
        lf = VS.lieferung_anlegen(a, None, self.lg, lieferdatum=self.tag)
        lf.gewicht_kg, lf.paletten = (D(kg) if kg is not None else None), D(paletten)
        lf.save()
        return VS.warenausgang_buchen(lf, self.lg) if buchen else lf


class TourenTests(VersandBasis):
    def test_region_und_liefertage(self):
        self.assertEqual(S.region_fuer("70173").code, "STGT")
        self.assertEqual(S.region_fuer("73728").code, "NECKAR")
        self.assertEqual(S.region_fuer("85049").code, "BAYERN")
        self.assertIsNone(S.region_fuer("10115"))
        spezial = Region.objects.create(code="ESS", name="Esslingen", plz_bereiche="737", liefertage="1")
        self.assertEqual(S.region_fuer("73728"), spezial)  # spezifischerer PLZ-Bereich gewinnt
        self.assertEqual(S.naechster_liefertag(self.neckar, self.tag).isoweekday() in (2, 4), True)

    def test_tourenbildung_kapazitaet_und_sortierung(self):
        Fahrzeug.objects.filter(kennzeichen="S-MW 102").update(max_kg=500, max_paletten=4)
        a = self.lieferung("71634", kg="300", name="Ludwigsburg")
        b = self.lieferung("70469", kg="150", name="Feuerbach")
        c = self.lieferung("70499", kg="250", name="Weilimdorf")
        ohne = self.lieferung("10115", kg="10", name="Berlin")
        erg = S.touren_bilden(self.tag, self.lg)
        stgt = [t for t in erg["touren"] if t.region == self.stgt]
        self.assertEqual(len(stgt), 2)  # 700 kg passen nicht in einen 500-kg-Sprinter
        for t in stgt:
            self.assertEqual(S.kapazitaet_pruefen(t), [])
        alle = {s.lieferung_id for t in stgt for s in t.stopps.all()}
        self.assertEqual(alle, {a.pk, b.pk, c.pk})
        self.assertIn(ohne.pk, [lf.pk for lf, _ in erg["nicht_zugeordnet"]])
        # Sortierung nach Entfernung ab Firmensitz 70565: Weilimdorf (≈10 km) vor Ludwigsburg (≈19 km)
        reihen = S.sortieren([a, c], "ENTFERNUNG")
        self.assertEqual([lf.pk for lf, _ in reihen], [c.pk, a.pk])
        self.assertTrue(all(km is not None for _, km in reihen))
        self.assertEqual([lf.pk for lf, _ in S.sortieren([a, c], "PLZ")], [c.pk, a.pk])

    def test_kapazitaet_manuell_und_liefertag(self):
        t = Tour.objects.create(datum=self.tag, region=self.stgt, fahrzeug=Fahrzeug.objects.get(kennzeichen="S-MW 102"))
        gross = self.lieferung("70173", kg="2000", paletten="2")
        with self.assertRaises(S.VersandFehler):
            S.stopp_hinzufuegen(t, gross)
        S.stopp_hinzufuegen(t, gross, erzwingen=True)
        self.assertTrue(S.kapazitaet_pruefen(t))
        with self.assertRaises(S.VersandFehler):
            S.tour_freigeben(t)
        # Region ohne Belieferung am Tag → Hinweis statt Tour
        montag = self.tag - timedelta(days=self.tag.isoweekday() - 1)
        lf = self.lieferung("73728")
        lf.lieferdatum = montag
        lf.save()
        erg = S.touren_bilden(montag, self.lg, regionen=[self.neckar])
        self.assertIn(lf.pk, [x.pk for x, _ in erg["nicht_zugeordnet"]])

    def test_verladen_zustellung_und_pdf(self):
        lf1 = self.lieferung("70469", buchen=True)
        lf2 = self.lieferung("70499")
        t = S.touren_bilden(self.tag, self.lg, regionen=[self.stgt])["touren"][0]
        with self.assertRaises(S.VersandFehler):
            S.tour_verladen(t, self.lg)  # lf2 noch nicht gebucht
        VS.warenausgang_buchen(lf2, self.lg)
        S.tour_freigeben(t, self.lg)
        S.tour_verladen(t, self.lg)
        lf1.refresh_from_db()
        self.assertEqual(lf1.status, Lieferung.Status.VERLADEN)
        S.zugestellt(lf1, self.lg)
        lf1.refresh_from_db()
        self.assertEqual(lf1.status, Lieferung.Status.ZUGESTELLT)
        pdf = tour_pdf_bytes(t)
        self.assertTrue(pdf.startswith(b"%PDF"))
        with self.assertRaises(VS.VertriebFehler):
            VS.lieferung_stornieren(lf2, self.lg, "Test")  # auf Tour → erst entfernen


class LvsTests(VersandBasis):
    def test_export_json_csv_und_rueckmeldung(self):
        lf = self.lieferung("70173", menge=5)
        daten = json.loads(S.lvs_export([lf], "JSON", self.lg))["kommissionierauftraege"]
        self.assertEqual(daten[0]["referenz"], lf.kommissionier_referenz)
        lf.refresh_from_db()
        self.assertEqual(lf.status, Lieferung.Status.KOMMISSIONIERUNG)
        csv_text = S.lvs_export([lf], "CSV", self.lg)
        self.assertIn(lf.kommissionier_referenz, csv_text.splitlines()[1])
        pos = daten[0]["positionen"][0]["position_id"]
        # Fehler: Menge größer als Liefermenge → nichts verarbeitet, protokolliert
        ok, fehler = S.lvs_import({"rueckmeldungen": [{"referenz": lf.kommissionier_referenz, "positionen": [
            {"position_id": pos, "menge": "9"}]}]}, "JSON", self.lg)
        self.assertEqual((ok, len(fehler)), ([], 1))
        self.assertFalse(LvsProtokoll.objects.filter(richtung="IMPORT").latest("pk").erfolgreich)
        # CSV-Rückmeldung mit Fehlmenge (4 statt 5), Packstücken und sofortiger Buchung
        csv_rm = f"referenz;position_id;menge;packstueck;packstuecke\n{lf.kommissionier_referenz};{pos};4;P1;2\n"
        ok, fehler = S.lvs_import(csv_rm, "CSV", self.lg, buchen=True)
        self.assertEqual(fehler, [])
        lf.refresh_from_db()
        self.assertEqual((lf.status, lf.packstuecke), (Lieferung.Status.GEBUCHT, 2))
        ap = lf.positionen.get().auftrag_position
        self.assertEqual((ap.menge_geliefert, ap.offen_liefern), (D(4), D(1)))
        ok, fehler = S.lvs_import(csv_rm, "CSV", self.lg)
        self.assertTrue(fehler)  # bereits gebucht

    def test_rest_endpunkte_und_seiten(self):
        lf = self.lieferung("70173", menge=3)
        self.client.force_login(self.lg)
        r = self.client.get("/api/v1/versand/lvs/kommissionierauftraege/")
        self.assertEqual(r.status_code, 200)
        eintrag = next(x for x in r.json()["kommissionierauftraege"] if x["lieferschein_id"] == lf.pk)
        r = self.client.post("/api/v1/versand/lvs/rueckmeldungen/", {"buchen": True, "rueckmeldungen": [
            {"referenz": eintrag["referenz"], "packstuecke": 1,
             "positionen": [{"position_id": eintrag["positionen"][0]["position_id"], "menge": "3"}]}]},
            content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        lf.refresh_from_db()
        self.assertEqual(lf.status, Lieferung.Status.GEBUCHT)
        r = self.client.post("/api/v1/versand/touren/bilden/", {"datum": self.tag.isoformat()},
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["touren"])
        self.assertEqual(self.client.get("/api/v1/versand/touren/").status_code, 200)
        t = Tour.objects.first()
        for url in [reverse("versand:index"), reverse("versand:touren"), reverse("versand:planen"),
                    reverse("versand:lvs"), reverse("versand:tour", args=[t.pk]),
                    reverse("versand:tour_pdf", args=[t.pk]), reverse("versand:regionen_liste"),
                    reverse("versand:fahrzeuge_liste"), reverse("versand:plzkoordinaten_liste")]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.client.force_login(self.vt)  # Vertrieb: Versand nur lesen
        self.assertEqual(self.client.get(reverse("versand:index")).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/versand/lvs/kommissionierauftraege/").status_code, 403)

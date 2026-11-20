"""Tests Finanzbuchhaltung (Phase 4): Hauptbuch, Steuer, GoBD, Perioden, Automatik, OP/Skonto, Bank, Mahnwesen,
Auswertungen, DATEV und Rechte."""
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction
from django.test import TestCase
from django.urls import reverse

from apps.stammdaten.models import Geschaeftspartner

from . import auswertungen as A
from . import bank as BK
from . import mahnwesen as MW
from . import services as S
from .models import (Bankumsatz, Buchung, Buchungszeile, Eingangsrechnung, FibuGesperrt, Mahnung, OffenerPosten,
                     Periode)

D = Decimal
BEISPIELE = Path(settings.BASE_DIR) / "docs" / "beispiele"


def zeilen_summe(b, nr):
    s = Buchungszeile.objects.filter(buchung=b, konto__nummer=nr)
    return sum((z.soll - z.haben for z in s), D("0"))


class HauptbuchTests(TestCase):
    """Grundfunktionen ohne Demodaten (nur Grunddaten + Geschäftsjahr)."""

    @classmethod
    def setUpTestData(cls):
        call_command("erp_grunddaten", verbosity=0)
        S.geschaeftsjahr_anlegen(2026)
        cls.user = get_user_model().objects.create_user("fibutest", password="x")

    def test_soll_gleich_haben_erzwungen(self):
        with self.assertRaises(S.FibuFehler):
            S.buchen(date(2026, 3, 1), "T1", "Unausgeglichen", [{"konto": "1200", "soll": "100"},
                                                                {"konto": "8400", "haben": "90"}])
        with self.assertRaises(S.FibuFehler):
            S.buchen(date(2026, 3, 1), "T1", "Nur eine Zeile", [{"konto": "1200", "soll": "100"}])
        b = S.buchen(date(2026, 3, 1), "T2", "Splitbuchung", [
            {"konto": "1200", "soll": "300"}, {"konto": "8400", "haben": "200"}, {"konto": "2700", "haben": "100"}])
        self.assertEqual(b.zeilen.count(), 3)
        self.assertEqual(sum(z.soll for z in b.zeilen.all()), sum(z.haben for z in b.zeilen.all()))

    def test_steueraufteilung_19_7_und_brutto(self):
        b = S.buchen(date(2026, 3, 2), "T3", "Erlös 19 %", [
            {"konto": "1200", "soll": "119"}, {"konto": "8400", "haben": "119", "steuerschluessel": "U19",
                                               "brutto": True}])
        st = S.steuerschluessel("U19")
        self.assertEqual(zeilen_summe(b, "8400"), D("-100.00"))
        self.assertEqual(zeilen_summe(b, st.steuerkonto.nummer), D("-19.00"))
        b7 = S.buchen(date(2026, 3, 2), "T4", "Erlös 7 %", [
            {"konto": "1200", "soll": "107"}, {"konto": "8300", "haben": "100", "steuerschluessel": "U7"}])
        self.assertEqual(zeilen_summe(b7, S.steuerschluessel("U7").steuerkonto.nummer), D("-7.00"))

    def test_reverse_charge_13b_und_ige_saldoneutral(self):
        for code in ("RC19", "IGE19"):
            st = S.steuerschluessel(code)
            b = S.buchen(date(2026, 3, 3), f"T-{code}", f"Fremdleistung {code}", [
                {"konto": "3000", "soll": "1000", "steuerschluessel": code}, {"konto": "1600", "haben": "1000"}])
            self.assertEqual(zeilen_summe(b, st.steuerkonto.nummer), D("190.00"), code)
            self.assertEqual(zeilen_summe(b, st.gegenkonto.nummer), D("-190.00"), code)
            self.assertEqual(zeilen_summe(b, "1600"), D("-1000.00"))  # Lieferant erhält nur netto

    def test_gobd_festschreibung_trigger_und_storno(self):
        b = S.buchen(date(2026, 4, 1), "T5", "Festschreiben", [{"konto": "4210", "soll": "50"},
                                                               {"konto": "1200", "haben": "50"}])
        S.festschreiben([b], self.user)
        b.refresh_from_db()
        self.assertTrue(b.festgeschrieben)
        self.assertTrue(b.pruefsumme)
        b.text = "Geändert"
        with self.assertRaises(FibuGesperrt):
            b.save()
        with self.assertRaises(FibuGesperrt):
            b.delete()
        # Datenbank-Trigger greift auch ohne ORM
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as c:
                c.execute("UPDATE fibu_buchungszeile SET soll = 999 WHERE buchung_id = %s", [b.pk])
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as c:
                c.execute("DELETE FROM fibu_buchung WHERE id = %s", [b.pk])
        storno = S.stornieren(b, self.user, grund="Fehlbuchung")
        self.assertEqual(storno.storno_von, b)
        self.assertEqual(S.saldo(S.konto("4210")), D("0"))
        with self.assertRaises(S.FibuFehler):
            S.stornieren(b, self.user)
        S.festschreiben([storno], self.user)
        anzahl, fehler = S.kette_pruefen()
        self.assertEqual(fehler, [])
        self.assertGreater(anzahl, 0)

    def test_periodensperre(self):
        p = Periode.objects.get(geschaeftsjahr__jahr=2026, nummer=5)
        offen = S.buchen(date(2026, 5, 10), "T6", "Mai", [{"konto": "4210", "soll": "10"},
                                                          {"konto": "1200", "haben": "10"}])
        S.periode_sperren(p, self.user)
        offen.refresh_from_db()
        self.assertTrue(offen.festgeschrieben)  # Periodenabschluss schreibt offene Buchungen fest
        with self.assertRaises(FibuGesperrt):
            S.buchen(date(2026, 5, 11), "T7", "Gesperrt", [{"konto": "4210", "soll": "10"},
                                                            {"konto": "1200", "haben": "10"}])
        with self.assertRaises(S.FibuFehler):
            S.buchen(date(2031, 1, 2), "T8", "Ohne GJ", [{"konto": "4210", "soll": "10"},
                                                         {"konto": "1200", "haben": "10"}])
        S.periode_entsperren(p)
        S.buchen(date(2026, 5, 11), "T7", "Wieder offen", [{"konto": "4210", "soll": "10"},
                                                           {"konto": "1200", "haben": "10"}])

    def test_saldenvortrag(self):
        S.buchen(date(2026, 6, 1), "T9", "Erlös", [{"konto": "1200", "soll": "119"},
                                                   {"konto": "8400", "haben": "100", "steuerschluessel": "U19"}])
        sv = S.saldenvortrag(2026, self.user)
        self.assertEqual(sv.belegdatum, date(2027, 1, 1))
        self.assertEqual(S.saldo(S.konto("1200"), von=date(2027, 1, 1)), D("119.00"))
        self.assertEqual(S.saldo(S.automatikkonto("GEWINNVORTRAG"), von=date(2027, 1, 1)), D("-100.00"))
        self.assertEqual(S.saldenvortrag(2026, self.user), sv)  # idempotent

    def test_verzugszinsen_288_bgb(self):
        # 1.000 € × (1,52 % + 9 %) × 30/365
        self.assertEqual(MW.verzugszinssatz(date(2026, 11, 20), True), D("10.52"))
        self.assertEqual(MW.verzugszinssatz(date(2026, 11, 20), False), D("6.52"))
        self.assertEqual(MW.verzugszinsen(D("1000"), 30, D("10.52")), D("8.65"))
        self.assertEqual(MW.verzugszinsen(D("1000"), 0, D("10.52")), D("0"))


class ParserTests(TestCase):
    def test_camt053(self):
        erg = BK.parse_camt053((BEISPIELE / "camt053_demo.xml").read_bytes())
        self.assertEqual(erg["iban"], "DE02120300000000202051")
        self.assertEqual(erg["anfangssaldo"], D("91366.14"))
        self.assertEqual(erg["endsaldo"], D("93178.64"))
        self.assertEqual(len(erg["umsaetze"]), 4)
        self.assertEqual(erg["anfangssaldo"] + sum(u["betrag"] for u in erg["umsaetze"]), erg["endsaldo"])
        mueller = erg["umsaetze"][1]
        self.assertEqual(mueller["betrag"], D("8991.40"))
        self.assertIn("RE-2026-00005", mueller["verwendungszweck"])
        self.assertEqual(mueller["iban"], "DE89370400440532013000")
        self.assertEqual(erg["umsaetze"][0]["betrag"], D("-7616.00"))
        self.assertEqual(erg["umsaetze"][3]["verwendungszweck"], "Kontoführungsentgelt 11/2026")

    def test_mt940(self):
        erg = BK.parse_mt940((BEISPIELE / "mt940_demo.sta").read_bytes())
        self.assertEqual(erg["iban"], "DE02120300000000202051")
        self.assertEqual(len(erg["umsaetze"]), 8)
        self.assertEqual(erg["anfangssaldo"] + sum(u["betrag"] for u in erg["umsaetze"]), erg["endsaldo"])
        u = erg["umsaetze"][0]
        self.assertEqual((u["buchungsdatum"], u["betrag"], u["name"]), (date(2026, 7, 14), D("6747.30"),
                                                                        "Autoteile Weber AG"))
        self.assertIn("RE-2026-00001", u["verwendungszweck"])
        self.assertEqual(erg["umsaetze"][1]["betrag"], D("-12.90"))

    def test_format_erkennung_und_fehler(self):
        self.assertEqual(BK.format_erkennen((BEISPIELE / "camt053_demo.xml").read_bytes()), "CAMT053")
        self.assertEqual(BK.format_erkennen((BEISPIELE / "mt940_demo.sta").read_bytes()), "MT940")
        with self.assertRaises(BK.ParserFehler):
            BK.format_erkennen(b"irgendwas")
        with self.assertRaises(BK.ParserFehler):
            BK.parse_camt053(b"<kaputt")


class DemoFibuTests(TestCase):
    """Fachliche Abläufe auf Basis der Demodaten (Geschäftsjahr 2026 mit EB, AR/ER, MT940 abgearbeitet)."""

    @classmethod
    def setUpTestData(cls):
        call_command("erp_demodaten", verbosity=0)
        cls.bh = get_user_model().objects.get(username="buchhaltung")

    def op(self, nr):
        return OffenerPosten.objects.get(belegnr=nr)

    def test_demodaten_konsistent(self):
        self.assertEqual(Buchung.objects.filter(quelle="beleg").count(), 5)
        self.assertEqual(self.op("RE-2026-00001").offen, 0)
        self.assertEqual(self.op("RE-2026-00004").offen, D("3581.10"))  # Teilzahlung
        self.assertEqual(Bankumsatz.objects.filter(status="OFFEN").count(), 0)
        self.assertTrue(Periode.objects.get(geschaeftsjahr__jahr=2026, nummer=9).gesperrt)
        self.assertFalse(Buchung.objects.filter(belegdatum__lte=date(2026, 9, 30), status="ERFASST").exists())
        anzahl, fehler = S.kette_pruefen()
        self.assertEqual(fehler, [])
        self.assertGreater(anzahl, 0)

    def test_automatik_ar_idempotent(self):
        from apps.belege.models import Beleg

        beleg = Beleg.objects.get(nummer="RE-2026-00005")
        b = Buchung.objects.get(quelle="beleg", quelle_id=str(beleg.pk))
        anzahl = Buchung.objects.count()
        self.assertEqual(S.ar_buchen(beleg), b)
        from apps.fibu.ereignisse import beleg_festgeschrieben, nachbuchen

        beleg_festgeschrieben(beleg)
        nachbuchen()
        self.assertEqual(Buchung.objects.count(), anzahl)
        self.assertEqual(OffenerPosten.objects.filter(beleg=beleg).count(), 1)
        # Debitor an Erlöse 19 % und USt
        self.assertEqual(zeilen_summe(b, S.personenkonto(beleg.partner, "D").nummer), beleg.summe_brutto)
        self.assertEqual(zeilen_summe(b, "8400"), -beleg.summe_netto)
        self.assertEqual(zeilen_summe(b, "1776"), -(beleg.summe_brutto - beleg.summe_netto))

    def test_automatik_wareneingang_material_fertigmeldung(self):
        we_b = Buchung.objects.get(quelle="wareneingang")
        self.assertGreater(zeilen_summe(we_b, "3970"), 0)
        self.assertEqual(zeilen_summe(we_b, "3970"), -zeilen_summe(we_b, S.automatikkonto("WERE").nummer))
        self.assertTrue(Buchung.objects.filter(quelle="material").exists())
        self.assertTrue(Buchung.objects.filter(quelle="fertigmeldung").exists())
        anzahl = Buchung.objects.count()
        from apps.wareneingang.models import Wareneingang

        S.we_buchen(Wareneingang.objects.first())
        self.assertEqual(Buchung.objects.count(), anzahl)

    def test_rechnungspruefung_gegen_wareneingang(self):
        er = Eingangsrechnung.objects.get(rechnungsnummer="SH-RE-2026-4471")
        self.assertEqual(S.er_pruefen(er), [])
        # Preisabweichung 10 % → Hinweis, Buchung gesperrt bis zur Freigabe
        pos = er.positionen.get(artikel__artikelnummer="R-1000")
        pos.einzelpreis = D("1.30")
        pos.save()
        S.er_summen(er)
        hinweise = S.er_pruefen(er)
        self.assertTrue(er.abweichung)
        self.assertEqual([h["art"] for h in hinweise], ["PREIS"])
        with self.assertRaises(S.FibuFehler):
            S.er_buchen(er, self.bh)
        er.abweichung_akzeptiert = True
        er.save()
        b = S.er_buchen(er, self.bh)
        were = S.automatikkonto("WERE").nummer
        # WE/RE wird mit dem WE-Wert entlastet, die Preisdifferenz (1200 × 0,12) geht auf Aufwand
        self.assertEqual(zeilen_summe(b, were), D("1200") * D("1.18") + D("500") * D("4.80"))
        self.assertEqual(zeilen_summe(b, "3000"), D("144.00"))
        self.assertEqual(zeilen_summe(b, S.personenkonto(er.lieferant, "K").nummer), -er.summe_brutto)
        self.assertEqual(S.saldo(S.automatikkonto("WERE")), D("0"))  # WE und ER gleichen sich aus
        # Mengenprüfung: zweite Rechnung über dieselbe Lieferung
        from apps.wareneingang.models import Wareneingang

        er2 = S.er_aus_wareneingang(Wareneingang.objects.first(), "DOPPELT", date(2026, 11, 20))
        self.assertIn("MENGE", [h["art"] for h in S.er_pruefen(er2)])

    def test_bankimport_camt_zuordnung_skonto_und_dubletten(self):
        daten = (BEISPIELE / "camt053_demo.xml").read_bytes()
        imp = BK.importieren(daten, "camt053_demo.xml", self.bh)
        self.assertEqual(imp.anzahl_neu, 4)
        with self.assertRaises(S.FibuFehler):  # gleiche Datei
            BK.importieren(daten, "nochmal.xml", self.bh)
        imp2 = BK.importieren(daten.replace(b"CAMT-DEMO-20261120", b"CAMT-DEMO-KOPIE"), "kopie.xml", self.bh)
        self.assertEqual((imp2.anzahl_neu, imp2.anzahl_dubletten), (0, 4))
        z, g, o = BK.automatisch_zuordnen(imp.umsaetze.all(), self.bh)
        self.assertEqual((z, g, o), (2, 1, 1))
        op = self.op("RE-2026-00005")
        self.assertEqual(op.offen, 0)
        skonto = op.ausgleiche.get(art="SKONTO")
        self.assertEqual(skonto.betrag, D("183.50"))
        # Skonto mit Steuerkorrektur: 183,50 brutto → 154,20 netto + 29,30 USt-Korrektur
        b = skonto.buchung
        self.assertEqual(zeilen_summe(b, "8736"), D("154.20"))
        self.assertEqual(zeilen_summe(b, "1776"), D("29.30"))
        self.assertEqual(self.op("ER-2026-00002").offen, 0)
        unklar = imp.umsaetze.get(status="OFFEN")
        BK.auf_verrechnung(unklar, self.bh)
        self.assertEqual(S.saldo(S.automatikkonto("UNGEKLAERT")), D("-450.00"))
        # Bankstand = Endsaldo laut Auszug
        self.assertEqual(BK.bankstand(imp.bankkonto), D("93178.64"))

    def test_teilausgleich(self):
        op = self.op("RE-2026-00002")
        S.op_ausgleichen(op, D("1000"), date(2026, 11, 20), S.konto("1200"), self.bh)
        op.refresh_from_db()
        self.assertEqual(op.offen, D("7996.40"))
        self.assertEqual(op.ausgleiche.count(), 1)

    def test_mahnlauf_zinsen_sperre_pdf(self):
        stichtag = date(2026, 11, 20)
        lauf = MW.vorschlag(stichtag, self.bh)
        m = lauf.mahnungen.get()
        self.assertEqual(m.partner.name, "Autoteile Weber AG")
        self.assertEqual(m.stufe.stufe, 2)  # nach Zahlungserinnerung im September
        self.assertEqual(m.positionen.count(), 2)
        self.assertEqual(m.summe_offen, D("8996.40") + D("3581.10"))
        pos = m.positionen.get(op__belegnr="RE-2026-00002")
        self.assertEqual(pos.tage_verzug, 68)
        self.assertEqual(pos.zinsen, MW.verzugszinsen(D("8996.40"), 68, D("10.52")))
        self.assertEqual(m.gebuehr, D("5.00"))
        pdf = MW.mahnung_pdf(m)
        self.assertTrue(pdf.startswith(b"%PDF"))
        MW.ausfuehren(lauf, self.bh)
        self.assertEqual(self.op("RE-2026-00002").mahnstufe, 2)
        with self.assertRaises(S.FibuFehler):
            MW.ausfuehren(lauf, self.bh)
        # Mahnsperre am Partner → keine mahnfähigen Posten mehr
        Geschaeftspartner.objects.filter(name="Autoteile Weber AG").update(mahnsperre=True)
        with self.assertRaises(S.FibuFehler):
            MW.vorschlag(date(2026, 12, 1), self.bh)

    def test_bilanz_guv_ustva(self):
        bil = A.bilanz_daten(date(2026, 12, 31))
        self.assertTrue(bil["ausgeglichen"])
        guv = A.guv_daten(date(2026, 1, 1), date(2026, 12, 31))
        self.assertEqual(bil["ergebnis"], guv["ergebnis"])
        self.assertNotEqual(guv["ergebnis"], 0)
        u = A.ustva_daten(date(2026, 11, 1), date(2026, 11, 30))
        self.assertEqual(u["bemessung"]["81"], D("7710.00"))
        self.assertEqual(u["zahllast"], u["summe_ust"] - u["summe_vst"])

    def test_datev_export(self):
        daten, n = A.datev_csv(date(2026, 1, 1), date(2026, 12, 31))
        text = daten.decode("cp1252")
        zeilen = text.split("\r\n")
        kopf = zeilen[0].split(";")
        self.assertEqual(kopf[:5], ['"EXTF"', "700", "21", '"Buchungsstapel"', "13"])
        self.assertEqual(kopf[12], "20260101")
        self.assertTrue(zeilen[1].startswith("Umsatz (ohne Soll/Haben-Kz);Soll/Haben-Kennzeichen"))
        self.assertGreater(n, 10)
        self.assertEqual(len([z for z in zeilen[2:] if z]), n)
        for z in zeilen[2:]:
            if z:
                felder = z.split(";")
                self.assertEqual(len(felder), 14)
                self.assertRegex(felder[0], r"^\d+,\d{2}$")
                self.assertIn(felder[1], ('"S"', '"H"'))

    def test_rechte(self):
        url = reverse("fibu:index")
        self.client.login(username="vertrieb", password="Demo!2026")
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.login(username="gast", password="Demo!2026")
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get(reverse("fibu:buchung_neu")).status_code, 403)
        self.assertEqual(self.client.post(reverse("fibu:bank_auto")).status_code, 403)
        self.assertEqual(self.client.post(reverse("fibu:buchungen_festschreiben")).status_code, 403)
        lauf = MW.vorschlag(date(2026, 11, 20), self.bh)
        self.assertEqual(self.client.post(reverse("fibu:mahnlauf", args=[lauf.pk]),
                                          {"aktion": "ausfuehren"}).status_code, 404)
        p = Periode.objects.get(geschaeftsjahr__jahr=2026, nummer=12)
        self.client.post(reverse("fibu:perioden"), {"aktion": "sperren", "periode": p.pk})
        p.refresh_from_db()
        self.assertFalse(p.gesperrt)
        self.client.login(username="buchhaltung", password="Demo!2026")
        self.assertEqual(self.client.get(reverse("fibu:buchung_neu")).status_code, 200)
        self.client.post(reverse("fibu:perioden"), {"aktion": "sperren", "periode": p.pk})
        p.refresh_from_db()
        self.assertTrue(p.gesperrt)
        # REST-API: lesen für Buchhaltung, Buchen über die API inkl. Steuerautomatik
        r = self.client.get("/api/v1/offene-posten/?offen=1")
        self.assertEqual(r.status_code, 200)
        r = self.client.post("/api/v1/buchungen/", {
            "belegdatum": "2026-11-20", "belegnr": "API1", "text": "API-Buchung",
            "eingabe": [{"konto": "4210", "soll": "100", "steuerschluessel": "V19"}, {"konto": "1200", "haben": "119"}]},
            content_type="application/json")
        self.assertEqual(r.status_code, 201, r.content)
        self.client.login(username="vertrieb", password="Demo!2026")
        self.assertEqual(self.client.get("/api/v1/buchungen/").status_code, 403)

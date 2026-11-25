from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction
from django.test import TestCase
from django.utils import timezone

from apps.core import bankdaten as B
from apps.core.models import Mandant
from apps.fibu import services as F
from apps.fibu.models import Ausgleich, Bankkonto, Buchungszeile
from apps.stammdaten.models import Geschaeftspartner

from . import sepa
from . import services as S
from .models import Ruecklastschrift, SepaGesperrt, SepaMandat, Zahlungslauf, ZahlungslaufPosten, monate_addieren


class BankdatenTests(TestCase):
    def test_iban_pruefsumme(self):
        self.assertIsNotNone(B.iban_pruefen("DE89 3704 0044 0532 0130 00"))
        self.assertRaises(ValidationError, lambda: B.iban_pruefen("DE89370400440532013001"))
        self.assertRaises(ValidationError, lambda: B.iban_pruefen("DE8937040044053201300"))
        with self.assertRaises(ValidationError):
            B.iban_validator("DE00123")

    def test_bic_und_glaeubiger_id(self):
        self.assertIsNotNone(B.bic_pruefen("BYLADEM1001"))
        self.assertIsNotNone(B.bic_pruefen("COBADEFF"))
        self.assertRaises(ValidationError, lambda: B.bic_pruefen("BYLA1"))
        self.assertIsNotNone(B.glaeubiger_id_pruefen("DE98ZZZ09999999999"))
        self.assertRaises(ValidationError, lambda: B.glaeubiger_id_pruefen("DE97ZZZ09999999999"))


class SepaXmlTests(TestCase):
    TX = {"e2e": "ER-0001", "betrag": Decimal("980.00"), "name": "Müller Maschinenbau GmbH",
          "iban": "DE89370400440532013000", "bic": "", "zweck": "Rechnung ER-0001 abzgl. 2 % Skonto"}

    def test_zeichensatz_und_grenzen(self):
        self.assertEqual(sepa.sepa_text("Größe & Söhne_Ü"), "Groesse + Soehne-Ue")
        self.assertEqual(len(sepa.sepa_text("x" * 200)), 140)
        self.assertEqual(sepa.sepa_id("/A//B/"), "A/B")
        with self.assertRaises(sepa.SepaFehler):
            sepa.betrag("0")
        with self.assertRaises(sepa.SepaFehler):
            sepa.betrag("1000000000")

    def test_pain001_xsd_gueltig(self):
        xml = sepa.pain001("ZL-0001", "ZL-0001-1", "Demo GmbH", "DE02120300000000202051", "BYLADEM1001",
                           date(2026, 12, 1), [self.TX, dict(self.TX, e2e="ER-0002", bic="COBADEFF")])
        self.assertEqual(sepa.xsd_pruefen(xml, "pain.001"), [])
        self.assertIn(b"<CtrlSum>1960.00</CtrlSum>", xml)
        self.assertIn(b"Mueller Maschinenbau", xml)

    def test_pain008_sequenzen_xsd_gueltig(self):
        basis = dict(self.TX, mandat="MANDAT-000001", mandat_datum=date(2026, 1, 15), typ="CORE")
        xml = sepa.pain008("LL-0001", "Demo GmbH", "DE98ZZZ09999999999", "DE02120300000000202051", "",
                           date(2026, 12, 3), [dict(basis, sequenz="FRST"), dict(basis, e2e="AR-2", sequenz="RCUR"),
                                               dict(basis, e2e="AR-3", typ="B2B", sequenz="OOFF")])
        self.assertEqual(sepa.xsd_pruefen(xml, "pain.008"), [])
        for seq in (b"FRST", b"RCUR", b"OOFF"):
            self.assertIn(b"<SeqTp>" + seq + b"</SeqTp>", xml)
        self.assertEqual(xml.count(b"<PmtInf>"), 3)

    def test_xsd_erkennt_fehler(self):
        xml = sepa.pain001("M", "P", "Demo", "DE02120300000000202051", "", date(2026, 12, 1), [self.TX])
        kaputt = xml.replace(b"<PmtMtd>TRF</PmtMtd>", b"<PmtMtd>XXX</PmtMtd>")
        self.assertTrue(sepa.xsd_pruefen(kaputt, "pain.001"))


class MandatTests(TestCase):
    def test_verfall_36_monate(self):
        self.assertEqual(monate_addieren(date(2023, 11, 30), 36), date(2026, 11, 30))
        self.assertEqual(monate_addieren(date(2024, 2, 29), 36), date(2027, 2, 28))



IBAN = "DE89370400440532013000"
D = Decimal


class SepaWorkflowTests(TestCase):
    """Vorschlag → Freigabe → Export → Storno, Trigger-Sperre und Rückgabegründe."""

    @classmethod
    def setUpTestData(cls):
        call_command("erp_grunddaten", verbosity=0)
        cls.heute = timezone.localdate()
        for jahr in (cls.heute.year, cls.heute.year + 1):
            F.geschaeftsjahr_anlegen(jahr)
        cls.user = get_user_model().objects.create_user("sepatest", password="x")
        cls.bank = Bankkonto.objects.create(bezeichnung="Hausbank", iban="DE02120300000000202051", bic="BYLADEM1001",
                                            konto=F.konto("1200"))
        Mandant.objects.filter(pk=Mandant.aktuell().pk).update(glaeubiger_id="DE98ZZZ09999999999")

    def setUp(self):
        self.nr = 0

    def _op(self, art, partner, betrag, faellig, skonto_bis=None, prozent=0):
        self.nr += 1
        belegnr = f"{art}-T{self.nr}"
        pk = F.personenkonto(partner, art)
        if art == "K":
            zeilen = [{"konto": "4210", "soll": betrag}, {"konto": pk, "haben": betrag, "partner": partner}]
        else:
            zeilen = [{"konto": pk, "soll": betrag, "partner": partner}, {"konto": "8400", "haben": betrag}]
        b = F.buchen(self.heute, belegnr, "Testrechnung", zeilen)
        op = F.op_anlegen(art, partner, b, belegnr, self.heute, D(betrag), faellig_am=faellig)
        if skonto_bis:
            op.skonto_bis, op.skonto_prozent = skonto_bis, D(prozent)
            op.save()
        return op

    def _partner(self, name, iban=IBAN, kunde=False):
        return Geschaeftspartner.objects.create(name=name, iban=iban, ist_kunde=kunde, ist_lieferant=not kunde)

    def _mandat(self, partner, **kw):
        daten = {"partner": partner, "unterschrieben_am": self.heute - timedelta(days=30), "iban": IBAN}
        daten.update(kw)
        return SepaMandat.objects.create(**daten)

    def _ueberweisungslauf(self):
        datum = S.bankarbeitstag_addieren(self.heute, 2)
        lieferant = self._partner("Lieferant Skonto GmbH")
        op_skonto = self._op("K", lieferant, "1000.00", datum + timedelta(days=5), datum + timedelta(days=1), 2)
        op_abgelaufen = self._op("K", lieferant, "500.00", datum + timedelta(days=3), self.heute - timedelta(days=1), 3)
        op_spaeter = self._op("K", lieferant, "300.00", datum + timedelta(days=90))
        ohne_iban = self._op("K", self._partner("Lieferant ohne IBAN", iban=""), "200.00", datum)
        lauf = S.ueberweisungsvorschlag(self.bank, datum, bis=datum + timedelta(days=10), benutzer=self.user)
        return lauf, op_skonto, op_abgelaufen, op_spaeter, ohne_iban

    # ---------------------------------------------------------------- Kalender

    def test_bankarbeitstage_target2(self):
        self.assertEqual(S._ostersonntag(2026), date(2026, 4, 5))
        self.assertEqual(S.bankarbeitstag_addieren(date(2027, 3, 25), 1), date(2027, 3, 30))  # Karfreitag/Ostermontag
        self.assertEqual(S.bankarbeitstag_addieren(date(2026, 12, 24), 1), date(2026, 12, 28))
        self.assertFalse(S.ist_bankarbeitstag(date(2026, 5, 1)))
        self.assertEqual(S.geldtransit_konto().nummer, "1360")  # SKR03

    # ---------------------------------------------------------------- 1. Überweisungsvorschlag

    def test_ueberweisungsvorschlag_skonto_iban_und_faelligkeit(self):
        lauf, op_skonto, op_abgelaufen, op_spaeter, ohne_iban = self._ueberweisungslauf()
        posten = {p.op_id: p for p in lauf.posten.all()}
        self.assertEqual(set(posten), {op_skonto.pk, op_abgelaufen.pk})
        self.assertEqual(posten[op_skonto.pk].skonto, D("20.00"))
        self.assertEqual(posten[op_skonto.pk].betrag, D("980.00"))
        self.assertEqual(posten[op_abgelaufen.pk].skonto, D("0.00"))  # Skontofrist abgelaufen
        self.assertEqual(lauf.summe, D("1480.00"))
        self.assertEqual(lauf.status, Zahlungslauf.Status.ENTWURF)
        self.assertTrue(lauf.nummer.startswith("ZL-"))
        self.assertEqual(len(lauf.uebersprungen), 1)
        self.assertIn("IBAN", lauf.uebersprungen[0])
        # bereits im offenen Lauf enthaltene OPs werden nicht erneut vorgeschlagen
        with self.assertRaises(S.SepaWorkflowFehler):
            S.ueberweisungsvorschlag(self.bank, lauf.ausfuehrungsdatum, bis=lauf.beruecksichtigen_bis)

    # ---------------------------------------------------------------- 2. Lastschriftvorschlag

    def test_lastschriftvorschlag_mandate_sequenz_vorlauf(self):
        k_core = self._partner("Kunde Core", kunde=True)
        k_ooff = self._partner("Kunde Einmal", kunde=True)
        k_gesperrt = self._partner("Kunde Gesperrt", kunde=True)
        k_verfallen = self._partner("Kunde Verfallen", kunde=True)
        k_ohne = self._partner("Kunde ohne Mandat", kunde=True)
        m_core = self._mandat(k_core)
        m_ooff = self._mandat(k_ooff, typ="B2B", art="OOFF")
        self._mandat(k_gesperrt, status=SepaMandat.Status.GESPERRT)
        self._mandat(k_verfallen, unterschrieben_am=monate_addieren(self.heute, -40))
        faellig = self.heute + timedelta(days=1)
        for k in (k_core, k_ooff, k_gesperrt, k_verfallen, k_ohne):
            self._op("D", k, "119.00", faellig)
        self._op("D", k_ooff, "50.00", faellig)  # OOFF erlaubt nur einen Einzug

        Mandant.objects.update(glaeubiger_id="")
        with self.assertRaises(S.SepaWorkflowFehler):
            S.lastschriftvorschlag(self.bank)
        Mandant.objects.update(glaeubiger_id="DE98ZZZ09999999999")

        lauf = S.lastschriftvorschlag(self.bank, einzugsdatum=self.heute, bis=faellig + timedelta(days=5))
        self.assertTrue(lauf.nummer.startswith("LL-"))
        self.assertEqual(lauf.ausfuehrungsdatum, S.fruehestes_einzugsdatum("CORE", self.heute))
        self.assertGreater(lauf.ausfuehrungsdatum, self.heute)
        seq = {p.partner_id: (p.sequenz, p.lastschrift_typ, p.mandatsreferenz) for p in lauf.posten.all()}
        self.assertEqual(seq, {k_core.pk: ("FRST", "CORE", m_core.mandatsreferenz),
                               k_ooff.pk: ("OOFF", "B2B", m_ooff.mandatsreferenz)})
        self.assertEqual(len(lauf.uebersprungen), 4)

    # ---------------------------------------------------------------- 3. Freigabe und Export

    def test_freigabe_export_lastschrift_bucht_geldtransit_und_rcur(self):
        kunde = self._partner("Kunde Folge", kunde=True)
        einmal = self._partner("Kunde Einmalig", kunde=True)
        mandat = self._mandat(kunde)
        m_ooff = self._mandat(einmal, art="OOFF")
        op = self._op("D", kunde, "238.00", self.heute)
        op2 = self._op("D", einmal, "100.00", self.heute)
        lauf = S.lastschriftvorschlag(self.bank)
        with self.assertRaises(S.SepaWorkflowFehler):
            S.exportieren(lauf, self.user)  # erst freigeben
        with self.assertRaises(S.SepaWorkflowFehler):
            S.freigeben(lauf, None)
        lauf = S.freigeben(lauf, self.user)
        self.assertEqual((lauf.status, lauf.freigegeben_von), (Zahlungslauf.Status.FREIGEGEBEN, self.user))
        self.assertIsNotNone(lauf.freigegeben_am)

        lauf = S.exportieren(lauf, self.user)
        self.assertEqual(lauf.status, Zahlungslauf.Status.EXPORTIERT)
        self.assertTrue(lauf.xsd_geprueft)
        self.assertEqual(sepa.xsd_pruefen(lauf.xml_inhalt.encode(), "pain.008"), [])
        self.assertIn("pain.008.001.08", lauf.xml_dateiname)
        self.assertIn("<SeqTp>FRST</SeqTp>", lauf.xml_inhalt)
        self.assertIn("DE98ZZZ09999999999", lauf.xml_inhalt)
        self.assertEqual(len(lauf.xml_sha256), 64)
        self.assertEqual(lauf.buchung.belegnr, lauf.nummer)
        transit = Buchungszeile.objects.get(buchung=lauf.buchung, konto__nummer="1360")
        self.assertEqual(transit.soll, D("338.00"))
        for o in (op, op2):
            o.refresh_from_db()
            self.assertEqual(o.offen, D("0.00"))
        self.assertTrue(all(p.end_to_end_id for p in lauf.posten.all()))
        mandat.refresh_from_db()
        m_ooff.refresh_from_db()
        self.assertEqual(mandat.naechste_sequenz(), "RCUR")
        self.assertEqual(mandat.letzte_nutzung, lauf.ausfuehrungsdatum)
        self.assertEqual(m_ooff.status, SepaMandat.Status.BEENDET)
        lauf.summe = D("1.00")
        with self.assertRaises(SepaGesperrt):
            lauf.save()
        with self.assertRaises(S.SepaWorkflowFehler):
            S.exportieren(lauf, self.user)  # kein zweiter Export

    def test_export_ueberweisung_mit_skonto(self):
        lauf, op_skonto, op_abgelaufen, *_ = self._ueberweisungslauf()
        S.freigeben(lauf, self.user)
        lauf = S.exportieren(lauf, self.user)
        self.assertEqual(sepa.xsd_pruefen(lauf.xml_inhalt.encode(), "pain.001"), [])
        self.assertIn("<CtrlSum>1480.00</CtrlSum>", lauf.xml_inhalt)
        transit = Buchungszeile.objects.get(buchung=lauf.buchung, konto__nummer="1360")
        self.assertEqual(transit.haben, D("1480.00"))
        op_skonto.refresh_from_db()
        self.assertEqual(op_skonto.offen, D("0.00"))
        self.assertTrue(Ausgleich.objects.filter(op=op_skonto, art=Ausgleich.Art.SKONTO, betrag=D("20.00")).exists())

    # ---------------------------------------------------------------- 4. Storno

    def test_storno_per_gegenbuchung_nimmt_ausgleich_zurueck(self):
        lauf, op_skonto, op_abgelaufen, *_ = self._ueberweisungslauf()
        S.freigeben(lauf, self.user)
        with self.assertRaises(S.SepaWorkflowFehler):
            S.stornieren(lauf, self.user)  # nur exportierte Läufe
        lauf = S.exportieren(lauf, self.user)
        xml, buchung = lauf.xml_inhalt, lauf.buchung
        lauf = S.stornieren(lauf, self.user, "Bank hat Datei abgelehnt")
        self.assertEqual(lauf.status, Zahlungslauf.Status.STORNIERT)
        self.assertEqual(lauf.storno_buchung.storno_von, buchung)
        self.assertEqual(lauf.xml_inhalt, xml)  # nichts gelöscht oder verändert
        self.assertEqual(lauf.posten.count(), 2)
        for o, betrag in ((op_skonto, "1000.00"), (op_abgelaufen, "500.00")):
            o.refresh_from_db()
            self.assertEqual(o.offen, D(betrag))
        self.assertEqual(F.saldo(F.konto("1360")), D("0.00"))
        with self.assertRaises(S.SepaWorkflowFehler):
            S.stornieren(lauf, self.user)
        neu = S.ueberweisungsvorschlag(self.bank, lauf.ausfuehrungsdatum, bis=lauf.beruecksichtigen_bis)
        self.assertEqual(neu.posten.count(), 2)  # OPs aus storniertem Lauf wieder zahlbar

    def test_storno_lastschrift_setzt_mandat_zurueck(self):
        kunde = self._partner("Kunde Storno", kunde=True)
        mandat = self._mandat(kunde, art="OOFF")
        self._op("D", kunde, "80.00", self.heute)
        lauf = S.exportieren(S.freigeben(S.lastschriftvorschlag(self.bank), self.user), self.user)
        S.stornieren(lauf, self.user)
        mandat.refresh_from_db()
        self.assertEqual(mandat.status, SepaMandat.Status.AKTIV)
        self.assertIsNone(mandat.letzte_nutzung)
        self.assertEqual(mandat.naechste_sequenz(), "OOFF")

    # ---------------------------------------------------------------- 5. Trigger und erp_pruefen

    def test_trigger_sperrt_exportierten_lauf_auch_per_sql(self):
        lauf, *_ = self._ueberweisungslauf()
        entwurf_posten = lauf.posten.first()
        with connection.cursor() as cur:  # Entwurf: Änderungen erlaubt
            cur.execute("UPDATE fibu_sepa_zahlungslaufposten SET hinweis = 'ok', betrag = betrag WHERE id = %s",
                        [entwurf_posten.pk])
        lauf = S.exportieren(S.freigeben(lauf, self.user), self.user)
        sperren = [
            ("UPDATE fibu_sepa_zahlungslauf SET summe = 1 WHERE id = %s", lauf.pk),
            ("UPDATE fibu_sepa_zahlungslauf SET xml_inhalt = 'x' WHERE id = %s", lauf.pk),
            ("DELETE FROM fibu_sepa_zahlungslauf WHERE id = %s", lauf.pk),
            ("UPDATE fibu_sepa_zahlungslaufposten SET betrag = 1 WHERE id = %s", entwurf_posten.pk),
            ("DELETE FROM fibu_sepa_zahlungslaufposten WHERE id = %s", entwurf_posten.pk),
        ]
        for sql, pk in sperren:
            with self.subTest(sql=sql), self.assertRaises(DatabaseError), transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(sql, [pk])
        with connection.cursor() as cur:  # Status-/Abgleichsfelder bleiben änderbar
            cur.execute("UPDATE fibu_sepa_zahlungslauf SET bemerkung = 'geprüft' WHERE id = %s", [lauf.pk])
            cur.execute("UPDATE fibu_sepa_zahlungslaufposten SET hinweis = 'abgeglichen' WHERE id = %s",
                        [entwurf_posten.pk])
        with self.assertRaises(SepaGesperrt):
            ZahlungslaufPosten.objects.get(pk=entwurf_posten.pk).delete()
        ausgabe = StringIO()
        call_command("erp_pruefen", "--ohne-pdf", stdout=ausgabe)
        self.assertIn("Trigger: 7/7", ausgabe.getvalue())

    # ---------------------------------------------------------------- 6. Rückgabegründe

    def test_rueckgabegruende_md06_vermerken_ac04_md01_sperren(self):
        self.assertNotIn("MD06", Ruecklastschrift.MANDAT_SPERREN)
        self.assertTrue({"AC04", "MD01"} <= Ruecklastschrift.MANDAT_SPERREN)
        kunde = self._partner("Kunde Rückgabe", kunde=True)
        m = self._mandat(kunde)
        self.assertEqual(S.rueckgabe_mandat_behandeln(m, "MD06"), "vermerkt")
        m.refresh_from_db()
        self.assertEqual(m.status, SepaMandat.Status.AKTIV)
        self.assertIn("MD06", m.bemerkung)
        self.assertEqual(S.rueckgabe_mandat_behandeln(m, "AM04"), "")
        self.assertEqual(S.rueckgabe_mandat_behandeln(m, "AC04"), "gesperrt")
        m.refresh_from_db()
        self.assertEqual(m.status, SepaMandat.Status.GESPERRT)
        self.assertIn("AC04", m.bemerkung)
        m2 = self._mandat(self._partner("Kunde MD01", kunde=True))
        self.assertEqual(S.rueckgabe_mandat_behandeln(m2, "md01"), "gesperrt")
        self.assertIsNone(S.gueltiges_mandat(m2.partner, self.heute))


class SepaOberflaecheTests(TestCase):
    """Seiten, Rechte und Client-Ablauf Vorschlag → Freigabe → Export → Download → Storno."""

    _op, _partner, _mandat = SepaWorkflowTests._op, SepaWorkflowTests._partner, SepaWorkflowTests._mandat

    @classmethod
    def setUpTestData(cls):
        from apps.core.models import Rolle

        SepaWorkflowTests.setUpTestData.__func__(cls)
        User = get_user_model()
        cls.admin = User.objects.create_superuser("sepa_admin", password="x")
        cls.buha = User.objects.create_user("sepa_buha", password="x")
        Rolle.objects.get(name="Buchhaltung").benutzer.add(cls.buha)
        cls.einkauf = User.objects.create_user("sepa_einkauf", password="x")
        Rolle.objects.get(name="Einkauf").benutzer.add(cls.einkauf)
        cls.kunde = Geschaeftspartner.objects.create(name="Kunde Lastschrift", iban=IBAN, ist_kunde=True)
        cls.mandat_obj = SepaMandat.objects.create(partner=cls.kunde, unterschrieben_am=cls.heute - timedelta(days=30),
                                                   iban=IBAN)

    def setUp(self):
        self.nr = 0

    def _seiten(self):
        from django.urls import reverse

        lauf = Zahlungslauf.objects.create(nummer="ZL-TEST", art=Zahlungslauf.Art.UEBERWEISUNG, bankkonto=self.bank,
                                           ausfuehrungsdatum=self.heute)
        return [reverse("fibu_sepa:laeufe"), reverse("fibu_sepa:mandate"), reverse("fibu_sepa:mandate") + "?filter=alle",
                reverse("fibu_sepa:mandate") + "?filter=gesperrt", reverse("fibu_sepa:mandate") + "?filter=abgelaufen",
                reverse("fibu_sepa:mandat", args=[self.mandat_obj.pk]), reverse("fibu_sepa:lauf", args=[lauf.pk])]

    def test_rechte_und_seiten(self):
        from django.urls import reverse

        from apps.core.permissions import hat_recht

        self.assertTrue(hat_recht(self.buha, "fibu_sepa", "freigeben"))
        self.assertFalse(hat_recht(self.einkauf, "fibu_sepa", "lesen"))
        seiten = self._seiten()
        schreibseiten = [reverse("fibu_sepa:mandat_neu"), reverse("fibu_sepa:mandat_bearbeiten", args=[self.mandat_obj.pk]),
                         reverse("fibu_sepa:vorschlag", args=["ueberweisung"]),
                         reverse("fibu_sepa:vorschlag", args=["lastschrift"])]
        for user in (self.admin, self.buha):
            self.client.force_login(user)
            for url in seiten + schreibseiten:
                self.assertEqual(self.client.get(url).status_code, 200, f"{user}: {url}")
        # Menüeintrag sichtbar
        self.assertContains(self.client.get(reverse("core:dashboard")), reverse("fibu_sepa:laeufe"))
        self.client.force_login(self.einkauf)
        for url in seiten + schreibseiten:
            self.assertEqual(self.client.get(url).status_code, 403, url)
        lauf = Zahlungslauf.objects.get(nummer="ZL-TEST")
        self.assertEqual(self.client.post(reverse("fibu_sepa:lauf_freigeben", args=[lauf.pk])).status_code, 403)
        self.assertNotContains(self.client.get(reverse("core:dashboard")), reverse("fibu_sepa:laeufe"))
        self.client.logout()
        self.assertEqual(self.client.get(seiten[0]).status_code, 302)

    def test_nur_lesen_darf_nicht_freigeben(self):
        from django.urls import reverse

        from apps.core.models import Rolle

        leser = get_user_model().objects.create_user("sepa_leser", password="x")
        Rolle.objects.get(name="Nur Lesen").benutzer.add(leser)
        self.client.force_login(leser)
        seiten = self._seiten()
        self.assertEqual(self.client.get(seiten[0]).status_code, 200)
        lauf = Zahlungslauf.objects.get(nummer="ZL-TEST")
        self.assertEqual(self.client.post(reverse("fibu_sepa:lauf_freigeben", args=[lauf.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("fibu_sepa:lauf_exportieren", args=[lauf.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("fibu_sepa:vorschlag", args=["ueberweisung"])).status_code, 403)
        self.assertEqual(Zahlungslauf.objects.get(pk=lauf.pk).status, Zahlungslauf.Status.ENTWURF)

    def test_mandat_anlegen_sperren_beenden(self):
        from django.urls import reverse

        self.client.force_login(self.buha)
        antwort = self.client.post(reverse("fibu_sepa:mandat_neu"), {
            "partner": self.kunde.pk, "typ": "B2B", "art": "OOFF",
            "unterschrieben_am": (self.heute - timedelta(days=3)).isoformat(), "iban": "DE89 3704 0044 0532 0130 00",
            "bic": "", "kontoinhaber": "", "bemerkung": ""})
        m = SepaMandat.objects.exclude(pk=self.mandat_obj.pk).get()
        self.assertRedirects(antwort, reverse("fibu_sepa:mandat", args=[m.pk]))
        self.assertTrue(m.mandatsreferenz.startswith("MANDAT-"))
        self.assertEqual((m.typ, m.art, m.iban), ("B2B", "OOFF", IBAN))
        # ungültige IBAN wird abgelehnt
        antwort = self.client.post(reverse("fibu_sepa:mandat_bearbeiten", args=[m.pk]), {
            "partner": self.kunde.pk, "typ": "B2B", "art": "OOFF", "unterschrieben_am": self.heute.isoformat(),
            "iban": "DE89370400440532013001"})
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Prüfziffer")
        self.client.post(reverse("fibu_sepa:mandat_status", args=[m.pk, "sperren"]))
        m.refresh_from_db()
        self.assertEqual(m.status, SepaMandat.Status.GESPERRT)
        self.assertContains(self.client.get(reverse("fibu_sepa:mandate") + "?filter=gesperrt"), m.mandatsreferenz)
        self.assertNotContains(self.client.get(reverse("fibu_sepa:mandate")), m.mandatsreferenz)
        self.client.post(reverse("fibu_sepa:mandat_status", args=[m.pk, "beenden"]))
        m.refresh_from_db()
        self.assertEqual(m.status, SepaMandat.Status.BEENDET)
        self.assertContains(self.client.get(reverse("fibu_sepa:mandate") + "?filter=abgelaufen"), m.mandatsreferenz)

    def test_ablauf_ueberweisung_vorschlag_freigabe_export_download_storno(self):
        from django.urls import reverse

        datum = S.bankarbeitstag_addieren(self.heute, 2)
        lieferant = self._partner("Lieferant UI GmbH")
        op1 = self._op("K", lieferant, "1000.00", datum)
        op2 = self._op("K", lieferant, "250.00", datum)
        self._op("K", self._partner("Lieferant ohne IBAN UI", iban=""), "99.00", datum)
        self.client.force_login(self.buha)
        antwort = self.client.post(reverse("fibu_sepa:vorschlag", args=["ueberweisung"]), {
            "bankkonto": self.bank.pk, "datum": datum.isoformat(), "bis": "", "skonto_nutzen": "on"}, follow=True)
        lauf = Zahlungslauf.objects.get(art=Zahlungslauf.Art.UEBERWEISUNG)
        self.assertRedirects(antwort, reverse("fibu_sepa:lauf", args=[lauf.pk]))
        self.assertContains(antwort, "Lieferant ohne IBAN UI: keine gültige IBAN")  # übersprungene Partner
        self.assertEqual(lauf.anzahl, 2)
        # Position im Entwurf entfernen → Summe neu
        p2 = lauf.posten.get(op=op2)
        self.client.post(reverse("fibu_sepa:posten_entfernen", args=[lauf.pk, p2.pk]))
        lauf.refresh_from_db()
        self.assertEqual((lauf.anzahl, lauf.summe), (1, D("1000.00")))
        # Export vor Freigabe → Service-Fehlermeldung als Message
        antwort = self.client.post(reverse("fibu_sepa:lauf_exportieren", args=[lauf.pk]), follow=True)
        self.assertContains(antwort, "muss vor dem Export freigegeben sein")
        self.client.post(reverse("fibu_sepa:lauf_freigeben", args=[lauf.pk]))
        lauf.refresh_from_db()
        self.assertEqual(lauf.status, Zahlungslauf.Status.FREIGEGEBEN)
        self.client.post(reverse("fibu_sepa:lauf_exportieren", args=[lauf.pk]))
        lauf.refresh_from_db()
        self.assertEqual(lauf.status, Zahlungslauf.Status.EXPORTIERT)
        op1.refresh_from_db()
        self.assertEqual(op1.offen, 0)
        antwort = self.client.get(reverse("fibu_sepa:lauf_xml", args=[lauf.pk]))
        self.assertEqual(antwort.status_code, 200)
        self.assertIn(f'filename="{lauf.nummer}_pain.001.001.09.xml"', antwort["Content-Disposition"])
        self.assertIn(b"<CtrlSum>1000.00</CtrlSum>", antwort.content)
        # Posten nach Export nicht mehr entfernbar
        p1 = lauf.posten.get(op=op1)
        antwort = self.client.post(reverse("fibu_sepa:posten_entfernen", args=[lauf.pk, p1.pk]), follow=True)
        self.assertContains(antwort, "nicht mehr im Entwurf")
        # Storno nur mit Bestätigung
        url = reverse("fibu_sepa:lauf_stornieren", args=[lauf.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(url, {"grund": "Bank hat Datei abgelehnt"})
        lauf.refresh_from_db()
        self.assertEqual(lauf.status, Zahlungslauf.Status.EXPORTIERT)
        self.client.post(url, {"grund": "Bank hat Datei abgelehnt", "bestaetigt": "on"})
        lauf.refresh_from_db()
        self.assertEqual(lauf.status, Zahlungslauf.Status.STORNIERT)
        op1.refresh_from_db()
        self.assertEqual(op1.offen, D("1000.00"))

    def test_ablauf_lastschrift_per_client(self):
        from django.urls import reverse

        self._op("D", self.kunde, "300.00", self.heute + timedelta(days=1))
        self.client.force_login(self.admin)
        self.client.post(reverse("fibu_sepa:vorschlag", args=["lastschrift"]), {"bankkonto": self.bank.pk})
        lauf = Zahlungslauf.objects.get(art=Zahlungslauf.Art.LASTSCHRIFT)
        self.assertEqual(lauf.posten.get().sequenz, "FRST")
        self.client.post(reverse("fibu_sepa:lauf_freigeben", args=[lauf.pk]))
        self.client.post(reverse("fibu_sepa:lauf_exportieren", args=[lauf.pk]))
        antwort = self.client.get(reverse("fibu_sepa:lauf_xml", args=[lauf.pk]))
        self.assertEqual(antwort.status_code, 200)
        self.assertIn(f'filename="{lauf.nummer}_pain.008.001.08.xml"', antwort["Content-Disposition"])
        self.assertContains(self.client.get(reverse("fibu_sepa:mandat", args=[self.mandat_obj.pk])), lauf.nummer)

    def test_stammdaten_iban_bic_und_glaeubiger_id(self):
        from django.urls import reverse

        self.client.force_login(self.admin)
        url = reverse("stammdaten:partner_neu")
        daten = {"name": "Bank Test GmbH", "ist_lieferant": "on", "iban": "DE89 3704 0044 0532 0130 01",
                 "bic": "COBADEFF", "aktiv": "on"}
        for fs in ("fs0", "fs1"):  # Inline-Formsets (Adressen, Ansprechpartner) leer
            daten.update({f"{fs}-TOTAL_FORMS": "0", f"{fs}-INITIAL_FORMS": "0"})
        antwort = self.client.get(url)
        self.assertContains(antwort, 'name="iban"')
        self.assertContains(antwort, 'name="bic"')
        antwort = self.client.post(url, daten)
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Prüfziffer")
        daten.update(iban="DE89 3704 0044 0532 0130 00", bic="cobadeff")
        self.client.post(url, daten)
        p = Geschaeftspartner.objects.get(name="Bank Test GmbH")
        self.assertEqual((p.iban, p.bic), (IBAN, "COBADEFF"))
        antwort = self.client.get(reverse("core:mandant"))
        self.assertContains(antwort, 'name="glaeubiger_id"')

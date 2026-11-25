from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.core import bankdaten as B

from . import sepa
from .models import monate_addieren


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

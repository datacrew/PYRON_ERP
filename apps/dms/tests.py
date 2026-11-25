"""Tests DMS – Fundament: Ablage, SHA-256, Versionierung, GoBD-Schutz (Code und Trigger), Suche, Rechte."""
import io
import shutil
import tempfile
from datetime import date

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import Rolle

from . import services as S
from .models import Dokument, DokumentProtokoll, DokumentVersion, DmsGesperrt, Dokumentart, aufbewahren_bis

TEMP_MEDIA = tempfile.mkdtemp(prefix="dms_test_")


def tearDownModule():
    shutil.rmtree(TEMP_MEDIA, ignore_errors=True)


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class DmsTestBasis(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("erp_grunddaten", verbosity=0)
        cls.user = get_user_model().objects.create_superuser("dmsadmin", password="x")


class FundamentTests(DmsTestBasis):
    def test_anlage_mit_sha256_und_protokoll(self):
        d = S.dokument_anlegen(b"<xml>Test</xml>", "test.xml", art=Dokumentart.SONSTIGES, benutzer=self.user)
        self.assertTrue(d.nummer.startswith("DOK-"))
        v = d.original
        self.assertEqual(v.sha256, S.sha256(b"<xml>Test</xml>"))
        self.assertTrue(v.hash_pruefen())
        self.assertEqual(d.status, Dokument.Status.NEU)
        self.assertTrue(d.protokoll.filter(aktion="Eingang").exists())

    def test_ungueltige_datei_abgelehnt(self):
        with self.assertRaises(S.DmsFehler):
            S.dokument_anlegen(b"MZ...", "virus.exe")
        with self.assertRaises(S.DmsFehler):
            S.dokument_anlegen(b"", "leer.pdf")

    def test_versionierung_unveraenderbar(self):
        d = S.dokument_anlegen(b"<a/>", "a.xml", benutzer=self.user)
        v2 = S.neue_version(d, b"<b/>", "b.xml", "Korrigierter Scan", self.user)
        self.assertEqual(v2.version, 2)
        self.assertEqual(d.original.pk, v2.pk)
        v1 = d.versionen.get(version=1)
        v1.kommentar = "manipuliert"
        with self.assertRaises(DmsGesperrt):
            v1.save()

    def test_aufbewahrungsfrist(self):
        self.assertEqual(aufbewahren_bis(Dokumentart.EINGANGSRECHNUNG, date(2026, 3, 5)), date(2034, 12, 31))
        self.assertEqual(aufbewahren_bis(Dokumentart.VERTRAG, date(2026, 3, 5)), date(2036, 12, 31))

    def test_archiv_schutz_code(self):
        d = S.dokument_anlegen(b"<a/>", "a.xml", art=Dokumentart.EINGANGSRECHNUNG, benutzer=self.user)
        d.belegdatum = date(2026, 2, 1)
        d.save()
        S.archivieren(d, self.user)
        self.assertEqual(d.aufbewahren_bis, date(2034, 12, 31))
        d.refresh_from_db()
        d.ocr_text = "manipuliert"
        with self.assertRaises(DmsGesperrt):
            d.save()
        d.refresh_from_db()
        d.archiviert = False
        with self.assertRaises(DmsGesperrt):
            d.save()
        d.refresh_from_db()
        with self.assertRaises(DmsGesperrt):
            S.loeschen(d)
        with self.assertRaises(DmsGesperrt):
            d.delete()
        # Metadaten (z. B. Bemerkung) bleiben pflegbar
        d.bemerkung = "geprüft"
        d.save()

    def test_archiv_schutz_trigger(self):
        d = S.dokument_anlegen(b"<a/>", "a.xml", benutzer=self.user)
        S.archivieren(d, self.user)
        for sql in ("UPDATE dms_dokument SET ocr_text='x' WHERE id=%s",
                    "UPDATE dms_dokument SET archiviert=false WHERE id=%s",
                    "DELETE FROM dms_dokument WHERE id=%s"):
            with self.subTest(sql=sql), self.assertRaises(DatabaseError), transaction.atomic():
                with connection.cursor() as c:
                    c.execute(sql, [d.pk])
        for sql in ("UPDATE dms_dokumentversion SET sha256='0' WHERE dokument_id=%s",
                    "DELETE FROM dms_dokumentversion WHERE dokument_id=%s",
                    "UPDATE dms_dokumentprotokoll SET text='x' WHERE dokument_id=%s"):
            with self.subTest(sql=sql), self.assertRaises(DatabaseError), transaction.atomic():
                with connection.cursor() as c:
                    c.execute(sql, [d.pk])

    def test_loeschen_vor_archivierung(self):
        d = S.dokument_anlegen(b"<a/>", "a.xml", benutzer=self.user)
        name = d.original.datei.name
        S.loeschen(d, self.user)
        self.assertFalse(Dokument.objects.filter(pk=d.pk).exists())
        self.assertFalse(DokumentVersion._meta.get_field("datei").storage.exists(name))

    def test_volltextsuche(self):
        d = S.dokument_anlegen(b"<a/>", "a.xml", titel="Rechnung Büromaterial", benutzer=self.user)
        Dokument.objects.filter(pk=d.pk).update(ocr_text="Lieferung von Kopierpapier und Druckerpatronen")
        d.refresh_from_db()
        S.suchvektor_aktualisieren(d)
        S.dokument_anlegen(b"<b/>", "b.xml", titel="Mietvertrag", benutzer=self.user)
        treffer = list(S.suchen(Dokument.objects.all(), "Druckerpatrone"))  # Stemming (german)
        self.assertEqual(treffer, [d])
        self.assertEqual(list(S.suchen(Dokument.objects.all(), "Büromaterial")), [d])
        # globale Suche in der Kopfzeile findet das Dokument über den OCR-Text
        self.client.force_login(self.user)
        r = self.client.get(reverse("core:suche"), {"q": "Druckerpatronen"})
        self.assertContains(r, reverse("dms:detail", args=[d.pk]))

    def test_integritaet_erkennt_manipulation(self):
        d = S.dokument_anlegen(b"<a/>", "a.xml", benutzer=self.user)
        anzahl, fehler = S.integritaet_pruefen()
        self.assertEqual((anzahl, fehler), (1, []))
        with open(d.original.datei.path, "wb") as f:
            f.write(b"<manipuliert/>")
        self.assertEqual(len(S.integritaet_pruefen()[1]), 1)

    def test_protokoll_nur_anfuegen(self):
        d = S.dokument_anlegen(b"<a/>", "a.xml", benutzer=self.user)
        p = DokumentProtokoll.objects.filter(dokument=d).first()
        p.text = "x"
        with self.assertRaises(DmsGesperrt):
            p.save()


class OberflaecheTests(DmsTestBasis):
    def test_seiten_und_upload(self):
        self.client.force_login(self.user)
        for name in ("dms:index", "dms:liste", "dms:hochladen", "dms:stapel_liste", "dms:fristen", "dms:tags"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        from django.core.files.uploadedfile import SimpleUploadedFile
        r = self.client.post(reverse("dms:hochladen"), {
            "dateien": [SimpleUploadedFile("a.xml", b"<a/>"), SimpleUploadedFile("b.xml", b"<b/>")],
            "art": Dokumentart.SONSTIGES, "bezeichnung": "Test"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Dokument.objects.count(), 2)
        d = Dokument.objects.first()
        self.assertEqual(self.client.get(reverse("dms:detail", args=[d.pk])).status_code, 200)
        r = self.client.get(reverse("dms:datei", args=[d.pk]))
        self.assertEqual(b"".join(r.streaming_content), d.original.datei.open("rb").read())
        self.assertEqual(self.client.get(reverse("dms:liste") + "?q=a").status_code, 200)

    def test_rechte(self):
        gast = get_user_model().objects.create_user("dmsgast", password="x")
        self.client.force_login(gast)
        self.assertEqual(self.client.get(reverse("dms:index")).status_code, 403)
        leser = get_user_model().objects.create_user("dmsleser", password="x")
        Rolle.objects.get(name="Vertrieb").benutzer.add(leser)
        self.client.force_login(leser)
        self.assertEqual(self.client.get(reverse("dms:liste")).status_code, 200)
        self.assertEqual(self.client.get(reverse("dms:hochladen")).status_code, 403)


# ------------------------------------------------------------------ OCR-Pipeline

from unittest import skipUnless  # noqa: E402

from . import ocr as O  # noqa: E402
from . import testdaten as T  # noqa: E402
from . import verarbeitung as V  # noqa: E402


class TexterkennungTests(TestCase):
    """Reine Analyse ohne Datenbank: E-Rechnung, Textlayer, OCR."""

    def test_xrechnung_und_zugferd_ohne_ocr(self):
        e = O.analysieren(T.cii_xml(), "rechnung.xml")
        self.assertEqual((e.quelle, O.xml_art(e.xml)), ("xrechnung", "CII"))
        self.assertIn("RE-2026-0815", e.text)
        self.assertEqual(O.xml_art(O.analysieren(T.ubl_xml(), "u.xml").xml), "UBL")
        z = O.analysieren(T.zugferd_pdf(), "zugferd.pdf")
        self.assertEqual(z.quelle, "zugferd")
        self.assertEqual(O.xml_art(z.xml), "CII")
        self.assertIsNone(z.pdfa)
        self.assertIn("Gesamtbetrag", z.text)

    def test_textlayer_zeilengetreu(self):
        e = O.analysieren(T.pdf(), "digital.pdf")
        self.assertEqual((e.quelle, e.seiten), ("textlayer", 1))
        self.assertIsNone(e.pdfa)
        self.assertRegex(e.text, r"2\s+2,00\s+Stk\s+Toner schwarz HP 26A\s+89,00\s+19 %\s+178,00")

    def test_unbekanntes_format(self):
        with self.assertRaises(O.OcrFehler):
            O.analysieren(b"abc", "x.docx")
        self.assertTrue(O.analysieren(b"<foo/>", "x.xml").hinweise)

    @skipUnless(O.tesseract_verfuegbar(), "Tesseract nicht installiert")
    def test_ocr_scan_pdf_mit_pdfa(self):
        e = O.analysieren(T.scan_pdf(), "scan.pdf")
        self.assertIn(e.quelle, ("ocr", "ocr-einfach"))
        self.assertIn("RE-2026-0815", e.text)
        self.assertIn("329,57", e.text)
        if O.ocrmypdf_verfuegbar():
            self.assertEqual(e.quelle, "ocr")
            self.assertTrue(e.pdfa.startswith(b"%PDF"))
            self.assertIn(b"pdfaid:part", e.pdfa)  # PDF/A-Kennung in den XMP-Metadaten
            self.assertIn("Gesamtbetrag", O.pdf_text(e.pdfa)[0])  # durchsuchbar

    @skipUnless(O.tesseract_verfuegbar(), "Tesseract nicht installiert")
    def test_ocr_bilder(self):
        for name, daten in (("scan.png", T.png(dpi=200)), ("scan.tif", T.tiff(dpi=200))):
            with self.subTest(name=name):
                e = O.analysieren(daten, name)
                self.assertIn("Kaffeebohnen", e.text)
                self.assertEqual(e.seiten, 1)


class VerarbeitungTests(DmsTestBasis):
    def test_pipeline_textlayer(self):
        d = S.dokument_anlegen(T.pdf(), "re.pdf", art=Dokumentart.SONSTIGES, benutzer=self.user)
        V.verarbeiten(d, self.user)
        self.assertEqual(d.status, Dokument.Status.ERKANNT)
        self.assertEqual(d.textquelle, "textlayer")
        self.assertEqual(list(S.suchen(Dokument.objects.all(), "Kopierpapier")), [d])
        self.assertTrue(d.protokoll.filter(aktion="Texterkennung").exists())

    @skipUnless(O.tesseract_verfuegbar(), "Tesseract nicht installiert")
    def test_pipeline_scan_erzeugt_pdfa_version(self):
        d = S.dokument_anlegen(T.scan_pdf(dpi=200), "scan.pdf", benutzer=self.user)
        call_command("dms_verarbeiten", verbosity=0, stdout=io.StringIO())
        d.refresh_from_db()
        self.assertEqual(d.status, Dokument.Status.ERKANNT, d.fehlermeldung)
        if O.ocrmypdf_verfuegbar():
            pdfa = d.anzeigeversion
            self.assertEqual(pdfa.art, DokumentVersion.Art.PDFA)
            self.assertEqual(pdfa.quelle, d.original)
            self.assertTrue(pdfa.hash_pruefen())
        self.assertIn("Toner", d.ocr_text)

    def test_fehler_landet_im_status(self):
        d = S.dokument_anlegen(b"%PDF-kaputt", "kaputt.pdf", benutzer=self.user)
        V.verarbeiten(d)
        self.assertEqual(d.status, Dokument.Status.FEHLER)
        self.assertTrue(d.fehlermeldung)

    def test_archiviertes_dokument_nicht_neu_verarbeiten(self):
        d = S.dokument_anlegen(T.pdf(), "re.pdf", benutzer=self.user)
        V.verarbeiten(d)
        S.archivieren(d)
        with self.assertRaises(S.DmsFehler):
            V.verarbeiten(d)


# ------------------------------------------------------------------ Stapel, Eingangsordner, Trennung

import os  # noqa: E402
import time  # noqa: E402

from pypdf import PdfReader, PdfWriter  # noqa: E402

from . import trennung as TR  # noqa: E402
from .models import Stapel  # noqa: E402


def sammel_pdf(*teile):
    w = PdfWriter()
    for daten in teile:
        for seite in PdfReader(io.BytesIO(daten)).pages:
            w.add_page(seite)
    puffer = io.BytesIO()
    w.write(puffer)
    return puffer.getvalue()


def trennblatt_scan():
    """Trennblatt als Bild-PDF (wie vom Scanner, ohne Textlayer) – nur per Barcode erkennbar."""
    import pymupdf
    from PIL import Image

    with pymupdf.open(stream=TR.trennblatt_pdf(), filetype="pdf") as doc:
        bild = Image.open(io.BytesIO(doc[0].get_pixmap(dpi=150).tobytes("png"))).convert("L")
    puffer = io.BytesIO()
    bild.save(puffer, "PDF", resolution=150.0)
    return puffer.getvalue()


class TrennungTests(DmsTestBasis):
    def test_bereiche(self):
        self.assertEqual(TR.bereiche_aus_trennern(7, [0, 3]), [(2, 3), (5, 7)])
        self.assertEqual(TR.bereiche_aus_trennern(4, [3]), [(1, 3)])
        self.assertEqual(TR.bereiche_parsen("1-2; 3,4-5", 5), [(1, 2), (3, 3), (4, 5)])
        for falsch in ("1-2; 2-3", "0-1; 2", "1-9; 2", "abc; 1", "1-3"):
            with self.subTest(falsch=falsch), self.assertRaises(S.DmsFehler):
                TR.bereiche_parsen(falsch, 5)

    def test_trennblatt_text_und_barcode(self):
        self.assertEqual(TR.trennseiten(sammel_pdf(T.pdf(), TR.trennblatt_pdf(), T.pdf())), [1])
        if TR.zbar_verfuegbar():
            self.assertEqual(TR.trennseiten(sammel_pdf(T.pdf(), trennblatt_scan(), T.pdf(), T.pdf())), [1])

    def test_automatische_trennung_im_stapel(self):
        r2 = T.beispiel(rechnungsnummer="RE-2026-0999")
        stapel = S.stapel_anlegen(automatisch_trennen=True, benutzer=self.user)
        d = S.dokument_anlegen(sammel_pdf(T.pdf(), TR.trennblatt_pdf(), T.pdf(r2), T.pdf(r2)), "stapelscan.pdf",
                               art=Dokumentart.SONSTIGES, stapel=stapel, benutzer=self.user)
        V.verarbeiten(d, self.user)
        d.refresh_from_db()
        self.assertEqual((d.status, d.aufgeteilt, d.seiten), (Dokument.Status.ABGELEGT, True, 4))
        teile = list(d.teile.order_by("seiten_von"))
        self.assertEqual([(t.seiten_von, t.seiten_bis) for t in teile], [(1, 1), (3, 4)])
        self.assertTrue(all(t.stapel == stapel and t.status == Dokument.Status.NEU for t in teile))
        self.assertEqual(V.warteschlange_abarbeiten(stapel=stapel), (2, 0))
        self.assertIn("RE-2026-0999", Dokument.objects.get(pk=teile[1].pk).ocr_text)
        self.assertTrue(d.original.hash_pruefen())  # Sammel-PDF unverändert
        f = stapel.fortschritt()
        self.assertEqual((f["gesamt"], f["offen"], f["prozent"]), (3, 0, 100))

    def test_ohne_trennung_bleibt_dokument_ganz(self):
        stapel = S.stapel_anlegen(automatisch_trennen=False)
        d = S.dokument_anlegen(sammel_pdf(T.pdf(), TR.trennblatt_pdf(), T.pdf()), "s.pdf", stapel=stapel)
        V.verarbeiten(d)
        self.assertEqual((d.status, d.teile.count()), (Dokument.Status.ERKANNT, 0))

    def test_manuelle_trennung_oberflaeche(self):
        self.client.force_login(self.user)
        d = S.dokument_anlegen(sammel_pdf(T.pdf(), T.pdf(), T.pdf()), "s.pdf", stapel=S.stapel_anlegen())
        self.assertEqual(self.client.get(reverse("dms:trennen", args=[d.pk])).status_code, 200)
        self.client.post(reverse("dms:trennen", args=[d.pk]), {"bereiche": "1-1; 2-2; 3-3"})
        self.assertEqual(d.teile.count(), 3)
        r = self.client.post(reverse("dms:trennen", args=[d.pk]), {"bereiche": "1; 2"})
        self.assertEqual(d.teile.count(), 3)  # zweite Trennung abgelehnt
        self.assertEqual(r.status_code, 200)
        r = self.client.get(reverse("dms:trennblatt"))
        self.assertTrue(r.content.startswith(b"%PDF"))


class StapelTests(DmsTestBasis):
    def test_fortschritt_und_schrittweise_verarbeitung(self):
        self.client.force_login(self.user)
        stapel = S.stapel_anlegen()
        for i in range(3):
            S.dokument_anlegen(T.pdf(T.beispiel(rechnungsnummer=f"RE-{i}")), f"r{i}.pdf", stapel=stapel)
        r = self.client.get(reverse("dms:stapel_status", args=[stapel.pk])).json()
        self.assertEqual((r["gesamt"], r["offen"], r["prozent"]), (3, 3, 0))
        self.assertEqual(self.client.get(reverse("dms:stapel", args=[stapel.pk]) + "?start=1").status_code, 200)
        url = reverse("dms:stapel_verarbeiten", args=[stapel.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        r = self.client.post(url).json()
        self.assertEqual((r["offen"], r["prozent"]), (2, 33))
        self.client.post(url)
        r = self.client.post(url).json()
        self.assertEqual((r["offen"], r["prozent"]), (0, 100))
        self.assertIsNone(self.client.post(url).json()["verarbeitet"])

    def test_upload_warnt_bei_dublette(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_login(self.user)
        S.dokument_anlegen(b"<a/>", "a.xml")
        r = self.client.post(reverse("dms:hochladen"), {"dateien": [SimpleUploadedFile("a.xml", b"<a/>")],
                                                        "art": Dokumentart.SONSTIGES}, follow=True)
        self.assertContains(r, "identische Datei bereits")


class EingangsordnerTests(DmsTestBasis):
    def setUp(self):
        self.ordner = tempfile.mkdtemp(prefix="dms_eingang_", dir=TEMP_MEDIA)

    def _datei(self, name, daten, alt=True):
        pfad = os.path.join(self.ordner, name)
        os.makedirs(os.path.dirname(pfad), exist_ok=True)
        with open(pfad, "wb") as f:
            f.write(daten)
        if alt:
            os.utime(pfad, (time.time() - 60, time.time() - 60))
        return pfad

    def test_hotfolder(self):
        rechnung = T.pdf()  # einmal erzeugen: fpdf schreibt einen Zeitstempel in jedes PDF
        self._datei("rechnung.pdf", rechnung)
        self._datei("vertrag/mietvertrag.xml", b"<vertrag>Miete</vertrag>")
        self._datei("virus.exe", b"MZ")
        self._datei("noch_im_schreiben.pdf", T.pdf(T.beispiel(rechnungsnummer="X")), alt=False)
        aus = io.StringIO()
        call_command("dms_eingang", ordner=self.ordner, stdout=aus)
        self.assertIn("2 übernommen", aus.getvalue())
        stapel = Stapel.objects.get(quelle=Stapel.Quelle.EINGANGSORDNER)
        arten = dict(stapel.dokumente.values_list("titel", "art"))
        self.assertEqual(arten, {"rechnung": Dokumentart.EINGANGSRECHNUNG, "mietvertrag": Dokumentart.VERTRAG})
        self.assertFalse(stapel.dokumente.filter(status=Dokument.Status.NEU).exists())  # gleich verarbeitet
        self.assertTrue(os.path.exists(os.path.join(self.ordner, "noch_im_schreiben.pdf")))
        self.assertTrue(os.path.exists(os.path.join(self.ordner, "fehler", "virus.exe.txt")))
        verarbeitet = os.listdir(os.path.join(self.ordner, "verarbeitet"))
        self.assertEqual(len(verarbeitet), 1)
        # Erneut eingeworfene identische Datei → „doppelt“
        self._datei("rechnung_kopie.pdf", rechnung)
        call_command("dms_eingang", ordner=self.ordner, stdout=io.StringIO())
        self.assertTrue(os.path.exists(os.path.join(self.ordner, "doppelt", "rechnung_kopie.pdf")))
        self.assertEqual(Dokument.objects.count(), 2)

    def test_fehlender_ordner(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("dms_eingang", ordner=os.path.join(self.ordner, "gibtsnicht"))


# ------------------------------------------------------------------ Belegerkennung Eingangsrechnungen

from decimal import Decimal  # noqa: E402

from apps.stammdaten.models import Geschaeftspartner  # noqa: E402

from . import erkennung as E  # noqa: E402

FREMDES_LAYOUT = """Elektro Huber KG
Gewerbering 4, 93047 Regensburg
Rechnungs-Nr.: 2026/00471   Datum: 05.02.2026
Steuernummer 244/123/45678 · USt-ID DE 129 273 398
Menge   Artikel   Preis   Summe
3   Stk.   LED-Panel 60x60   49,90   149,70
1   pauschal   Montage   1.250,00   1.250,00
Summe netto   1.399,70 €
MwSt 19 % auf 1.399,70   265,94 €
Rechnungsbetrag   1.665,64 €
2 % Skonto bei Zahlung innerhalb von 10 Tagen, 30 Tage netto.
IBAN: AT61 1904 3002 3457 3201
"""


class ExtraktionTests(TestCase):
    """Regelbasierte Extraktion ohne Datenbank."""

    def werte(self, erg):
        return {k: v["wert"] for k, v in erg["felder"].items()}

    def test_hilfsfunktionen(self):
        self.assertEqual(E.zahl("1.234,56"), Decimal("1234.56"))
        self.assertEqual(E.zahl("12.50"), Decimal("12.50"))
        self.assertEqual(E.datum("5.2.26"), date(2026, 2, 5))
        self.assertTrue(E.iban_gueltig("DE89 3704 0044 0532 0130 00"))
        self.assertFalse(E.iban_gueltig("DE89370400440532013001"))

    def test_textlayer_kopf_und_positionen(self):
        erg = E.extrahieren(O.analysieren(T.pdf(), "re.pdf").text)
        w = self.werte(erg)
        self.assertEqual(erg["quelle"], "text")
        self.assertEqual(w["rechnungsnummer"], "RE-2026-0815")
        self.assertEqual((w["rechnungsdatum"], w["faellig_am"]), ("2026-03-12", "2026-04-11"))
        self.assertEqual((w["netto"], w["steuer"], w["brutto"]), ("283.00", "46.57", "329.57"))
        self.assertEqual((w["ust_id"], w["iban"]), ("DE811569869", "DE89370400440532013000"))
        self.assertEqual(w["zahlungsziel_tage"], 30)
        self.assertEqual(len(erg["positionen"]), 3)
        p = erg["positionen"][1]
        self.assertEqual((p["menge"], p["beschreibung"], p["einzelpreis"], p["betrag"], p["steuersatz"]),
                         ("2.000", "Toner schwarz HP 26A", "89.0000", "178.00", "19"))
        self.assertEqual([s["satz"] for s in erg["steuersaetze"]], ["19", "7"])
        self.assertGreaterEqual(erg["felder"]["rechnungsnummer"]["konfidenz"], 0.9)

    def test_anderes_layout(self):
        erg = E.extrahieren(FREMDES_LAYOUT)
        w = self.werte(erg)
        self.assertEqual(w["rechnungsnummer"], "2026/00471")
        self.assertEqual(w["rechnungsdatum"], "2026-02-05")
        self.assertEqual(w["ust_id"], "DE129273398")
        self.assertEqual(w["iban"], "AT611904300234573201")
        self.assertEqual((w["netto"], w["steuer"], w["brutto"]), ("1399.70", "265.94", "1665.64"))
        self.assertEqual(w["faellig_am"], "2026-03-07")
        self.assertIn("2 % Skonto innerhalb 10 Tagen", w["zahlungsbedingung"])
        self.assertEqual(erg["steuersaetze"][0], {"satz": "19", "basis": "1399.70", "betrag": "265.94"})
        self.assertEqual([p["betrag"] for p in erg["positionen"]], ["149.70", "1250.00"])
        self.assertEqual({p["steuersatz"] for p in erg["positionen"]}, {"19"})  # einziger Satz der Rechnung

    def test_eigene_firma_wird_ignoriert(self):
        text = "USt-IdNr. DE111111125\nLieferant USt-IdNr. DE811569869"
        self.assertEqual(self.werte(E.extrahieren(text, eigene={"ust_id": "DE111111125"}))["ust_id"], "DE811569869")

    def test_e_rechnungen_konfidenz_eins(self):
        for name, daten in (("z.pdf", T.zugferd_pdf()), ("x.xml", T.ubl_xml()), ("c.xml", T.cii_xml())):
            with self.subTest(name=name):
                e = O.analysieren(daten, name)
                erg = E.extrahieren(e.text, e.xml)
                w = self.werte(erg)
                self.assertEqual(erg["quelle"], "xml")
                self.assertEqual((w["rechnungsnummer"], w["brutto"], w["faellig_am"]),
                                 ("RE-2026-0815", "329.57", "2026-04-11"))
                self.assertEqual(len(erg["positionen"]), 3)
                self.assertEqual(erg["felder"]["brutto"]["konfidenz"], 1.0)

    @skipUnless(O.tesseract_verfuegbar(), "Tesseract nicht installiert")
    def test_ocr_scan(self):
        erg = E.extrahieren(O.analysieren(T.png(), "scan.png").text)
        w = self.werte(erg)
        self.assertEqual((w["rechnungsnummer"], w["brutto"], w["netto"]), ("RE-2026-0815", "329.57", "283.00"))
        self.assertEqual(w["iban"], "DE89370400440532013000")
        self.assertEqual(sum(Decimal(p["betrag"]) for p in erg["positionen"]), Decimal("283.00"))


class BelegerkennungTests(DmsTestBasis):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.lieferant = Geschaeftspartner.objects.create(name="Bürobedarf Schneider GmbH", ist_lieferant=True,
                                                         ust_id="DE811569869")

    def er(self, daten=None, name="re.pdf"):
        d = S.dokument_anlegen(daten or T.pdf(), name, art=Dokumentart.EINGANGSRECHNUNG, benutzer=self.user)
        V.verarbeiten(d, self.user)
        return d

    def test_zuordnung_ust_id_und_status(self):
        d = self.er()
        self.assertEqual(d.status, Dokument.Status.ERKANNT, d.erkennung.get("plausibilitaet"))
        self.assertEqual(d.partner, self.lieferant)
        self.assertEqual(d.erkennung["lieferant"]["methode"], "USt-IdNr.")
        self.assertEqual(d.belegdatum, date(2026, 3, 12))
        self.assertGreaterEqual(d.konfidenz, E.SCHWELLE_ERKANNT)
        self.assertTrue(d.protokoll.filter(aktion="Belegerkennung").exists())
        # Kontierungsvorschlag: Standard-Aufwand mit Vorsteuerschlüssel
        p = d.erkennung["positionen"][0]
        self.assertEqual((p["konto"], p["steuerschluessel"], p["kontoquelle"]), ("3000", "V19", "Standard-Aufwand"))
        self.assertEqual(d.erkennung["positionen"][2]["steuerschluessel"], "V7")

    def test_zuordnung_iban_und_name(self):
        self.lieferant.ust_id = ""
        self.lieferant.iban = "DE89 3704 0044 0532 0130 00"
        self.lieferant.save()
        f = E.extrahieren(O.analysieren(T.pdf(), "re.pdf").text)["felder"]
        self.assertEqual(E.lieferant_zuordnen(f)["methode"], "IBAN")
        self.lieferant.iban = ""
        self.lieferant.save()
        z = E.lieferant_zuordnen(f, "Bürobedarf Schneider GmbH\n…")
        self.assertEqual((z["partner_id"], z["methode"]), (self.lieferant.pk, "Name"))
        self.assertEqual(E.lieferant_zuordnen({"lieferant_name": {"wert": "Ganz Anders AG"}}), {})

    def test_lernregel_hat_vorrang(self):
        from apps.fibu import services as F

        from .models import Kontierungsregel

        Kontierungsregel.objects.create(partner=self.lieferant, konto=F.konto("4930"))
        Kontierungsregel.objects.create(partner=self.lieferant, stichwort="kaffee", konto=F.konto("4900"))
        d = self.er()
        konten = [(p["konto"], p["kontoquelle"]) for p in d.erkennung["positionen"]]
        self.assertEqual(konten[0], ("4930", "Lernregel Lieferant"))
        self.assertEqual(konten[2], ("4900", "Lernregel „kaffee“"))

    def test_unbekannter_lieferant_zu_pruefen(self):
        d = self.er(T.pdf(T.beispiel(ust_id="DE999999999", lieferant="Unbekannt & Co")))
        self.assertEqual(d.status, Dokument.Status.PRUEFEN)
        self.assertIsNone(d.partner)
        self.assertTrue(any("Kein Lieferant" in m["text"] for m in d.erkennung["plausibilitaet"]))

    def test_summenabweichung(self):
        erg = E.extrahieren(O.analysieren(T.pdf(), "re.pdf").text)
        erg["lieferant"] = {"partner_id": self.lieferant.pk, "konfidenz": 0.99}
        erg["felder"]["brutto"]["wert"] = "330.00"
        texte = [m["text"] for m in E.plausibilitaet(erg) if m["stufe"] == "fehler"]
        self.assertTrue(any("Summen stimmen nicht" in t for t in texte), texte)
        erg["felder"]["brutto"]["wert"] = "329.57"
        erg["positionen"].pop()
        texte = [m["text"] for m in E.plausibilitaet(erg) if m["stufe"] == "fehler"]
        self.assertTrue(any("Summe der Positionen" in t for t in texte), texte)

    def test_dublette_lieferant_und_rechnungsnummer(self):
        erstes = self.er()
        self.assertEqual(erstes.status, Dokument.Status.ERKANNT)
        zweites = self.er(T.zugferd_pdf(), "zugferd.pdf")
        self.assertEqual(zweites.status, Dokument.Status.PRUEFEN)
        meldung = [m["text"] for m in zweites.erkennung["plausibilitaet"] if "Dublette" in m["text"]]
        self.assertTrue(meldung and erstes.nummer in meldung[0])

    def test_iban_abweichung_warnt(self):
        self.lieferant.iban = "DE02120300000000202051"
        self.lieferant.save()
        d = self.er()
        self.assertTrue(any("IBAN der Rechnung" in m["text"] and m["stufe"] == "warnung"
                            for m in d.erkennung["plausibilitaet"]))

    def test_andere_dokumentart_ohne_belegerkennung(self):
        d = S.dokument_anlegen(T.pdf(), "re.pdf", art=Dokumentart.VERTRAG, benutzer=self.user)
        V.verarbeiten(d)
        self.assertEqual(d.status, Dokument.Status.ERKANNT)
        self.assertEqual(d.erkennung, {})


# ------------------------------------------------------------------ Prüfmaske

class PruefmaskeTests(DmsTestBasis):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.lieferant = Geschaeftspartner.objects.create(name="Bürobedarf Schneider GmbH", ist_lieferant=True,
                                                         ust_id="DE811569869")

    def setUp(self):
        self.client.force_login(self.user)

    def er(self, r=None):
        d = S.dokument_anlegen(T.pdf(r), "re.pdf", art=Dokumentart.EINGANGSRECHNUNG, benutzer=self.user)
        V.verarbeiten(d, self.user)
        return d

    def post_daten(self, d, **kopf):
        from django.utils.formats import localize_input

        from . import pruefung as P

        k, positionen = P.formular_daten(d)
        k.update(kopf)
        daten = {"aktion": "speichern", "pos-TOTAL_FORMS": str(len(positionen)), "pos-INITIAL_FORMS": "0",
                 "pos-MIN_NUM_FORMS": "0", "pos-MAX_NUM_FORMS": "1000"}
        for name, wert in k.items():
            daten[f"kopf-{name}"] = "" if wert is None else (
                wert.isoformat() if hasattr(wert, "isoformat") else localize_input(wert))
        from django.utils.formats import localize_input

        for i, p in enumerate(positionen):
            for name, wert in p.items():
                daten[f"pos-{i}-{name}"] = "" if wert is None else localize_input(wert)
        return daten

    def test_split_view(self):
        d = self.er()
        r = self.client.get(reverse("dms:pruefen", args=[d.pk]))
        self.assertContains(r, "Plausibilitätsprüfung")
        self.assertContains(r, 'value="RE-2026-0815"')
        self.assertContains(r, reverse("dms:datei", args=[d.pk]))  # Dokument links (iframe)
        self.assertContains(r, "Toner schwarz HP 26A")
        self.assertContains(r, "Standard-Aufwand")

    def test_korrektur_lieferant_und_konto(self):
        d = self.er(T.beispiel(ust_id="DE999999999", lieferant="Unbekannt & Co"))
        self.assertEqual(d.status, Dokument.Status.PRUEFEN)
        daten = self.post_daten(d, lieferant=self.lieferant.pk)
        daten["pos-0-konto"] = "4930"
        r = self.client.post(reverse("dms:pruefen", args=[d.pk]), daten)
        self.assertRedirects(r, reverse("dms:pruefen", args=[d.pk]))
        d.refresh_from_db()
        self.assertEqual(d.status, Dokument.Status.ERKANNT, d.erkennung["plausibilitaet"])
        self.assertEqual(d.partner, self.lieferant)
        self.assertEqual(d.erkennung["lieferant"]["methode"], "manuell")
        self.assertEqual(d.erkennung["positionen"][0]["konto"], "4930")
        self.assertEqual(d.erkennung["positionen"][0]["kontoquelle"], "manuell")
        self.assertTrue(d.erkennung["korrigiert"])
        self.assertIn("lieferant", d.protokoll.filter(aktion="Korrektur").last().text)

    def test_korrektur_mit_summenfehler_bleibt_zu_pruefen(self):
        d = self.er()
        r = self.client.post(reverse("dms:pruefen", args=[d.pk]), self.post_daten(d, brutto="400,00"))
        self.assertEqual(r.status_code, 302)
        d.refresh_from_db()
        self.assertEqual(d.status, Dokument.Status.PRUEFEN)
        self.assertEqual(d.wert("brutto"), "400.00")
        self.assertTrue(d.erkennung["felder"]["brutto"]["manuell"])

    def test_position_hinzufuegen_und_loeschen(self):
        d = self.er()
        daten = self.post_daten(d, netto="223,00", steuer="42,37", brutto="265,37")
        daten["pos-2-DELETE"] = "on"  # Kaffee (7 %) entfernen
        self.client.post(reverse("dms:pruefen", args=[d.pk]), daten)
        d.refresh_from_db()
        self.assertEqual(len(d.erkennung["positionen"]), 2)
        self.assertEqual(d.status, Dokument.Status.ERKANNT, d.erkennung["plausibilitaet"])

    def test_nur_lesen_darf_nicht_korrigieren(self):
        d = self.er()
        leser = get_user_model().objects.create_user("leser", password="x")
        leser.erp_rollen.add(Rolle.objects.get(name="Vertrieb"))  # dms: nur lesen
        self.client.force_login(leser)
        self.assertEqual(self.client.get(reverse("dms:pruefen", args=[d.pk])).status_code, 200)
        r = self.client.post(reverse("dms:pruefen", args=[d.pk]), self.post_daten(d))
        self.assertEqual(r.status_code, 403)

    def test_lernregeln_seite(self):
        from apps.fibu import services as F

        r = self.client.post(reverse("dms:regeln"), {"partner": self.lieferant.pk, "stichwort": "toner",
                                                     "konto": F.konto("4930").pk, "steuerschluessel": "V19"})
        self.assertRedirects(r, reverse("dms:regeln"))
        self.assertContains(self.client.get(reverse("dms:regeln")), "toner")


# ------------------------------------------------------------------ Verbuchung in die Fibu

class VerbuchungTests(DmsTestBasis):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from apps.fibu import services as F

        F.geschaeftsjahr_anlegen(2026)
        cls.lieferant = Geschaeftspartner.objects.create(name="Bürobedarf Schneider GmbH", ist_lieferant=True,
                                                         ust_id="DE811569869")

    def er(self, r=None, daten=None, name="re.pdf"):
        d = S.dokument_anlegen(daten or T.pdf(r), name, art=Dokumentart.EINGANGSRECHNUNG, benutzer=self.user)
        V.verarbeiten(d, self.user)
        return d

    def test_verbuchen_doppelte_buchfuehrung(self):
        from apps.fibu.models import OffenerPosten

        from . import verbuchung as VB

        d = self.er()
        er = VB.verbuchen(d, self.user)
        d.refresh_from_db()
        # Eingangsrechnung der Fibu
        self.assertEqual((er.lieferant, er.rechnungsnummer, er.rechnungsdatum, er.faellig_am),
                         (self.lieferant, "RE-2026-0815", date(2026, 3, 12), date(2026, 4, 11)))
        self.assertEqual((er.summe_netto, er.summe_steuer, er.summe_brutto),
                         (Decimal("283.00"), Decimal("46.57"), Decimal("329.57")))
        self.assertEqual(er.positionen.count(), 3)
        self.assertEqual(er.status, "GEBUCHT")
        # Buchung: Aufwand + Vorsteuer an Kreditor, ausgeglichen
        zeilen = list(er.buchung.zeilen.select_related("konto"))
        self.assertEqual(sum(z.soll for z in zeilen), sum(z.haben for z in zeilen))
        konten = {}
        for z in zeilen:
            konten[z.konto.nummer] = konten.get(z.konto.nummer, 0) + z.soll - z.haben
        self.assertEqual(konten["3000"], Decimal("283.00"))
        self.assertEqual((konten["1576"], konten["1571"]), (Decimal("42.37"), Decimal("4.20")))
        kreditor = [z for z in zeilen if z.partner_id == self.lieferant.pk]
        self.assertEqual(kreditor[0].haben, Decimal("329.57"))
        # Offener Posten
        op = OffenerPosten.objects.get(eingangsrechnung=er)
        self.assertEqual((op.art, op.offen, op.fremdbelegnr), ("K", Decimal("329.57"), "RE-2026-0815"))
        # Rückverweis und GoBD
        self.assertEqual((d.status, d.eingangsrechnung, d.buchung), (Dokument.Status.GEBUCHT, er, er.buchung))
        self.assertTrue(d.archiviert)
        self.assertEqual(list(er.dms_dokumente.all()), [d])
        self.assertTrue(d.protokoll.filter(aktion="Verbucht").exists())
        with self.assertRaises(S.DmsFehler):
            VB.verbuchen(d, self.user)

    def test_lernregel_aus_buchung(self):
        from apps.fibu import services as F

        from . import pruefung as P
        from . import verbuchung as VB
        from .models import Kontierungsregel

        d = self.er()
        kopf, pos = P.formular_daten(d)
        kopf["lieferant"] = self.lieferant
        pos[1]["konto"] = F.konto("4930")  # Toner manuell auf Bürobedarf
        P.korrektur_speichern(d, kopf, pos, self.user)
        VB.verbuchen(d, self.user)
        self.assertTrue(Kontierungsregel.objects.filter(partner=self.lieferant, stichwort="toner",
                                                        konto__nummer="4930").exists())
        # Folgerechnung desselben Lieferanten: Vorschlag aus den Lernregeln
        d2 = self.er(T.beispiel(rechnungsnummer="RE-2026-0999"))
        vorschlag = [(p["konto"], p["kontoquelle"]) for p in d2.erkennung["positionen"]]
        self.assertEqual(vorschlag[1], ("4930", "Lernregel „toner“"))
        self.assertEqual(vorschlag[0][1], "Lernregel Lieferant")

    def test_plausibilitaetsfehler_verhindert_buchung(self):
        from apps.fibu.models import Eingangsrechnung

        from . import verbuchung as VB

        d = self.er(T.beispiel(ust_id="DE999999999", lieferant="Unbekannt & Co"))
        with self.assertRaises(S.DmsFehler):
            VB.verbuchen(d, self.user)
        self.assertFalse(Eingangsrechnung.objects.exists())
        d.refresh_from_db()
        self.assertFalse(d.archiviert)

    def test_dublette_nach_buchung(self):
        from . import verbuchung as VB

        er = VB.verbuchen(self.er(), self.user)
        zweit = self.er(daten=T.zugferd_pdf(), name="zugferd.pdf")
        self.assertEqual(zweit.status, Dokument.Status.PRUEFEN)
        self.assertTrue(any(er.nummer in m["text"] for m in zweit.erkennung["plausibilitaet"]))
        with self.assertRaises(S.DmsFehler):
            VB.verbuchen(zweit, self.user)

    def test_oberflaeche_freigeben_und_rueckverweis(self):
        self.client.force_login(self.user)
        d = self.er()
        daten = PruefmaskeTests.post_daten(self, d)
        daten["aktion"] = "verbuchen"
        r = self.client.post(reverse("dms:pruefen", args=[d.pk]), daten)
        d.refresh_from_db()
        self.assertIsNotNone(d.eingangsrechnung)
        self.assertRedirects(r, reverse("fibu:er", args=[d.eingangsrechnung.pk]))
        self.assertContains(self.client.get(r.url), reverse("dms:detail", args=[d.pk]))
        self.assertContains(self.client.get(reverse("fibu:buchung", args=[d.buchung.pk])),
                            reverse("dms:detail", args=[d.pk]))

    def test_ohne_freigaberecht(self):
        d = self.er()
        einkauf = get_user_model().objects.create_user("einkauf", password="x")
        einkauf.erp_rollen.add(Rolle.objects.get(name="Einkauf"))  # dms: lesen + anlegen
        self.client.force_login(einkauf)
        self.assertNotContains(self.client.get(reverse("dms:pruefen", args=[d.pk])), 'value="verbuchen"')
        self.assertEqual(self.client.post(reverse("dms:verbuchen", args=[d.pk])).status_code, 403)
        daten = PruefmaskeTests.post_daten(self, d)
        daten["aktion"] = "verbuchen"
        self.assertEqual(self.client.post(reverse("dms:pruefen", args=[d.pk]), daten).status_code, 403)


class DemodatenTests(DmsTestBasis):
    """Demodaten-Command: Stapel, Trennung, Erkennung und Verbuchung der ZUGFeRD-Rechnung (idempotent)."""

    @skipUnless(O.tesseract_verfuegbar(), "Tesseract nicht installiert")
    def test_dms_demodaten(self):
        from django.utils import timezone

        from apps.dms.models import Stapel
        from apps.fibu import services as F

        F.geschaeftsjahr_anlegen(timezone.localdate().year)
        call_command("dms_demodaten", verbosity=0)
        stapel = Stapel.objects.get(bezeichnung="Demodaten Dokumentenmanagement")
        docs = Dokument.objects.filter(stapel=stapel)
        # 4 Rechnungsdateien + Vertrag; das Sammel-PDF wird in 2 Teildokumente geteilt
        self.assertEqual(docs.filter(eltern__isnull=True).count(), 5)
        self.assertEqual(docs.filter(eltern__isnull=False).count(), 2)
        gebucht = docs.filter(status=Dokument.Status.GEBUCHT)
        self.assertEqual(gebucht.count(), 1)
        self.assertIsNotNone(gebucht.first().eingangsrechnung)
        self.assertTrue(gebucht.first().archiviert)
        self.assertFalse(docs.filter(status=Dokument.Status.FEHLER).exists())
        # simulierter E-Mail-Eingang mit eigenem Stapel, Lieferant über die E-Mail-Adresse zugeordnet
        from apps.dms.models import MailEingang

        mail = MailEingang.objects.get()
        self.assertEqual((mail.status, mail.anhaenge, mail.stapel.quelle), ("UEBERNOMMEN", 1, "EMAIL"))
        self.assertEqual(mail.stapel.dokumente.get().partner.ust_id, "DE811569869")
        self.assertTrue(docs.filter(art=Dokumentart.VERTRAG, tags__name="Rahmenvertrag").exists())
        call_command("dms_demodaten", verbosity=0)  # zweiter Lauf legt nichts doppelt an
        self.assertEqual(Stapel.objects.filter(bezeichnung="Demodaten Dokumentenmanagement").count(), 1)


class RestApiTests(DmsTestBasis):
    """REST-API /api/v1/dms/…: Token-Auth, Rechte, Upload als Stapel, Suche, Detail, Download, Tags, Verbuchen."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from apps.fibu import services as F

        F.geschaeftsjahr_anlegen(2026)
        cls.lieferant = Geschaeftspartner.objects.create(name="Bürobedarf Schneider GmbH", ist_lieferant=True,
                                                         ust_id="DE811569869")

    def setUp(self):
        from rest_framework.authtoken.models import Token
        from rest_framework.test import APIClient

        self.api = APIClient()
        self.api.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=self.user).key}")

    def hochladen(self, *dateien, **felder):
        from django.core.files.uploadedfile import SimpleUploadedFile

        daten = {"dateien": [SimpleUploadedFile(n, b) for n, b in dateien], **felder}
        return self.api.post("/api/v1/dms/dokumente/", daten, format="multipart")

    def test_ohne_anmeldung_abgewiesen(self):
        from rest_framework.test import APIClient

        self.assertIn(APIClient().get("/api/v1/dms/dokumente/").status_code, (401, 403))

    def test_upload_stapel_detail_und_download(self):
        r = self.hochladen(("a.xml", T.cii_xml()), ("b.pdf", T.pdf()), ("x.exe", b"MZ"))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(len(r.data["dokumente"]), 2)
        self.assertEqual(len(r.data["fehler"]), 1)
        self.assertEqual(r.data["stapel"]["fortschritt"]["gesamt"], 2)
        sid = r.data["stapel"]["id"]
        # Stapel abarbeiten und Status abfragen
        r = self.api.post(f"/api/v1/dms/stapel/{sid}/verarbeiten/")
        self.assertEqual((r.status_code, r.data["ok"], r.data["fehler"]), (200, 2, 0))
        d = Dokument.objects.filter(stapel_id=sid).order_by("pk").last()
        r = self.api.get(f"/api/v1/dms/dokumente/{d.pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("RE-2026-0815", r.data["ocr_text"])
        self.assertEqual(r.data["erkennung"]["felder"]["rechnungsnummer"]["wert"], "RE-2026-0815")
        self.assertEqual(r.data["versionen"][0]["art"], "ORIGINAL")
        self.assertEqual(self.api.get(f"/api/v1/dms/dokumente/{d.pk}/status/").data["status"], d.status)
        # Download Original mit Hash
        v = d.original
        r = self.api.get(f"/api/v1/dms/dokumente/{d.pk}/datei/?version={v.pk}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(S.sha256(b"".join(r.streaming_content)), v.sha256)
        self.assertEqual(r["X-Sha256"], v.sha256)
        self.assertEqual(self.api.get(f"/api/v1/dms/dokumente/{d.pk}/datei/?version=999999").status_code, 404)
        # Protokoll
        aktionen = [p["aktion"] for p in self.api.get(f"/api/v1/dms/dokumente/{d.pk}/protokoll/").data]
        self.assertEqual(aktionen[0], "Eingang")

    def test_liste_filter_volltext_und_tags(self):
        a = S.dokument_anlegen(b"<a/>", "a.xml", art=Dokumentart.VERTRAG, titel="Wartungsvertrag Heizung")
        S.dokument_anlegen(b"<b/>", "b.xml", art=Dokumentart.SONSTIGES, titel="Kantinenplan")
        r = self.api.get("/api/v1/dms/dokumente/", {"q": "Wartungsvertrag"})
        self.assertEqual([x["nummer"] for x in r.data["results"]], [a.nummer])
        self.assertEqual(self.api.get("/api/v1/dms/dokumente/", {"art": "SONSTIGES"}).data["count"], 1)
        r = self.api.post(f"/api/v1/dms/dokumente/{a.pk}/tags/", {"tags": ["Heizung", "2026"]}, format="json")
        self.assertEqual(sorted(t["name"] for t in r.data), ["2026", "Heizung"])
        r = self.api.post(f"/api/v1/dms/dokumente/{a.pk}/tags/", {"tags": ["2026"], "modus": "entfernen"},
                          format="json")
        self.assertEqual([t["name"] for t in r.data], ["Heizung"])
        self.assertEqual(self.api.get("/api/v1/dms/dokumente/", {"tag": "Heizung"}).data["count"], 1)
        self.assertEqual(self.api.get("/api/v1/dms/tags/").data["count"], 2)
        # Metadaten ändern, Archivieren, danach gesperrt
        r = self.api.patch(f"/api/v1/dms/dokumente/{a.pk}/", {"bemerkung": "Laufzeit 3 Jahre"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/dms/dokumente/{a.pk}/archivieren/").data["archiviert"], True)
        self.assertEqual(self.api.patch(f"/api/v1/dms/dokumente/{a.pk}/", {"titel": "x"}, format="json")
                         .status_code, 409)
        self.assertEqual(self.api.delete(f"/api/v1/dms/dokumente/{a.pk}/").status_code, 409)

    def test_verbuchen_ueber_api(self):
        r = self.hochladen(("re.pdf", T.pdf()), sofort_verarbeiten="true")
        self.assertEqual(r.status_code, 201, r.content)
        did = r.data["dokumente"][0]["id"]
        r = self.api.post(f"/api/v1/dms/dokumente/{did}/verbuchen/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.data["eingangsrechnung"].startswith("ER-"))
        self.assertTrue(r.data["dokument"]["archiviert"])
        self.assertEqual(self.api.post(f"/api/v1/dms/dokumente/{did}/verbuchen/").status_code, 409)

    def test_rechte_pro_modul(self):
        from rest_framework.authtoken.models import Token
        from rest_framework.test import APIClient

        d = S.dokument_anlegen(T.pdf(), "re.pdf", art=Dokumentart.EINGANGSRECHNUNG)
        vertrieb = get_user_model().objects.create_user("vertrieb_api", password="x")
        vertrieb.erp_rollen.add(Rolle.objects.get(name="Vertrieb"))  # dms: nur lesen
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=vertrieb).key}")
        self.assertEqual(api.get("/api/v1/dms/dokumente/").status_code, 200)
        self.assertEqual(api.post(f"/api/v1/dms/dokumente/{d.pk}/verarbeiten/").status_code, 403)
        self.assertEqual(api.post(f"/api/v1/dms/dokumente/{d.pk}/tags/", {"tags": ["x"]}, format="json")
                         .status_code, 403)
        self.assertEqual(api.post(f"/api/v1/dms/dokumente/{d.pk}/verbuchen/").status_code, 403)
        gast = get_user_model().objects.create_user("ohne_rolle", password="x")
        api.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=gast).key}")
        self.assertEqual(api.get("/api/v1/dms/dokumente/").status_code, 403)


class FakeImap:
    """Minimaler IMAP4_SSL-Ersatz: Postfach mit Ordnern, UIDs und Flags (kein Netzwerk)."""

    postfach = {}
    instanzen = []

    def __init__(self, host, port, ssl_context=None, timeout=None):
        if host == "unerreichbar":
            raise OSError("Name or service not known")
        self.host, self.port, self.ssl_context = host, port, ssl_context
        self.capabilities = ("IMAP4REV1", "MOVE")
        self.ordner, self.angemeldet, self.expunged = None, False, 0
        FakeImap.instanzen.append(self)

    def login(self, user, passwort):
        import imaplib

        if passwort != "geheim":
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        self.angemeldet = True
        return "OK", [b"angemeldet"]

    def select(self, ordner):
        ordner = ordner.strip('"')
        if ordner not in self.postfach:
            return "NO", [b"unbekannt"]
        self.ordner = ordner
        return "OK", [str(len(self.postfach[ordner])).encode()]

    def uid(self, befehl, *args):
        mails = self.postfach[self.ordner]
        if befehl == "SEARCH":
            uids = [u for u, m in mails.items() if args[1] == "ALL" or "\\Seen" not in m["flags"]]
            return "OK", [b" ".join(str(u).encode() for u in sorted(uids))]
        uid = int(args[0])
        if befehl == "FETCH":
            if mails[uid].get("kaputt"):
                return "NO", [None]
            return "OK", [(f"{uid} (UID {uid} BODY[] {{1}}".encode(), mails[uid]["roh"]), b")"]
        if befehl == "STORE":
            mails[uid]["flags"].update(args[2].strip("()").split())
            return "OK", [b""]
        if befehl in ("MOVE", "COPY"):
            ziel = args[1].strip('"')
            self.postfach.setdefault(ziel, {})[uid] = {"roh": mails[uid]["roh"], "flags": set()}
            if befehl == "MOVE":
                del mails[uid]
            return "OK", [b""]
        raise AssertionError(befehl)

    def expunge(self):
        self.expunged += 1
        for u in [u for u, m in self.postfach[self.ordner].items() if "\\Deleted" in m["flags"]]:
            del self.postfach[self.ordner][u]
        return "OK", [b""]

    def logout(self):
        return "BYE", [b""]


def _mail(betreff="Rechnung RE-2026-0815", von="Buchhaltung Schneider <rechnung@schneider-buero.de>",
          anhaenge=(), message_id="<a1@schneider-buero.de>", text="Anbei unsere Rechnung."):
    from email.message import EmailMessage

    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = von, "rechnungen@pyron.de", betreff
    m["Date"] = "Thu, 12 Mar 2026 09:30:00 +0100"
    if message_id:
        m["Message-ID"] = message_id
    m.set_content(text)
    for name, daten, typ in anhaenge:
        haupt, unter = typ.split("/")
        m.add_attachment(daten, maintype=haupt, subtype=unter, filename=name)
    return m.as_bytes()


class MailabrufTests(DmsTestBasis):
    """dms_mailabruf mit gemocktem IMAP: Anhänge → Stapel, Protokoll, Dubletten, Kennzeichnung, Fehler."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.lieferant = Geschaeftspartner.objects.create(name="Bürobedarf Schneider GmbH", ist_lieferant=True,
                                                         ust_id="DE811569869",
                                                         email_rechnung="rechnung@schneider-buero.de")

    def setUp(self):
        from unittest import mock

        FakeImap.postfach = {"INBOX": {}, "ERP/Verarbeitet": {}, "ERP/Fehler": {}}
        FakeImap.instanzen = []
        p = mock.patch("apps.dms.mailabruf.imaplib.IMAP4_SSL", FakeImap)
        p.start()
        self.addCleanup(p.stop)
        self.einstellungen = self.settings(
            ERP_DMS_IMAP_HOST="imap.example.de", ERP_DMS_IMAP_USER="rechnungen@pyron.de",
            ERP_DMS_IMAP_PASSWORD="geheim", ERP_DMS_IMAP_ORDNER="INBOX", ERP_DMS_IMAP_ZIELORDNER="ERP/Verarbeitet",
            ERP_DMS_IMAP_FEHLERORDNER="ERP/Fehler", ERP_DMS_IMAP_ABSENDER=[])
        self.einstellungen.enable()
        self.addCleanup(self.einstellungen.disable)

    def einwerfen(self, roh, uid=None, ordner="INBOX", **extra):
        uid = uid or (max(FakeImap.postfach[ordner], default=0) + 1)
        FakeImap.postfach[ordner][uid] = {"roh": roh, "flags": set(), **extra}
        return uid

    def abruf(self, *args):
        ausgabe = io.StringIO()
        call_command("dms_mailabruf", *args, stdout=ausgabe)
        return ausgabe.getvalue()

    def test_anhaenge_als_stapel_mit_protokoll_und_verschieben(self):
        from .models import MailEingang, Stapel

        self.einwerfen(_mail(anhaenge=[("RE-2026-0815.pdf", T.pdf(), "application/pdf"),
                                       ("xrechnung.xml", T.cii_xml(), "application/xml"),
                                       ("agb.docx", b"PK\x03\x04", "application/octet-stream")]))
        self.einwerfen(_mail(betreff="Newsletter", message_id="<n1@x.de>", von="info@x.de"))
        ausgabe = self.abruf("--ohne-verarbeitung")
        self.assertIn("1 übernommen (2 Dokumente)", ausgabe)
        self.assertIn("1 ohne Anhang", ausgabe)
        mail = MailEingang.objects.get(message_id="<a1@schneider-buero.de>")
        self.assertEqual((mail.status, mail.anhaenge, mail.absender), ("UEBERNOMMEN", 2, "rechnung@schneider-buero.de"))
        self.assertIn("agb.docx", mail.hinweise)
        self.assertEqual(mail.stapel.quelle, Stapel.Quelle.EMAIL)
        docs = list(mail.stapel.dokumente.order_by("pk"))
        self.assertEqual([d.titel for d in docs], ["RE-2026-0815", "xrechnung"])
        self.assertEqual(docs[0].partner, self.lieferant)  # über die Rechnungs-E-Mail-Adresse
        self.assertEqual(docs[0].metadaten["email"]["message_id"], "<a1@schneider-buero.de>")
        eingang = docs[0].protokoll.get(aktion="Eingang").text
        for teil in ("rechnung@schneider-buero.de", "Rechnung RE-2026-0815", "<a1@schneider-buero.de>"):
            self.assertIn(teil, eingang)
        # Kennzeichnung: übernommene Mail im Zielordner, Mail ohne Anhang im Fehlerordner, Posteingang leer
        self.assertEqual(len(FakeImap.postfach["ERP/Verarbeitet"]), 1)
        self.assertEqual(len(FakeImap.postfach["ERP/Fehler"]), 1)
        self.assertEqual(FakeImap.postfach["INBOX"], {})
        self.assertEqual(MailEingang.objects.get(message_id="<n1@x.de>").status, "OHNE_ANHANG")
        self.assertIsNotNone(FakeImap.instanzen[0].ssl_context)  # SSL/TLS mit Zertifikatsprüfung

    def test_dublette_per_message_id_und_datei(self):
        from .models import MailEingang

        pdf = T.pdf()  # einmal erzeugen: PDFs enthalten einen Zeitstempel
        roh = _mail(anhaenge=[("re.pdf", pdf, "application/pdf")])
        self.einwerfen(roh)
        self.abruf("--ohne-verarbeitung")
        # dieselbe Mail erneut (z. B. wieder auf ungelesen gesetzt) und dieselbe Datei in neuer Mail
        self.einwerfen(roh)
        self.einwerfen(_mail(message_id="<a2@schneider-buero.de>", anhaenge=[("kopie.pdf", pdf, "application/pdf")]))
        ausgabe = self.abruf("--ohne-verarbeitung")
        self.assertIn("1 bereits bekannt", ausgabe)
        self.assertEqual(Dokument.objects.count(), 1)
        zweite = MailEingang.objects.get(message_id="<a2@schneider-buero.de>")
        self.assertIn("identisch mit", zweite.hinweise)
        self.assertIsNone(zweite.stapel)
        self.assertEqual(MailEingang.objects.count(), 2)

    def test_ohne_zielordner_nur_gelesen_und_ohne_message_id(self):
        uid = self.einwerfen(_mail(message_id=None, anhaenge=[("scan.tif", T.tiff(), "image/tiff")]))
        with self.settings(ERP_DMS_IMAP_ZIELORDNER=""):
            self.abruf("--ohne-verarbeitung")
        self.assertIn("\\Seen", FakeImap.postfach["INBOX"][uid]["flags"])
        from .models import MailEingang

        self.assertTrue(MailEingang.objects.get().message_id.startswith("<sha256-"))
        # zweiter Lauf findet nichts Ungelesenes mehr
        self.assertIn("0 Mail(s)", self.abruf())

    def test_absenderfilter_und_trockenlauf(self):
        from .models import MailEingang

        self.einwerfen(_mail(von="spam@werbung.de", message_id="<s@w.de>",
                             anhaenge=[("x.pdf", T.pdf(), "application/pdf")]))
        ausgabe = self.abruf("--trocken")
        self.assertIn("spam@werbung.de", ausgabe)
        self.assertFalse(MailEingang.objects.exists())
        self.assertEqual(FakeImap.postfach["INBOX"][1]["flags"], set())
        with self.settings(ERP_DMS_IMAP_ABSENDER=["@schneider-buero.de"]):
            self.assertIn("1 ignoriert", self.abruf())
        self.assertEqual(MailEingang.objects.get().status, "IGNORIERT")
        self.assertFalse(Dokument.objects.exists())

    def test_fehler_robust_und_wiederholung(self):
        from unittest import mock

        from .models import MailEingang

        self.einwerfen(_mail(anhaenge=[("re.pdf", T.pdf(), "application/pdf")]))
        self.einwerfen(b"", kaputt=True)
        with mock.patch("apps.dms.services.dokument_anlegen", side_effect=RuntimeError("Platte voll")):
            ausgabe = self.abruf("--ohne-verarbeitung")
        self.assertIn("Platte voll", ausgabe)
        self.assertIn("nicht geladen", ausgabe)
        m = MailEingang.objects.get()
        self.assertEqual((m.status, m.versuche), ("FEHLER", 1))
        self.assertEqual(len(FakeImap.postfach["INBOX"]), 2)  # bleibt ungelesen → neuer Versuch
        self.assertFalse(Dokument.objects.exists())
        self.abruf("--ohne-verarbeitung")
        m.refresh_from_db()
        self.assertEqual((m.status, m.versuche, m.anhaenge), ("UEBERNOMMEN", 2, 1))
        self.assertEqual(Dokument.objects.count(), 1)

    def test_konfiguration_und_verbindungsfehler(self):
        from django.core.management.base import CommandError

        with self.settings(ERP_DMS_IMAP_HOST=""):
            self.assertIn("deaktiviert", self.abruf())
        with self.settings(ERP_DMS_IMAP_PASSWORD="falsch"), self.assertRaisesMessage(CommandError, "Anmeldung"):
            self.abruf()
        with self.settings(ERP_DMS_IMAP_HOST="unerreichbar"), self.assertRaisesMessage(CommandError, "Verbindung"):
            self.abruf()
        with self.assertRaisesMessage(CommandError, "nicht vorhanden"):
            self.abruf("--ordner", "Gibtsnicht")

    @skipUnless(O.tesseract_verfuegbar(), "Tesseract nicht installiert")
    def test_mit_verarbeitung_erkannt(self):
        self.einwerfen(_mail(anhaenge=[("re.pdf", T.pdf(), "application/pdf")]))
        self.abruf()
        d = Dokument.objects.get()
        self.assertEqual(d.wert("rechnungsnummer"), "RE-2026-0815")
        self.assertIn(d.status, (Dokument.Status.ERKANNT, Dokument.Status.PRUEFEN))


class MailEingangSeiteTests(DmsTestBasis):
    def test_seite_zeigt_protokoll(self):
        from .models import MailEingang

        MailEingang.objects.create(message_id="<x@y>", absender="rechnung@schneider-buero.de", betreff="RE 4711",
                                   status=MailEingang.Status.OHNE_ANHANG)
        self.client.force_login(self.user)
        r = self.client.get(reverse("dms:mails"))
        self.assertContains(r, "RE 4711")
        self.assertContains(r, "nicht eingerichtet")


# ------------------------------------------------------------------ Dreifach-Abgleich Bestellung/WE/Rechnung

class BestellnummerErkennungTests(TestCase):
    """Bestellnummer (BT-13) aus Text, CII und UBL."""

    def test_text_cii_ubl(self):
        r = T.beispiel(bestellnummer="EK-2026-00012")
        self.assertEqual(E.extrahieren("Rechnung 4711\nIhre Bestellung: EK-2026-00012 vom 01.11.2026\n")
                         ["felder"]["bestellnummer"]["wert"], "EK-2026-00012")
        self.assertEqual(E.extrahieren("Bestell-Nr. 4500012345")["felder"]["bestellnummer"]["wert"], "4500012345")
        self.assertNotIn("bestellnummer", E.extrahieren("Bestellung vom 12.03.2026")["felder"])
        for name, daten in (("c.xml", T.cii_xml(r)), ("u.xml", T.ubl_xml(r)), ("z.pdf", T.zugferd_pdf(r))):
            with self.subTest(name=name):
                e = O.analysieren(daten, name)
                self.assertEqual(E.extrahieren(e.text, e.xml)["felder"]["bestellnummer"]["wert"], "EK-2026-00012")


class DreifachAbgleichTests(DmsTestBasis):
    """Prüfung Rechnung ↔ Bestellung ↔ Wareneingang in Prüfmaske, Verbuchung und API."""

    DATUM = date(2026, 11, 10)  # Jan.–Sep. 2026 sind in den Demodaten gesperrt

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from apps.einkauf import services as EK
        from apps.einkauf.tests import bestellung_anlegen

        call_command("erp_demodaten", verbosity=0)
        cls.schmidt = Geschaeftspartner.objects.get(name="Stahlhandel Schmidt GmbH")
        cls.einkauf = get_user_model().objects.get(username="einkauf")
        cls.bestellung = EK.freigeben(bestellung_anlegen(cls.schmidt, [("R-1020", "1000", "0.06"),
                                                                       ("R-1010", "100", "4.80")]), cls.einkauf)

    def setUp(self):
        from apps.lager.models import Lagerort
        from apps.wareneingang import services as WE

        bp = list(self.bestellung.positionen.order_by("position"))
        WE.buchen(self.bestellung, [{"bestellposition": bp[0], "menge": "1000"},
                                    {"bestellposition": bp[1], "menge": "100", "chargennummer": "C-77"}],
                  lieferschein_nr="LS-4711", lagerort=Lagerort.objects.get(code="WE"),
                  benutzer=get_user_model().objects.get(username="lager"))
        self.bestellung.refresh_from_db()

    def rechnung(self, preis_schraube="0.06", menge_schraube="1000", bestellnummer=None, fracht=True, nr="SH-77"):
        positionen = [
            {"menge": Decimal(menge_schraube), "einheit": "Stk", "text": "R-1020 Sechskantschraube M8x20",
             "preis": Decimal(preis_schraube), "satz": 19},
            {"menge": Decimal("100"), "einheit": "kg", "text": "R-1010 Rundstahl S235", "preis": Decimal("4.80"),
             "satz": 19}]
        if fracht:
            positionen.append({"menge": Decimal("1"), "einheit": "pauschal", "text": "Frachtpauschale",
                               "preis": Decimal("15.00"), "satz": 19})
        r = T.beispiel(lieferant=self.schmidt.name, ust_id="DE119876543", iban="DE75512108001245126199",
                       rechnungsnummer=nr, datum=self.DATUM, positionen=positionen,
                       bestellnummer=self.bestellung.nummer if bestellnummer is None else bestellnummer)
        d = S.dokument_anlegen(T.pdf(r), "re.pdf", art=Dokumentart.EINGANGSRECHNUNG, benutzer=self.user)
        V.verarbeiten(d, self.user)
        d.refresh_from_db()
        return d

    def test_automatisch_ohne_abweichung_und_verbuchung(self):
        from . import verbuchung as VB

        d = self.rechnung()
        a = d.erkennung["abgleich"]
        self.assertEqual((a["bestellung_id"], a["quelle"]), (self.bestellung.pk, "Bestellnummer auf der Rechnung"))
        self.assertFalse(a["abweichung"], a)
        self.assertEqual([z["zuordnung"] for z in a["positionen"]], ["Artikelnummer", "Artikelnummer"])
        self.assertEqual([p["beschreibung"] for p in a["ohne_bezug"]], ["Frachtpauschale"])
        er = VB.verbuchen(d, self.user)
        self.assertEqual(er.bestellung, self.bestellung)
        self.assertTrue(er.abweichung_akzeptiert)
        pos = list(er.positionen.order_by("position"))
        self.assertEqual([p.bestellposition_id is not None for p in pos], [True, True, False])
        self.assertEqual(pos[0].artikel.artikelnummer, "R-1020")
        self.assertIsNotNone(pos[2].konto)  # Fracht über das Aufwandskonto
        self.assertIn("Bestellbezug", d.protokoll.filter(aktion="Verbucht").get().text)
        # Zweite Rechnung über dieselbe Lieferung → Menge bereits berechnet
        d2 = self.rechnung(nr="SH-78", fracht=False)
        arten = {a["art"] for z in d2.erkennung["abgleich"]["positionen"] for a in z["abweichungen"]}
        self.assertEqual(arten, {"MENGE"})

    def test_preisabweichung_nur_mit_begruendung_und_recht(self):
        from . import verbuchung as VB

        d = self.rechnung(preis_schraube="0.08")  # +33 %, +20,00 € auf die Position
        a = d.erkennung["abgleich"]
        self.assertTrue(a["abweichung"])
        self.assertEqual(a["positionen"][0]["abweichungen"][0]["art"], "PREIS")
        self.assertTrue(any("Dreifach-Abgleich" in m["text"] for m in d.erkennung["plausibilitaet"]))
        with self.assertRaisesMessage(S.DmsFehler, "Begründung"):
            VB.verbuchen(d, self.user)
        with self.assertRaisesMessage(S.DmsFehler, "mindestens 10 Zeichen"):
            VB.verbuchen(d, self.user, begruendung="ok")
        lager = get_user_model().objects.get(username="lager")  # ohne Freigaberecht Einkauf/Fibu
        with self.assertRaisesMessage(S.DmsFehler, "Recht"):
            VB.verbuchen(d, lager, begruendung="Preiserhöhung telefonisch bestätigt")
        er = VB.verbuchen(d, self.einkauf, begruendung="Preiserhöhung telefonisch bestätigt")
        self.assertEqual(er.bestellung, self.bestellung)
        self.assertIn("telefonisch", d.protokoll.get(aktion="Abweichung freigegeben").text)
        d.refresh_from_db()
        self.assertEqual(d.erkennung["abgleich"]["freigabe"]["von"], "einkauf")

    @override_settings(ERP_DMS_TOLERANZ_PREIS_PROZENT="50", ERP_DMS_TOLERANZ_BETRAG="0.50")
    def test_toleranz_konfigurierbar(self):
        self.assertFalse(self.rechnung(preis_schraube="0.08").erkennung["abgleich"]["abweichung"])

    def test_kleinbetrag_innerhalb_betragstoleranz(self):
        # 0,0604 € statt 0,06 € → +0,7 %, 0,40 € auf die Position: innerhalb der Toleranz
        self.assertFalse(self.rechnung(preis_schraube="0.0604").erkennung["abgleich"]["abweichung"])

    def test_ohne_wareneingang(self):
        from apps.einkauf import services as EK
        from apps.einkauf.tests import bestellung_anlegen

        b = EK.freigeben(bestellung_anlegen(self.schmidt, [("R-1020", "500", "0.06")]), self.einkauf)
        d = self.rechnung(bestellnummer=b.nummer, fracht=False)
        a = d.erkennung["abgleich"]
        self.assertEqual(a["bestellung_id"], b.pk)
        self.assertEqual(a["positionen"][0]["abweichungen"][0]["art"], "KEIN_WE")

    def test_manuelle_auswahl_in_pruefmaske(self):
        d = self.rechnung(bestellnummer="")
        self.assertIsNone(d.erkennung["abgleich"]["bestellung_id"])
        self.assertIn(self.bestellung.pk, [v["id"] for v in d.erkennung["abgleich"]["vorschlaege"]])
        self.client.force_login(self.user)
        r = self.client.get(reverse("dms:pruefen", args=[d.pk]))
        self.assertContains(r, "Dreifach-Abgleich")
        self.assertContains(r, self.bestellung.nummer)
        r = self.client.post(reverse("dms:pruefen", args=[d.pk]),
                             PruefmaskeTests.post_daten(self, d, bestellung=str(self.bestellung.pk)))
        self.assertEqual(r.status_code, 302)
        d.refresh_from_db()
        a = d.erkennung["abgleich"]
        self.assertEqual((a["auswahl"], a["bestellung_id"], a["abweichung"]), ("manuell", self.bestellung.pk, False))
        self.assertContains(self.client.get(reverse("dms:pruefen", args=[d.pk])), "R-1020")
        # „ohne Bestellbezug“ → klassische Verbuchung über Aufwandskonten
        self.client.post(reverse("dms:pruefen", args=[d.pk]), PruefmaskeTests.post_daten(self, d, bestellung="ohne"))
        d.refresh_from_db()
        self.assertEqual((d.erkennung["abgleich"]["auswahl"], d.erkennung["abgleich"]["bestellung_id"]),
                         ("ohne", None))

    def test_verbuchen_in_pruefmaske_mit_begruendung(self):
        d = self.rechnung(preis_schraube="0.08")
        self.client.force_login(self.user)
        daten = PruefmaskeTests.post_daten(self, d)
        daten["aktion"] = "verbuchen"
        self.client.post(reverse("dms:pruefen", args=[d.pk]), daten)
        d.refresh_from_db()
        self.assertIsNone(d.eingangsrechnung)
        daten["begruendung"] = "Preis laut Rahmenvertrag 2027 angepasst"
        r = self.client.post(reverse("dms:pruefen", args=[d.pk]), daten)
        d.refresh_from_db()
        self.assertIsNotNone(d.eingangsrechnung)
        self.assertRedirects(r, reverse("fibu:er", args=[d.eingangsrechnung.pk]))

    def test_api_verbuchen_mit_bestellbezug_und_begruendung(self):
        from rest_framework.authtoken.models import Token
        from rest_framework.test import APIClient

        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=self.user).key}")
        d = self.rechnung(bestellnummer="", preis_schraube="0.08")
        url = f"/api/v1/dms/dokumente/{d.pk}/verbuchen/"
        self.assertEqual(api.post(url, {"bestellung": "999999"}, format="json").status_code, 400)
        r = api.post(url, {"bestellung": str(self.bestellung.pk)}, format="json")
        self.assertEqual(r.status_code, 409, r.content)
        r = api.post(url, {"bestellung": str(self.bestellung.pk), "begruendung": "Mehrpreis Sonderverpackung ok"},
                     format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["dokument"]["erkennung"]["abgleich"]["bestellnummer"], self.bestellung.nummer)

    def test_demodaten_bestellrechnung(self):
        call_command("dms_demodaten", ohne_ocr=True, verbosity=0)
        d = Dokument.objects.get(versionen__dateiname="schmidt_rechnung_bestellung.pdf")
        V.verarbeiten(d, self.user)
        d.refresh_from_db()
        a = d.erkennung["abgleich"]
        self.assertTrue(a["bestellnummer"].startswith("EK-"))
        self.assertTrue(a["abweichung"], (a, d.erkennung["positionen"]))
        self.assertEqual([x["art"] for z in a["positionen"] for x in z["abweichungen"]], ["PREIS"])
        self.assertEqual([p["beschreibung"] for p in a["ohne_bezug"]], ["Frachtpauschale"])


# ------------------------------------------------------------------ Skonto (Punkt k)

class SkontoErkennungTests(TestCase):
    def test_text_varianten(self):
        from . import erkennung as E

        sk = E.skonto_aus_text("14 Tage 2 % Skonto, 30 Tage netto")
        self.assertEqual((sk["prozent"], sk["tage"]), (Decimal("2"), 14))
        sk = E.skonto_aus_text("Bei Zahlung bis 24.03.2026 abzüglich 3 % Skonto")
        self.assertEqual((sk["prozent"], sk["bis"]), (Decimal("3"), date(2026, 3, 24)))
        sk = E.skonto_aus_text("#SKONTO#TAGE=10#PROZENT=2.50#")
        self.assertEqual((sk["prozent"], sk["tage"]), (Decimal("2.50"), 10))
        self.assertIsNone(E.skonto_aus_text("Zahlbar innerhalb von 30 Tagen netto"))

    def _pruefe(self, erg, brutto):
        from . import erkennung as E

        f = erg["felder"]
        self.assertEqual(E.feldwert(f, "skonto_prozent", "betrag"), Decimal("2"))
        self.assertEqual(E.feldwert(f, "skonto_bis", "datum"), date(2026, 3, 26))
        self.assertEqual(E.feldwert(f, "skonto_betrag", "betrag"), (brutto * 2 / 100).quantize(Decimal("0.01")))
        self.assertEqual(E.feldwert(f, "faellig_am", "datum"), date(2026, 4, 11))

    def test_pdf_cii_ubl(self):
        from . import erkennung as E

        r = T.beispiel(skonto={"prozent": 2, "tage": 14})
        self._pruefe(E.extrahieren(xml=T.cii_xml(r)), r["brutto"])
        self._pruefe(E.extrahieren(xml=T.ubl_xml(r)), r["brutto"])
        text = "\n".join(" ".join(t for _, t, _, _ in felder) for _, felder in T.zeilen(r))
        self._pruefe(E.extrahieren(text=text), r["brutto"])

    def test_ohne_skonto_keine_felder(self):
        from . import erkennung as E

        erg = E.extrahieren(xml=T.cii_xml(T.beispiel()))
        self.assertIsNone(E.feldwert(erg["felder"], "skonto_prozent", "betrag"))


class SkontoVerbuchungTests(DmsTestBasis):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        from apps.fibu import services as F

        F.geschaeftsjahr_anlegen(2026)
        cls.lieferant = Geschaeftspartner.objects.create(name="Bürobedarf Schneider GmbH", ist_lieferant=True,
                                                         ust_id="DE811569869")

    def test_skonto_in_er_op_und_skontobuchung(self):
        from apps.fibu import services as F

        from . import verbuchung as VB

        r = T.beispiel(skonto={"prozent": 2, "tage": 14})
        d = S.dokument_anlegen(T.zugferd_pdf(r), "re.pdf", art=Dokumentart.EINGANGSRECHNUNG, benutzer=self.user)
        V.verarbeiten(d, self.user)
        er = VB.verbuchen(d, self.user)
        self.assertEqual((er.skonto_prozent, er.skonto_bis, er.skontobetrag),
                         (Decimal("2.00"), date(2026, 3, 26), Decimal("6.59")))
        op = er.offene_posten.get()
        self.assertEqual((op.skonto_prozent, op.skonto_bis, op.skontobetrag),
                         (Decimal("2.00"), date(2026, 3, 26), Decimal("6.59")))
        self.assertEqual(op.skonto_betrag(date(2026, 3, 20)), Decimal("6.59"))
        self.assertEqual(op.skonto_betrag(date(2026, 4, 1)), Decimal("0"))
        # Zahlung innerhalb der Frist mit Skontoabzug → Skontoertrag + Vorsteuerberichtigung (SKR03)
        bank = F.konto("1200")
        buchung = F.op_ausgleichen([op], Decimal("322.98"), date(2026, 3, 20), bank, self.user, skonto=True)
        op.refresh_from_db()
        self.assertEqual(op.offen, Decimal("0"))
        salden = {}
        for z in buchung.zeilen.select_related("konto"):
            salden[z.konto.nummer] = salden.get(z.konto.nummer, 0) + z.soll - z.haben
        self.assertLess(salden["3736"], 0)
        self.assertLess(salden["1576"], 0)
        self.assertEqual(salden["1200"], Decimal("-322.98"))
        # Skontoerträge (19 %/7 %) und Vorsteuerkorrekturen ergeben zusammen den Skontobetrag
        self.assertEqual(sum(v for k, v in salden.items() if v < 0 and k != "1200"), Decimal("-6.59"))


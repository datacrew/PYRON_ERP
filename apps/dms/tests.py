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
        self._datei("rechnung.pdf", T.pdf())
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
        self._datei("rechnung_kopie.pdf", T.pdf())
        call_command("dms_eingang", ordner=self.ordner, stdout=io.StringIO())
        self.assertTrue(os.path.exists(os.path.join(self.ordner, "doppelt", "rechnung_kopie.pdf")))
        self.assertEqual(Dokument.objects.count(), 2)

    def test_fehlender_ordner(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("dms_eingang", ordner=os.path.join(self.ordner, "gibtsnicht"))

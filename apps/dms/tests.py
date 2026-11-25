"""Tests DMS – Fundament: Ablage, SHA-256, Versionierung, GoBD-Schutz (Code und Trigger), Suche, Rechte."""
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

"""Demodaten für das Dokumentenmanagement (idempotent). NICHT im Produktivsystem ausführen!

Legt einen Lieferanten „Bürobedarf Schneider GmbH“ und einen Demo-Stapel mit synthetischen Eingangsrechnungen an:
- ZUGFeRD-Rechnung (XML wird direkt gelesen) → erkannt, freigegeben und in die Fibu verbucht
- digitale PDF-Rechnung (Textlayer) → erkannt, wartet auf Prüfung/Freigabe
- gescannte Rechnung (Bild-PDF, Texterkennung per Tesseract) → je nach OCR-Ergebnis erkannt bzw. „zu prüfen“
- Sammel-PDF mit Trennblatt → wird automatisch in zwei Dokumente geteilt
- Vertrag (Sonstiges Dokument mit Schlagwort)
- E-Mail-Eingang: eine simulierte Lieferantenmail mit PDF-Anhang (ohne IMAP-Verbindung) → eigener Stapel
Alle Rechnungen sind erzeugte Beispiele (apps/dms/testdaten.py), keine echten Belege.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.dms import services as S
from apps.dms import testdaten as T
from apps.dms import trennung as TR
from apps.dms import verarbeitung as V
from apps.dms import verbuchung as VB
from apps.dms.models import Dokument, Dokumentart, Stapel, Tag

D = Decimal
STAPEL_NAME = "Demodaten Dokumentenmanagement"


class Command(BaseCommand):
    help = "Legt Demodaten für das DMS an (Lieferant, Beispielrechnungen, Stapel, Verbuchung)."

    def add_arguments(self, parser):
        parser.add_argument("--ohne-ocr", action="store_true",
                            help="Keine Texterkennung ausführen (Dokumente bleiben in der Warteschlange)")

    def handle(self, *args, **o):
        if Stapel.objects.filter(bezeichnung=STAPEL_NAME).exists():
            self.stdout.write("DMS-Demodaten sind bereits vorhanden – übersprungen.")
            return
        admin = get_user_model().objects.filter(is_superuser=True).order_by("pk").first()
        schneider = self._lieferant("Bürobedarf Schneider GmbH", "DE811569869", "DE89370400440532013000")
        heute = timezone.localdate()
        datum = heute - timedelta(days=5)

        stapel = S.stapel_anlegen(bezeichnung=STAPEL_NAME, benutzer=admin)
        zugferd = T.beispiel(rechnungsnummer=f"RE-{heute.year}-0815", datum=datum - timedelta(days=10))
        digital = T.beispiel(rechnungsnummer=f"RE-{heute.year}-0901", datum=datum, positionen=[
            {"menge": D("4"), "einheit": "Stk", "text": "Ordner breit blau", "preis": D("3.20"), "satz": 19},
            {"menge": D("1"), "einheit": "Stk", "text": "Schreibtischlampe LED", "preis": D("49.90"), "satz": 19}])
        scan = T.beispiel(rechnungsnummer=f"RE-{heute.year}-0933", datum=datum, positionen=[
            {"menge": D("20"), "einheit": "Stk", "text": "Kopierpapier A4 80 g", "preis": D("4.50"), "satz": 19}])
        teil1 = T.beispiel(rechnungsnummer=f"RE-{heute.year}-0950", datum=datum)
        teil2 = T.beispiel(rechnungsnummer=f"RE-{heute.year}-0951", datum=datum, positionen=[
            {"menge": D("3"), "einheit": "kg", "text": "Kaffeebohnen Crema", "preis": D("12.00"), "satz": 7}])

        dokumente = [
            S.dokument_anlegen(T.zugferd_pdf(zugferd), "schneider_zugferd.pdf", art=Dokumentart.EINGANGSRECHNUNG,
                               stapel=stapel, benutzer=admin),
            S.dokument_anlegen(T.pdf(digital), "schneider_rechnung.pdf", art=Dokumentart.EINGANGSRECHNUNG,
                               stapel=stapel, benutzer=admin),
            S.dokument_anlegen(T.scan_pdf(scan, dpi=200), "scan_rechnung.pdf", art=Dokumentart.EINGANGSRECHNUNG,
                               stapel=stapel, benutzer=admin),
            S.dokument_anlegen(T.sammel_pdf(T.pdf(teil1), TR.trennblatt_pdf(), T.pdf(teil2)), "stapelscan.pdf",
                               art=Dokumentart.EINGANGSRECHNUNG, stapel=stapel, benutzer=admin),
        ]
        vertrag = S.dokument_anlegen(self._vertrag_pdf(), "rahmenvertrag_schneider.pdf", art=Dokumentart.VERTRAG,
                                     titel="Rahmenvertrag Büromaterial", stapel=stapel, partner=schneider,
                                     benutzer=admin)
        tag, _ = Tag.objects.get_or_create(name="Rahmenvertrag", defaults={"farbe": "info"})
        vertrag.tags.add(tag)

        mail_stapel = self._demo_mail(T.beispiel(rechnungsnummer=f"RE-{heute.year}-0960", datum=datum, positionen=[
            {"menge": D("2"), "einheit": "Pkg", "text": "Haftnotizen gelb", "preis": D("6.40"), "satz": 19}]))

        if o["ohne_ocr"]:
            self.stdout.write(self.style.SUCCESS(f"DMS-Demodaten: Stapel {stapel.nummer} angelegt (ohne OCR)."))
            return
        ok, fehler = V.warteschlange_abarbeiten(stapel=stapel, benutzer=admin)
        if mail_stapel:
            V.warteschlange_abarbeiten(stapel=mail_stapel, benutzer=admin)
        gebucht = ""
        erste = Dokument.objects.get(pk=dokumente[0].pk)
        if erste.status == Dokument.Status.ERKANNT:
            try:
                er = VB.verbuchen(erste, admin)
                gebucht = f", {erste.nummer} verbucht als {er.nummer}"
            except S.DmsFehler as exc:
                self.stdout.write(self.style.WARNING(f"  Verbuchung der Demo-Rechnung nicht möglich: {exc}"))
        self.stdout.write(self.style.SUCCESS(
            f"DMS-Demodaten: Stapel {stapel.nummer}, verarbeitet {ok}, Fehler {fehler}{gebucht}."))

    def _lieferant(self, name, ust_id, iban):
        from apps.stammdaten.models import Adresse, Geschaeftspartner, Zahlungsbedingung

        p = Geschaeftspartner.objects.filter(ust_id=ust_id).first()
        if p:
            return p
        p = Geschaeftspartner.objects.create(
            name=name, ist_lieferant=True, ust_id=ust_id, iban=iban, email="rechnung@schneider-buero.example",
            zahlungsbedingung=Zahlungsbedingung.objects.filter(code="30N").first())
        Adresse.objects.create(partner=p, typ="HAUPT", strasse="Industriestraße 12", plz="90402", ort="Nürnberg")
        return p

    @staticmethod
    def _demo_mail(rechnung):
        """Simulierter E-Mail-Eingang (gleiche Logik wie dms_mailabruf, nur ohne Postfach)."""
        from email.message import EmailMessage

        from apps.dms import mailabruf as M

        m = EmailMessage()
        m["From"] = "Buchhaltung Schneider <rechnung@schneider-buero.example>"
        m["To"], m["Subject"] = "rechnungen@pyron.example", f"Ihre Rechnung {rechnung['rechnungsnummer']}"
        m["Message-ID"] = f"<demo-{rechnung['rechnungsnummer']}@schneider-buero.example>"
        m.set_content("Sehr geehrte Damen und Herren,\nanbei erhalten Sie unsere Rechnung.\n(Demodaten)")
        m.add_attachment(T.pdf(rechnung), maintype="application", subtype="pdf",
                         filename=f"{rechnung['rechnungsnummer']}.pdf")
        eintrag, _ = M.mail_uebernehmen(m.as_bytes(), {"absender": [], "art": Dokumentart.EINGANGSRECHNUNG},
                                        "Demo-Postfach")
        return eintrag.stapel

    @staticmethod
    def _vertrag_pdf():
        import pymupdf

        doc = pymupdf.open()
        seite = doc.new_page()
        text = ("Rahmenvertrag über die Lieferung von Büromaterial\n\n"
                "zwischen Bürobedarf Schneider GmbH, Industriestraße 12, 90402 Nürnberg\n"
                "und PYRON Musterfirma GmbH, Hauptstraße 1, 80331 München\n\n"
                "§ 1 Gegenstand: Lieferung von Papier, Toner und Büroartikeln auf Abruf.\n"
                "§ 2 Laufzeit: 24 Monate, Verlängerung um jeweils 12 Monate.\n"
                "§ 3 Zahlung: 30 Tage netto nach Rechnungseingang.\n\n(Beispieldokument – Demodaten)")
        seite.insert_text((60, 80), text, fontsize=11)
        daten = doc.tobytes()
        doc.close()
        return daten

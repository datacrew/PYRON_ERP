"""Trennung mehrseitiger Sammel-PDFs (Stapelscan) in Einzeldokumente.

Automatisch an Trennblättern: Seite mit Barcode „PYRON-TRENNBLATT“ (Code 128, gelesen mit zbar) oder mit dem
Textlayer-Wort „TRENNBLATT“. Trennblätter selbst werden verworfen. Manuell über Seitenbereiche („1-2; 3; 4-6“).
Das Sammel-PDF bleibt als Original unverändert erhalten (GoBD), die Teile verweisen per `eltern` darauf.
"""
import io
import re
from pathlib import Path

from django.db import transaction

from . import services as S
from .models import Dokument
from .testdaten import SCHRIFT, SCHRIFT_FETT, TRENNBLATT_CODE

TRENNWORT = "TRENNBLATT"


def zbar_verfuegbar():
    try:
        from pyzbar import pyzbar  # noqa: F401
    except (ImportError, OSError):
        return False
    return True


def trennblatt_pdf():
    """Druckvorlage Trennblatt (A4) mit Barcode und Klartext."""
    import barcode
    from barcode.writer import ImageWriter
    from fpdf import FPDF

    png = io.BytesIO()
    barcode.Code128(TRENNBLATT_CODE, writer=ImageWriter()).write(
        png, options={"module_width": 0.5, "module_height": 25, "quiet_zone": 6, "write_text": False, "dpi": 300})
    png.seek(0)
    doc = FPDF(format="A4", unit="mm")
    doc.add_font("DejaVu", "", SCHRIFT)
    doc.add_font("DejaVu", "B", SCHRIFT_FETT)
    doc.add_page()
    doc.set_font("DejaVu", "B", 40)
    doc.cell(0, 40, TRENNWORT, align="C", new_x="LMARGIN", new_y="NEXT")
    doc.image(png, x=35, y=70, w=140)
    doc.set_xy(10, 130)
    doc.set_font("DejaVu", "", 12)
    doc.multi_cell(0, 7, "PYRON ERP – Dokumentenmanagement\n\nDieses Blatt vor jedes Dokument eines Stapelscans legen. "
                         "Das System teilt das Sammel-PDF an dieser Stelle und verwirft das Trennblatt.",
                   align="C")
    return bytes(doc.output())


def _ist_trennblatt(seite, mit_barcode):
    text = seite.get_text() or ""
    if TRENNWORT in text.upper().replace(" ", "") and len(text) < 600:
        return True
    if mit_barcode and len(text.strip()) < 600:
        from PIL import Image
        from pyzbar import pyzbar

        bild = Image.open(io.BytesIO(seite.get_pixmap(dpi=150, colorspace="gray").tobytes("png")))
        return any(c.data.decode("latin-1") == TRENNBLATT_CODE for c in pyzbar.decode(bild))
    return False


def trennseiten(pdf_daten):
    """0-basierte Indizes der Trennblätter im PDF."""
    import pymupdf

    mit_barcode = zbar_verfuegbar()
    with pymupdf.open(stream=pdf_daten, filetype="pdf") as doc:
        return [i for i, seite in enumerate(doc) if _ist_trennblatt(seite, mit_barcode)]


def bereiche_aus_trennern(seitenzahl, trenner):
    """1-basierte Seitenbereiche [(von, bis)] zwischen den Trennblättern (0-basierte Indizes); leere
    Abschnitte (z. B. Trennblatt als erste Seite) entfallen."""
    trennseiten_1 = {t + 1 for t in trenner}
    bereiche, start = [], None
    for seite in range(1, seitenzahl + 1):
        if seite in trennseiten_1:
            if start:
                bereiche.append((start, seite - 1))
            start = None
        elif start is None:
            start = seite
    if start:
        bereiche.append((start, seitenzahl))
    return bereiche


def bereiche_parsen(eingabe, seitenzahl):
    """„1-2; 3; 4-6“ → [(1, 2), (3, 3), (4, 6)] mit Prüfung auf Lücken-/Überschneidungsfreiheit."""
    bereiche = []
    for teil in re.split(r"[;,\s]+", (eingabe or "").strip()):
        if not teil:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", teil)
        if not m:
            raise S.DmsFehler(f"Ungültiger Seitenbereich „{teil}“ (Beispiel: 1-2; 3; 4-6).")
        von, bis = int(m.group(1)), int(m.group(2) or m.group(1))
        if not 1 <= von <= bis <= seitenzahl:
            raise S.DmsFehler(f"Seitenbereich {teil} liegt außerhalb von 1–{seitenzahl}.")
        bereiche.append((von, bis))
    if len(bereiche) < 2:
        raise S.DmsFehler("Für eine Trennung mindestens zwei Seitenbereiche angeben.")
    seiten = [s for von, bis in bereiche for s in range(von, bis + 1)]
    if len(seiten) != len(set(seiten)):
        raise S.DmsFehler("Seitenbereiche überschneiden sich.")
    return sorted(bereiche)


def seiten_extrahieren(pdf_daten, von, bis):
    from pypdf import PdfReader, PdfWriter

    leser, schreiber = PdfReader(io.BytesIO(pdf_daten)), PdfWriter()
    for i in range(von - 1, bis):
        schreiber.add_page(leser.pages[i])
    puffer = io.BytesIO()
    schreiber.write(puffer)
    return puffer.getvalue()


def aufteilen(dokument, bereiche, benutzer=None, grund="manuell"):
    """Legt je Seitenbereich ein Einzeldokument (Status NEU) an; das Sammel-PDF wird „abgelegt“."""
    if dokument.archiviert:
        raise S.DmsFehler("Archivierte Dokumente können nicht mehr aufgeteilt werden.")
    if dokument.aufgeteilt or dokument.teile.exists():
        raise S.DmsFehler(f"{dokument.nummer} wurde bereits aufgeteilt.")
    if dokument.eingangsrechnung_id:
        raise S.DmsFehler("Bereits verbuchte Dokumente können nicht aufgeteilt werden.")
    original = dokument.original
    if not original or not original.dateiname.lower().endswith(".pdf"):
        raise S.DmsFehler("Nur PDF-Dokumente können aufgeteilt werden.")
    with original.datei.open("rb") as f:
        daten = f.read()
    stamm = Path(original.dateiname).stem
    teile = []
    with transaction.atomic():
        for von, bis in bereiche:
            teile.append(S.dokument_anlegen(
                seiten_extrahieren(daten, von, bis), f"{stamm}_S{von}-{bis}.pdf", art=dokument.art,
                titel=f"{dokument.titel} (S. {von}–{bis})", stapel=dokument.stapel, partner=dokument.partner,
                benutzer=benutzer, eltern=dokument, seiten_von=von, seiten_bis=bis,
                quelle_text=f"Aus Sammel-PDF {dokument.nummer}"))
        Dokument.objects.filter(pk=dokument.pk).update(aufgeteilt=True, status=Dokument.Status.ABGELEGT,
                                                       fehlermeldung="")
        S.protokollieren(dokument, "Aufgeteilt", f"{grund}: {len(teile)} Einzeldokumente "
                         + ", ".join(f"{t.nummer} (S. {t.seiten_von}–{t.seiten_bis})" for t in teile), benutzer)
    dokument.refresh_from_db()
    return teile


def automatisch_aufteilen(dokument, pdf_daten, seitenzahl, benutzer=None):
    """Teilt an Trennblättern, falls vorhanden. Gibt die Teile zurück (leer = keine Trennung)."""
    if seitenzahl < 2 or dokument.eltern_id or dokument.aufgeteilt:
        return []
    trenner = trennseiten(pdf_daten)
    if not trenner:
        return []
    bereiche = bereiche_aus_trennern(seitenzahl, trenner)
    if not bereiche:
        return []
    seiten = ", ".join(str(t + 1) for t in trenner)
    return aufteilen(dokument, bereiche, benutzer, grund=f"Trennblätter auf Seite(n) {seiten}")

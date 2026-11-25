"""Texterkennung des DMS.

Reihenfolge (die günstigste verlässliche Quelle gewinnt):
1. Reine XML-Datei (XRechnung UBL/CII) → strukturierte Daten, kein OCR.
2. PDF mit eingebetteter ZUGFeRD-/Factur-X-/XRechnung-XML → strukturierte Daten, Text aus dem Textlayer.
3. Digitales PDF mit Textlayer → Text direkt auslesen (PyMuPDF), kein OCR.
4. Scan (PDF ohne Text, PNG/JPG/TIFF) → OCRmyPDF (Tesseract „deu“) erzeugt ein durchsuchbares PDF/A;
   steht OCRmyPDF nicht zur Verfügung, liest pytesseract nur den Text (ohne PDF/A).

Die Funktion `analysieren()` ist rein (keine Datenbank) und damit isoliert testbar.
"""
import io
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field

from django.conf import settings

log = logging.getLogger(__name__)
# factur-x warnt bei jedem PDF ohne E-Rechnung – das ist im DMS der Normalfall
logging.getLogger("factur-x").setLevel(logging.ERROR)

BILD_ENDUNGEN = (".png", ".jpg", ".jpeg", ".tif", ".tiff")
# Ab so vielen Zeichen je Seite (Durchschnitt) gilt ein PDF als „digital“ (Textlayer ausreichend)
MIN_ZEICHEN_JE_SEITE = 40


class OcrFehler(Exception):
    pass


@dataclass
class Ergebnis:
    text: str = ""
    quelle: str = ""          # xrechnung, zugferd, textlayer, ocr, ocr-einfach
    seiten: int = 0
    xml: bytes | None = None  # strukturierte E-Rechnung (falls vorhanden)
    xml_name: str = ""
    pdfa: bytes | None = None  # durchsuchbares PDF/A (nur nach OCR)
    hinweise: list = field(default_factory=list)


# ------------------------------------------------------------------ Verfügbarkeit

def tesseract_verfuegbar():
    return bool(shutil.which("tesseract"))


def ocrmypdf_verfuegbar():
    try:
        import ocrmypdf  # noqa: F401
    except ImportError:
        return False
    return tesseract_verfuegbar() and bool(shutil.which("gs"))


def sprache():
    return getattr(settings, "ERP_DMS_OCR_SPRACHE", "deu")


# ------------------------------------------------------------------ XML

def xml_art(daten):
    """'CII' (ZUGFeRD/Factur-X/XRechnung-CII), 'UBL' (XRechnung-UBL) oder '' (keine E-Rechnung)."""
    from lxml import etree

    try:
        wurzel = etree.fromstring(daten, parser=etree.XMLParser(resolve_entities=False, no_network=True))
    except (etree.XMLSyntaxError, ValueError):
        return ""
    name = etree.QName(wurzel).localname
    if name == "CrossIndustryInvoice":
        return "CII"
    if name in ("Invoice", "CreditNote"):
        return "UBL"
    return ""


def xml_text(daten):
    """Lesbarer Volltext aus einer XML-Datei (für die Suche)."""
    from lxml import etree

    try:
        wurzel = etree.fromstring(daten, parser=etree.XMLParser(resolve_entities=False, no_network=True))
    except (etree.XMLSyntaxError, ValueError):
        return daten.decode("utf-8", "replace")
    return "\n".join(t.strip() for t in wurzel.itertext() if t and t.strip())


def eingebettete_xml(pdf_daten):
    """(dateiname, xml) einer eingebetteten ZUGFeRD/Factur-X/XRechnung, sonst (None, None)."""
    try:
        from facturx import get_xml_from_pdf

        name, xml = get_xml_from_pdf(pdf_daten, check_xsd=False)
        if xml and xml_art(xml):
            return name, xml
    except Exception as exc:  # noqa: BLE001 – defekte Anhänge dürfen die Verarbeitung nicht stoppen
        log.debug("Factur-X-Auslesen fehlgeschlagen: %s", exc)
    # Fallback: beliebige XML-Anhänge (z. B. XRechnung in PDF ohne Factur-X-Metadaten)
    try:
        import pikepdf

        with pikepdf.open(io.BytesIO(pdf_daten)) as pdf:
            for name, anhang in pdf.attachments.items():
                if name.lower().endswith(".xml"):
                    xml = anhang.get_file().read_bytes()
                    if xml_art(xml):
                        return name, xml
    except Exception as exc:  # noqa: BLE001
        log.debug("PDF-Anhänge nicht lesbar: %s", exc)
    return None, None


# ------------------------------------------------------------------ PDF / Bilder

def seitentext(seite):
    """Text einer PDF-Seite zeilengetreu: Wörter mit gleicher Grundlinie werden zu einer Zeile verbunden,
    größere Lücken (Tabellenspalten) als drei Leerzeichen dargestellt – wichtig für die Positionserkennung."""
    woerter = sorted(seite.get_text("words"), key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    zeilen, aktuell, mitte = [], [], None
    for w in woerter:
        m, hoehe = (w[1] + w[3]) / 2, max(w[3] - w[1], 1)
        if aktuell and abs(m - mitte) > hoehe * 0.5:
            zeilen.append(aktuell)
            aktuell = []
        if not aktuell:
            mitte = m
        aktuell.append(w)
    if aktuell:
        zeilen.append(aktuell)
    ausgabe = []
    for zeile in zeilen:
        zeile.sort(key=lambda w: w[0])
        teile, ende = [], None
        for w in zeile:
            zeichenbreite = (w[2] - w[0]) / max(len(w[4]), 1)
            if ende is not None:
                teile.append("   " if w[0] - ende > zeichenbreite * 2.5 else " ")
            teile.append(w[4])
            ende = w[2]
        ausgabe.append("".join(teile))
    return "\n".join(ausgabe)


def pdf_text(pdf_daten):
    """(text, seitenzahl) aus dem Textlayer eines PDFs; Seiten durch Seitenvorschub (\\f) getrennt."""
    import pymupdf

    try:
        with pymupdf.open(stream=pdf_daten, filetype="pdf") as doc:
            return "\n\f\n".join(seitentext(seite) for seite in doc).strip(), doc.page_count
    except Exception as exc:  # noqa: BLE001
        raise OcrFehler(f"PDF nicht lesbar: {exc}") from exc


def bild_zu_pdf(bild_daten):
    """Bild (auch mehrseitiges TIFF) → PDF (300 dpi, falls keine Auflösung hinterlegt)."""
    from PIL import Image, ImageSequence

    try:
        bild = Image.open(io.BytesIO(bild_daten))
        dpi = bild.info.get("dpi", (300, 300))[0] or 300
        seiten = [s.convert("RGB") if s.mode not in ("L", "RGB") else s.copy()
                  for s in ImageSequence.Iterator(bild)]
    except Exception as exc:  # noqa: BLE001
        raise OcrFehler(f"Bild nicht lesbar: {exc}") from exc
    puffer = io.BytesIO()
    seiten[0].save(puffer, "PDF", resolution=float(dpi), save_all=True, append_images=seiten[1:])
    return puffer.getvalue()


def _ocrmypdf(pdf_daten):
    """Durchsuchbares PDF/A per OCRmyPDF. Gibt die PDF/A-Bytes zurück."""
    import ocrmypdf

    basis = os.environ.get("TMPDIR") or None
    with tempfile.TemporaryDirectory(prefix="dms_ocr_", dir=basis) as tmp:
        ein, aus = os.path.join(tmp, "ein.pdf"), os.path.join(tmp, "aus.pdf")
        with open(ein, "wb") as f:
            f.write(pdf_daten)
        # force_ocr: wird nur für Seiten ohne verwertbaren Textlayer aufgerufen (Scans). Das vermeidet zudem
        # die Ghostscript-10.0x-Regression bei --skip-text.
        ocrmypdf.ocr(ein, aus, language=[sprache()], output_type="pdfa", force_ocr=True, rotate_pages=True,
                     deskew=True, optimize=0, progress_bar=False, jobs=1,
                     tesseract_timeout=getattr(settings, "ERP_DMS_OCR_TIMEOUT", 180))
        with open(aus, "rb") as f:
            return f.read()


def _pytesseract(pdf_daten):
    """Nur Text (ohne PDF/A) – Rückfall, wenn OCRmyPDF/Ghostscript fehlen."""
    import pymupdf
    import pytesseract
    from PIL import Image

    texte = []
    with pymupdf.open(stream=pdf_daten, filetype="pdf") as doc:
        for seite in doc:
            pix = seite.get_pixmap(dpi=300)
            bild = Image.open(io.BytesIO(pix.tobytes("png")))
            texte.append(pytesseract.image_to_string(bild, lang=sprache()))
    return "\n".join(texte).strip()


def ocr(pdf_daten):
    """OCR eines Scan-PDFs → (text, pdfa_bytes|None, quelle)."""
    if ocrmypdf_verfuegbar():
        try:
            pdfa = _ocrmypdf(pdf_daten)
            return pdf_text(pdfa)[0], pdfa, "ocr"
        except Exception as exc:  # noqa: BLE001 – auf pytesseract zurückfallen
            log.warning("OCRmyPDF fehlgeschlagen, Rückfall auf pytesseract: %s", exc)
    if not tesseract_verfuegbar():
        raise OcrFehler("Tesseract ist nicht installiert – Scan kann nicht erkannt werden "
                        "(siehe deploy/INSTALLATION.md, Abschnitt DMS/OCR).")
    try:
        return _pytesseract(pdf_daten), None, "ocr-einfach"
    except Exception as exc:  # noqa: BLE001
        raise OcrFehler(f"OCR fehlgeschlagen: {exc}") from exc


# ------------------------------------------------------------------ Einstieg

def analysieren(daten, dateiname):
    """Ermittelt Text, Seitenzahl, E-Rechnungs-XML und ggf. PDF/A für eine Eingangsdatei."""
    endung = os.path.splitext(dateiname.lower())[1]
    erg = Ergebnis()
    if endung == ".xml":
        if not xml_art(daten):
            erg.hinweise.append("XML-Datei ist keine bekannte E-Rechnung (XRechnung/ZUGFeRD).")
        else:
            erg.xml, erg.xml_name = daten, dateiname
        erg.text, erg.quelle = xml_text(daten), "xrechnung"
        return erg

    if endung in BILD_ENDUNGEN:
        pdf = bild_zu_pdf(daten)
    elif endung == ".pdf":
        pdf = daten
        erg.xml_name, erg.xml = eingebettete_xml(pdf)
    else:
        raise OcrFehler(f"Dateityp {endung or '?'} wird nicht unterstützt.")

    text, erg.seiten = pdf_text(pdf)
    if erg.xml:
        erg.text = text or xml_text(erg.xml)
        erg.quelle = "zugferd"
        return erg
    if endung == ".pdf" and erg.seiten and len(text) >= MIN_ZEICHEN_JE_SEITE * erg.seiten:
        erg.text, erg.quelle = text, "textlayer"
        return erg
    erg.text, erg.pdfa, erg.quelle = ocr(pdf)
    if not erg.text:
        erg.hinweise.append("OCR hat keinen Text erkannt (leere Seite oder schlechte Scanqualität?).")
    return erg

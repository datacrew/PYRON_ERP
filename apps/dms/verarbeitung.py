"""Verarbeitungspipeline eines Dokuments: NEU → OCR → ERKANNT / PRUEFEN bzw. FEHLER.

Jeder Schritt protokolliert ins Dokumentprotokoll. Die Pipeline ist idempotent pro Version: eine neue
Dateiversion setzt das Dokument wieder auf NEU und es wird erneut verarbeitet.
"""
import logging
import os

from django.db import transaction

from . import ocr as O
from . import services as S
from . import trennung as T
from .models import Dokument, DokumentVersion

log = logging.getLogger(__name__)


def _status(dokument, status, **felder):
    Dokument.objects.filter(pk=dokument.pk).update(status=status, **felder)
    dokument.status = status
    for k, v in felder.items():
        setattr(dokument, k, v)


def warteschlange():
    """Dokumente, die auf Verarbeitung warten (älteste zuerst)."""
    return Dokument.objects.filter(status=Dokument.Status.NEU, archiviert=False).order_by("erstellt_am", "pk")


def verarbeiten(dokument, benutzer=None):
    """Führt Texterkennung (und Folgeschritte) für ein Dokument aus. Gibt das Dokument zurück.

    Fehler werden nicht weitergeworfen, sondern als Status FEHLER mit Meldung festgehalten, damit ein
    Stapel nicht an einer defekten Datei hängen bleibt."""
    dokument.refresh_from_db()
    if dokument.archiviert:
        raise S.DmsFehler(f"{dokument.nummer} ist archiviert und wird nicht erneut verarbeitet.")
    original = dokument.original
    if not original:
        _status(dokument, Dokument.Status.FEHLER, fehlermeldung="Keine Datei vorhanden.")
        return dokument
    _status(dokument, Dokument.Status.OCR, fehlermeldung="")
    try:
        with original.datei.open("rb") as f:
            daten = f.read()
        if _trennen_pruefen(dokument, original, daten, benutzer):
            return dokument
        erg = O.analysieren(daten, original.dateiname)
        with transaction.atomic():
            if erg.pdfa:
                name = os.path.splitext(original.dateiname)[0] + "_pdfa.pdf"
                S.version_speichern(dokument, erg.pdfa, name, art=DokumentVersion.Art.PDFA, quelle=original,
                                    kommentar="Durchsuchbares PDF/A (OCR)", benutzer=benutzer)
            Dokument.objects.filter(pk=dokument.pk).update(
                ocr_text=erg.text, seiten=erg.seiten, textquelle=erg.quelle)
            dokument.refresh_from_db()
            S.suchvektor_aktualisieren(dokument)
            S.protokollieren(dokument, "Texterkennung",
                             f"Quelle: {erg.quelle}, {erg.seiten} Seite(n), {len(erg.text)} Zeichen"
                             + (f", E-Rechnung {erg.xml_name}" if erg.xml else "")
                             + (". " + " ".join(erg.hinweise) if erg.hinweise else "."), benutzer)
            nachverarbeiten(dokument, erg, benutzer)
    except Exception as exc:  # noqa: BLE001 – jeder Fehler landet sichtbar im Dokument
        if isinstance(exc, (O.OcrFehler, S.DmsFehler)):
            log.warning("DMS-Verarbeitung %s: %s", dokument.nummer, exc)
        else:
            log.exception("DMS-Verarbeitung %s fehlgeschlagen", dokument.nummer)
        _status(dokument, Dokument.Status.FEHLER, fehlermeldung=str(exc)[:2000])
        S.protokollieren(dokument, "Fehler", str(exc)[:500], benutzer)
    return dokument


def _trennen_pruefen(dokument, original, daten, benutzer):
    """Sammel-PDF an Trennblättern aufteilen (nur wenn im Stapel aktiviert). True = aufgeteilt."""
    if not (dokument.stapel and dokument.stapel.automatisch_trennen) or dokument.eltern_id:
        return False
    if not original.dateiname.lower().endswith(".pdf"):
        return False
    import pymupdf

    try:
        with pymupdf.open(stream=daten, filetype="pdf") as doc:
            seiten = doc.page_count
    except Exception:  # noqa: BLE001 – defekte PDFs meldet die Texterkennung
        return False
    teile = T.automatisch_aufteilen(dokument, daten, seiten, benutzer)
    if teile:
        Dokument.objects.filter(pk=dokument.pk).update(seiten=seiten)
        dokument.refresh_from_db()
    return bool(teile)


def nachverarbeiten(dokument, erg, benutzer=None):
    """Folgeschritte nach der Texterkennung (Belegerkennung wird hier eingehängt)."""
    _status(dokument, Dokument.Status.ERKANNT)


def warteschlange_abarbeiten(limit=None, benutzer=None, stapel=None):
    """Verarbeitet wartende Dokumente → (verarbeitet, fehler)."""
    qs = warteschlange()
    if stapel is not None:
        qs = qs.filter(stapel=stapel)
    ok = fehler = 0
    for d in qs[:limit] if limit else qs:
        verarbeiten(d, benutzer)
        if d.status == Dokument.Status.FEHLER:
            fehler += 1
        else:
            ok += 1
    return ok, fehler

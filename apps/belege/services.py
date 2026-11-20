"""Beleg-Workflow: Freigabe, Festschreibung (GoBD), Storno und Prüfung der Prüfsummenkette."""
import hashlib
import json
from datetime import timezone as dt_timezone

from django.core.files.base import ContentFile
from django.db import connection, transaction
from django.db.models import Max
from django.utils import timezone

from apps.core.events import veroeffentlichen
from apps.core.nummernkreise import naechste_nummer

from .models import Beleg, BelegGesperrt

GOBD_SPERRE = 470815  # Schlüssel für pg_advisory_xact_lock (serialisiert die Kette)


class WorkflowFehler(Exception):
    pass


def _d(wert):
    return f"{wert:.4f}"


def _partner_snapshot(partner):
    adresse = partner.rechnungsadresse
    return {
        "nummer": partner.nummer, "name": partner.name, "name2": partner.name2,
        "zusatz": adresse.zusatz if adresse else "", "strasse": adresse.strasse if adresse else "",
        "plz": adresse.plz if adresse else "", "ort": adresse.ort if adresse else "",
        "land": adresse.land if adresse else "DE", "ust_id": partner.ust_id, "leitweg_id": partner.leitweg_id,
    }


def pruefsumme_berechnen(beleg):
    """SHA-256 über den kanonisierten Beleginhalt inkl. Vorgänger-Prüfsumme (Hash-Kette)."""
    inhalt = {
        "belegart": beleg.belegart, "nummer": beleg.nummer, "belegdatum": beleg.belegdatum.isoformat(),
        "leistungsdatum": beleg.leistungsdatum.isoformat() if beleg.leistungsdatum else None,
        "partner_id": beleg.partner_id, "partner": beleg.partner_daten,
        "betreff": beleg.betreff, "kopftext": beleg.kopftext, "fusstext": beleg.fusstext,
        "waehrung": beleg.waehrung, "netto": _d(beleg.summe_netto), "steuer": _d(beleg.summe_steuer),
        "brutto": _d(beleg.summe_brutto),
        "festgeschrieben_am": beleg.festgeschrieben_am.astimezone(dt_timezone.utc).isoformat(),
        "ketten_index": beleg.ketten_index, "vorgaenger": beleg.vorgaenger_pruefsumme,
        "positionen": [
            [p.position, p.artikel_id, p.bezeichnung, _d(p.menge), p.einheit, _d(p.einzelpreis),
             _d(p.rabatt_prozent), _d(p.steuersatz), _d(p.betrag_netto)]
            for p in beleg.positionen.order_by("position", "pk")
        ],
    }
    roh = json.dumps(inhalt, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()


def _sperren(pk):
    beleg = Beleg.objects.select_for_update().get(pk=pk)
    beleg._workflow_uebergang = True
    return beleg


def freigeben(beleg, benutzer):
    with transaction.atomic():
        b = _sperren(beleg.pk)
        if b.status != Beleg.Status.ENTWURF:
            raise WorkflowFehler("Nur Entwürfe können freigegeben werden.")
        if not b.positionen.exists():
            raise WorkflowFehler("Ein Beleg ohne Positionen kann nicht freigegeben werden.")
        b.berechne_summen()
        b.nummer = naechste_nummer(f"BELEG_{b.belegart}", b.belegdatum)
        b.partner_daten = _partner_snapshot(b.partner)
        b.status = Beleg.Status.FREIGEGEBEN
        b.freigegeben_von, b.freigegeben_am = benutzer, timezone.now()
        b.save()
    veroeffentlichen("beleg.freigegeben", beleg=b, benutzer=benutzer)
    return b


def festschreiben(beleg, benutzer):
    """GoBD-Festschreibung: Hash-Kette fortführen, PDF erzeugen und archivieren."""
    from .pdf import beleg_pdf

    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", [GOBD_SPERRE])
        b = _sperren(beleg.pk)
        if b.status != Beleg.Status.FREIGEGEBEN:
            raise WorkflowFehler("Nur freigegebene Belege können festgeschrieben werden.")
        letzter = Beleg.objects.exclude(ketten_index=None).aggregate(m=Max("ketten_index"))["m"] or 0
        vorgaenger = Beleg.objects.filter(ketten_index=letzter).values_list("pruefsumme", flat=True).first() or ""
        b.ketten_index = letzter + 1
        b.vorgaenger_pruefsumme = vorgaenger
        b.festgeschrieben_von, b.festgeschrieben_am = benutzer, timezone.now()
        b.status = Beleg.Status.FESTGESCHRIEBEN
        b.pruefsumme = pruefsumme_berechnen(b)
        pdf_bytes = beleg_pdf(b)
        b.pdf_pruefsumme = hashlib.sha256(pdf_bytes).hexdigest()
        b.pdf.save(f"{b.nummer}.pdf", ContentFile(pdf_bytes), save=False)
        b.save()
    veroeffentlichen("beleg.festgeschrieben", beleg=b, benutzer=benutzer)
    return b


def stornieren(beleg, benutzer, grund):
    if not (grund or "").strip():
        raise WorkflowFehler("Bitte einen Stornogrund angeben.")
    with transaction.atomic():
        b = _sperren(beleg.pk)
        if b.status not in (Beleg.Status.FREIGEGEBEN, Beleg.Status.FESTGESCHRIEBEN):
            raise WorkflowFehler("Nur freigegebene oder festgeschriebene Belege können storniert werden.")
        b.status = Beleg.Status.STORNIERT
        b.storniert_von, b.storniert_am, b.storno_grund = benutzer, timezone.now(), grund.strip()
        b.save()
    veroeffentlichen("beleg.storniert", beleg=b, benutzer=benutzer)
    return b


def kette_pruefen():
    """Prüft alle festgeschriebenen Belege. Rückgabe: (anzahl_geprueft, [Fehlermeldungen])."""
    fehler, vorher, anzahl = [], "", 0
    for b in Beleg.objects.exclude(ketten_index=None).order_by("ketten_index").prefetch_related("positionen"):
        anzahl += 1
        if b.vorgaenger_pruefsumme != vorher:
            fehler.append(f"{b}: Verkettung unterbrochen (Vorgänger-Prüfsumme passt nicht).")
        if pruefsumme_berechnen(b) != b.pruefsumme:
            fehler.append(f"{b}: Inhalt wurde nach der Festschreibung verändert (Prüfsumme abweichend).")
        if b.pdf:
            try:
                with b.pdf.open("rb") as f:
                    if hashlib.sha256(f.read()).hexdigest() != b.pdf_pruefsumme:
                        fehler.append(f"{b}: Archiv-PDF wurde verändert.")
            except FileNotFoundError:
                fehler.append(f"{b}: Archiv-PDF fehlt.")
        vorher = b.pruefsumme
    return anzahl, fehler


__all__ = ["freigeben", "festschreiben", "stornieren", "kette_pruefen", "WorkflowFehler", "BelegGesperrt"]

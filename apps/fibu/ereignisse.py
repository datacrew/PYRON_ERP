"""Ereignis-Abonnements der Finanzbuchhaltung → automatische, idempotente Buchungen.

Fehler (z. B. kein Geschäftsjahr, gesperrte Periode, fehlende Kontierungsregel) brechen den Vorgang im
Vorsystem NICHT ab: Die Buchung läuft in einem Savepoint, das Ergebnis landet im Automatik-Protokoll und
kann später über „Nachbuchen“ (Fibu-Dashboard) erneut versucht werden.
"""
import logging

from django.db import transaction

from apps.core.events import abonnieren

logger = logging.getLogger("apps.fibu")


def _protokollieren(quelle, quelle_id, funktion):
    from .models import AutomatikProtokoll

    try:
        with transaction.atomic():
            buchung = funktion()
    except Exception as exc:  # noqa: BLE001 – bewusst: Vorsystem darf nicht scheitern
        logger.warning("Fibu-Automatik %s %s fehlgeschlagen: %s", quelle, quelle_id, exc)
        AutomatikProtokoll.objects.update_or_create(quelle=quelle, quelle_id=str(quelle_id), defaults={
            "erfolgreich": False, "meldung": str(exc)[:500], "buchung": None})
        return None
    AutomatikProtokoll.objects.update_or_create(quelle=quelle, quelle_id=str(quelle_id), defaults={
        "erfolgreich": True, "meldung": f"Buchung {buchung.nummer}" if buchung else "Keine Buchung erforderlich",
        "buchung": buchung})
    return buchung


@abonnieren("beleg.festgeschrieben")
def beleg_festgeschrieben(beleg, benutzer=None, **kwargs):
    if beleg.belegart in ("RECHNUNG", "GUTSCHRIFT"):
        from .services import ar_buchen

        _protokollieren("beleg", beleg.pk, lambda: ar_buchen(beleg, benutzer))


@abonnieren("beleg.storniert")
def beleg_storniert(beleg, benutzer=None, **kwargs):
    from .models import Buchung
    from .services import ar_stornieren

    if Buchung.objects.filter(quelle="beleg", quelle_id=str(beleg.pk)).exists():
        _protokollieren("beleg_storno", beleg.pk, lambda: ar_stornieren(beleg, benutzer))


@abonnieren("wareneingang.gebucht")
def wareneingang_gebucht(wareneingang, benutzer=None, **kwargs):
    from .services import we_buchen

    _protokollieren("wareneingang", wareneingang.pk, lambda: we_buchen(wareneingang, benutzer))


@abonnieren("produktion.material_entnommen")
def material_entnommen(auftrag, entnahmen=(), benutzer=None, **kwargs):
    from .services import material_buchen

    entnahmen = list(entnahmen)
    if entnahmen:
        _protokollieren("material", "-".join(str(e.pk) for e in entnahmen)[:60],
                        lambda: material_buchen(auftrag, entnahmen, benutzer))


@abonnieren("produktion.fertiggemeldet")
def fertiggemeldet(auftrag, fertigmeldung=None, wert=0, benutzer=None, **kwargs):
    from .services import fertigmeldung_buchen

    if fertigmeldung is not None:
        _protokollieren("fertigmeldung", fertigmeldung.pk,
                        lambda: fertigmeldung_buchen(auftrag, fertigmeldung, wert, benutzer))


def nachbuchen(benutzer=None):
    """Fehlgeschlagene Automatikbuchungen erneut versuchen → (erfolgreich, weiterhin_fehlerhaft)."""
    from apps.belege.models import Beleg
    from apps.produktion.models import Fertigmeldung, Materialentnahme
    from apps.wareneingang.models import Wareneingang

    from .models import AutomatikProtokoll
    from .services import ar_buchen, fertigmeldung_buchen, material_buchen, we_buchen

    ok = fehler = 0
    for p in AutomatikProtokoll.objects.filter(erfolgreich=False):
        if p.quelle == "beleg":
            b = Beleg.objects.get(pk=p.quelle_id)
            ergebnis = _protokollieren("beleg", b.pk, lambda: ar_buchen(b, benutzer))
        elif p.quelle == "wareneingang":
            we = Wareneingang.objects.get(pk=p.quelle_id)
            ergebnis = _protokollieren("wareneingang", we.pk, lambda: we_buchen(we, benutzer))
        elif p.quelle == "material":
            ent = list(Materialentnahme.objects.filter(pk__in=p.quelle_id.split("-")).select_related("artikel"))
            fa = ent[0].auftrag if ent else None
            ergebnis = _protokollieren("material", p.quelle_id, lambda: material_buchen(fa, ent, benutzer)) if fa else None
        elif p.quelle == "fertigmeldung":
            fm = Fertigmeldung.objects.select_related("auftrag__artikel").get(pk=p.quelle_id)
            ergebnis = _protokollieren("fertigmeldung", fm.pk,
                                       lambda: fertigmeldung_buchen(fm.auftrag, fm, fm.herstellkosten, benutzer))
        else:
            continue
        p.refresh_from_db()
        if p.erfolgreich:
            ok += 1
        else:
            fehler += 1
        del ergebnis
    return ok, fehler

"""Ereignis-Abonnements der Finanzbuchhaltung.

Phase 1: Nur Protokollierung. Ab Phase 4 wird hier aus jeder festgeschriebenen Rechnung
automatisch ein Buchungssatz (Debitor an Erlöse/Umsatzsteuer) erzeugt.
"""
import logging

from apps.core.events import abonnieren

logger = logging.getLogger("apps.fibu")


@abonnieren("beleg.festgeschrieben")
def beleg_festgeschrieben(beleg, benutzer=None, **kwargs):
    logger.info("Fibu: %s festgeschrieben (brutto %s) – Buchung folgt ab Phase 4.", beleg, beleg.summe_brutto)

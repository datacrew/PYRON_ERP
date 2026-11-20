"""Einfacher Ereignisbus für die Kommunikation zwischen Modulen (auf Basis von Django-Signals).

Module kennen sich so nicht direkt: Das Belegmodul veröffentlicht z. B. „beleg.festgeschrieben“,
die Finanzbuchhaltung abonniert das Ereignis und erzeugt (ab Phase 4) die Buchung.

    from apps.core.events import abonnieren, veroeffentlichen

    @abonnieren("beleg.festgeschrieben")
    def buchen(beleg, benutzer=None, **kwargs): ...

    veroeffentlichen("beleg.festgeschrieben", beleg=beleg, benutzer=user)
"""
import logging

from django.dispatch import Signal

logger = logging.getLogger("apps.core.events")
_ereignis = Signal()

# Bekannte Ereignisse (Dokumentation; weitere dürfen frei ergänzt werden)
EREIGNISSE = {
    "beleg.freigegeben": "Beleg wurde freigegeben und hat seine Nummer erhalten",
    "beleg.festgeschrieben": "Beleg wurde GoBD-konform festgeschrieben und archiviert",
    "beleg.storniert": "Beleg wurde storniert",
}


def veroeffentlichen(name, **daten):
    logger.debug("Ereignis %s", name)
    return _ereignis.send(sender=name, **daten)


def abonnieren(name):
    def deco(fn):
        def empfaenger(sender, **kwargs):
            if sender == name:
                return fn(**kwargs)

        _ereignis.connect(empfaenger, weak=False, dispatch_uid=f"{name}:{fn.__module__}.{fn.__qualname__}")
        return fn

    return deco

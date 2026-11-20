"""Lückenlose Nummernvergabe.

Die Zeile des Nummernkreises wird per SELECT ... FOR UPDATE gesperrt. Wird die Nummer innerhalb
einer umgebenden Transaktion gezogen (z. B. Belegfreigabe) und diese bricht ab, wird auch der
Zähler zurückgerollt – es entstehen keine Lücken.
"""
from django.db import transaction
from django.utils import timezone

from .models import Nummernkreis


class NummernkreisFehler(Exception):
    pass


def naechste_nummer(code, datum=None):
    jahr = (datum or timezone.localdate()).year
    with transaction.atomic():
        try:
            nk = Nummernkreis.objects.select_for_update().get(code=code)
        except Nummernkreis.DoesNotExist as exc:
            raise NummernkreisFehler(f"Nummernkreis „{code}“ ist nicht angelegt.") from exc
        if nk.jahresbezogen and nk.aktuelles_jahr != jahr:
            if nk.aktuelles_jahr and jahr < nk.aktuelles_jahr:
                raise NummernkreisFehler(
                    f"Nummernkreis „{code}“ ist bereits im Jahr {nk.aktuelles_jahr}; Rückdatierung nicht möglich."
                )
            nk.aktuelles_jahr = jahr
            nk.naechste_nummer = 1
        nummer = nk.naechste_nummer
        nk.naechste_nummer = nummer + 1
        nk.save(update_fields=["naechste_nummer", "aktuelles_jahr"])
        return nk.formatieren(nummer, jahr)


def vorschau(code, datum=None):
    """Zeigt die nächste Nummer an, ohne sie zu verbrauchen."""
    nk = Nummernkreis.objects.get(code=code)
    jahr = (datum or timezone.localdate()).year
    nummer = 1 if (nk.jahresbezogen and nk.aktuelles_jahr != jahr) else nk.naechste_nummer
    return nk.formatieren(nummer, jahr)

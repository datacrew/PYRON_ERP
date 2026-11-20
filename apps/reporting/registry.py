"""Bericht-Registry.

Jedes Fachmodul kann in einer Datei ``berichte.py`` Berichte registrieren; die Reporting-App lädt diese
Dateien beim Start (``autodiscover_modules("berichte")``). Ein Bericht liest Daten ausschließlich über
Querysets/Services seines Moduls und schreibt nichts.

Beispiel::

    from apps.reporting.registry import Spalte, bericht

    @bericht("lager.beispiel", "Mein Bericht", modul="lager", filter=["stichtag", "artikel"],
             spalten=[Spalte("artikel", "Artikel"), Spalte("wert", "Wert", "geld", summe=True)])
    def mein_bericht(f):
        # f = bereinigte Filterwerte (dict); Rückgabe: Liste von Zeilen-Dicts oder dict mit "zeilen"/"hinweise"
        return [{"artikel": "F-5000", "wert": Decimal("12.50")}]

Sichtbar ist ein Bericht nur mit Recht ``reporting.lesen`` UND Leserecht auf ``modul``.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

TYPEN = ("text", "zahl", "menge", "geld", "prozent", "datum", "stunden")


@dataclass
class Spalte:
    key: str
    label: str
    typ: str = "text"
    summe: bool = False  # Summenzeile bilden (nur numerische Typen)

    @property
    def numerisch(self):
        return self.typ in ("zahl", "menge", "geld", "prozent", "stunden")


@dataclass
class Bericht:
    code: str
    name: str
    modul: str
    funktion: Callable
    beschreibung: str = ""
    filter: list = field(default_factory=list)
    spalten: list = field(default_factory=list)
    quer: bool = False
    diagramm: dict | None = None  # {"typ": "bar"|"line"|"pie", "label": key, "wert": key}

    @property
    def kategorie(self):
        from apps.core.models import Modul

        m = Modul.objects.filter(code=self.modul).first()
        return m.name if m else self.modul

    def ausfuehren(self, filterwerte):
        """Liefert {"zeilen", "summen", "hinweise"}; Summen werden aus den Zeilen gebildet."""
        ergebnis = self.funktion(filterwerte)
        if not isinstance(ergebnis, dict):
            ergebnis = {"zeilen": list(ergebnis)}
        zeilen = ergebnis.get("zeilen", [])
        summen = None
        if any(s.summe for s in self.spalten):
            summen = {s.key: sum((z.get(s.key) or Decimal(0) for z in zeilen), Decimal(0))
                      for s in self.spalten if s.summe}
            summen.update(ergebnis.get("summen_extra", {}))
        return {"zeilen": zeilen, "summen": summen, "hinweise": ergebnis.get("hinweise", [])}


_BERICHTE: dict[str, Bericht] = {}


def bericht(code, name, *, modul, spalten, filter=None, beschreibung="", quer=None, diagramm=None):
    """Decorator zum Registrieren eines Berichts."""

    def deco(funktion):
        registrieren(Bericht(code=code, name=name, modul=modul, funktion=funktion, beschreibung=beschreibung,
                             filter=list(filter or []), spalten=list(spalten),
                             quer=len(spalten) > 7 if quer is None else quer, diagramm=diagramm))
        return funktion

    return deco


def registrieren(b: Bericht):
    from .filter import FILTER

    unbekannt = [f for f in b.filter if f not in FILTER]
    if unbekannt:
        raise ValueError(f"Bericht {b.code}: unbekannte Filter {unbekannt}")
    for s in b.spalten:
        if s.typ not in TYPEN:
            raise ValueError(f"Bericht {b.code}: unbekannter Spaltentyp {s.typ}")
    _BERICHTE[b.code] = b


def alle():
    return sorted(_BERICHTE.values(), key=lambda b: (b.modul, b.code))


def holen(code):
    return _BERICHTE.get(code)


def darf(user, b):
    from apps.core.permissions import hat_recht

    return hat_recht(user, "reporting") and hat_recht(user, b.modul)


def fuer_benutzer(user):
    return [b for b in alle() if darf(user, b)]

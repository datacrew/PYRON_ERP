"""Filterparameter der Berichte: Formularfelder, Standardwerte und Klartext für Export-Köpfe."""
from datetime import date

from django import forms
from django.utils import timezone


def _heute():
    return timezone.localdate()


def _partner(**kw):
    from apps.stammdaten.models import Geschaeftspartner

    return Geschaeftspartner.objects.filter(aktiv=True, **kw).order_by("name")


def _artikel():
    from apps.stammdaten.models import Artikel

    return Artikel.objects.filter(aktiv=True).order_by("artikelnummer")


def _artikelarten():
    from apps.stammdaten.models import Artikel

    return [("", "– alle –")] + list(Artikel._meta.get_field("art").choices)


def _kostenstellen():
    from apps.stammdaten.models import Arbeitsplatz

    werte = sorted(set(Arbeitsplatz.objects.exclude(kostenstelle="").values_list("kostenstelle", flat=True)))
    return [("", "– alle –")] + [(k, k) for k in werte]


DATUM = {"widget": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}

# name: (Feld-Fabrik, Standardwert-Funktion oder None)
FILTER = {
    "von": (lambda: forms.DateField(label="Von", required=False, **DATUM), lambda: date(_heute().year, 1, 1)),
    "bis": (lambda: forms.DateField(label="Bis", required=False, **DATUM), _heute),
    "stichtag": (lambda: forms.DateField(label="Stichtag", required=False, **DATUM), _heute),
    "kunde": (lambda: forms.ModelChoiceField(_partner(ist_kunde=True), label="Kunde", required=False,
                                             empty_label="– alle –"), None),
    "lieferant": (lambda: forms.ModelChoiceField(_partner(ist_lieferant=True), label="Lieferant", required=False,
                                                 empty_label="– alle –"), None),
    "artikel": (lambda: forms.ModelChoiceField(_artikel(), label="Artikel", required=False,
                                               empty_label="– alle –"), None),
    "artikelart": (lambda: forms.ChoiceField(choices=_artikelarten(), label="Artikelart", required=False), None),
    "kostenstelle": (lambda: forms.ChoiceField(choices=_kostenstellen(), label="Kostenstelle", required=False), None),
    "tage": (lambda: forms.IntegerField(label="Tage", required=False, min_value=0, max_value=3650), lambda: 90),
    "op_art": (lambda: forms.ChoiceField(choices=[("D", "Debitoren"), ("K", "Kreditoren")], label="Art",
                                         required=False), lambda: "D"),
    "lager_gruppe": (lambda: forms.ChoiceField(choices=[("art", "Artikelart"), ("lagerort", "Lagerort")],
                                               label="Gruppierung", required=False), lambda: "art"),
    "ek_gruppe": (lambda: forms.ChoiceField(choices=[("lieferant", "Lieferant"), ("art", "Artikelart")],
                                            label="Gruppierung", required=False), lambda: "lieferant"),
    "ausschuss_gruppe": (lambda: forms.ChoiceField(choices=[("artikel", "Artikel"), ("grund", "Ausschussgrund")],
                                                   label="Gruppierung", required=False), lambda: "artikel"),
    "db_gruppe": (lambda: forms.ChoiceField(choices=[("artikel", "Artikel"), ("kunde", "Kunde")],
                                            label="Gruppierung", required=False), lambda: "artikel"),
}


def formular(bericht, daten=None):
    """Dynamisches Filterformular; leere Felder werden mit Standardwerten vorbelegt."""
    felder = {name: FILTER[name][0]() for name in bericht.filter}
    form_klasse = type("BerichtFilterForm", (forms.Form,), felder)
    form = form_klasse(daten if daten else None, initial=standardwerte(bericht))
    for f in form.fields.values():
        f.widget.attrs.setdefault("class", "form-select form-select-sm" if isinstance(f.widget, forms.Select)
                                  else "form-control form-control-sm")
    return form


def standardwerte(bericht):
    return {n: FILTER[n][1]() for n in bericht.filter if FILTER[n][1]}


def bereinigen(bericht, daten):
    """Prüft QueryDict/dict und liefert (werte, fehler). Leere Werte → Standardwert."""
    form = formular(bericht, {k: v for k, v in (daten or {}).items() if k in bericht.filter} or {"_": "1"})
    if not form.is_valid():
        return None, {k: [str(e) for e in v] for k, v in form.errors.items()}
    werte = standardwerte(bericht)
    for k, v in form.cleaned_data.items():
        if v not in (None, ""):
            werte[k] = v
    werte.setdefault("artikelart", "")
    return werte, None


def als_json(werte):
    """Filterwerte für die Speicherung (Berichtsvariante) serialisieren."""
    aus = {}
    for k, v in werte.items():
        if hasattr(v, "pk"):
            aus[k] = v.pk
        elif isinstance(v, date):
            aus[k] = v.isoformat()
        elif v not in (None, ""):
            aus[k] = v
    return aus


def klartext(bericht, werte):
    """[(Label, Text)] für Export-Köpfe."""
    aus = []
    for name in bericht.filter:
        feld = FILTER[name][0]()
        v = werte.get(name)
        if v in (None, ""):
            text = "alle"
        elif isinstance(v, date):
            text = v.strftime("%d.%m.%Y")
        elif hasattr(feld, "choices") and not hasattr(v, "pk"):
            text = dict(feld.choices).get(v, v)
        else:
            text = str(v)
        aus.append((feld.label, text))
    return aus

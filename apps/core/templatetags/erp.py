"""Vorlagen-Filter des ERP (global als builtin geladen, also auch in DB-Belegvorlagen nutzbar)."""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django import template
from django.db.models import Model

from apps.core.permissions import hat_recht as _hat_recht

register = template.Library()


def _deutsch(wert, stellen):
    try:
        d = Decimal(str(wert))
    except (InvalidOperation, TypeError, ValueError):
        return wert
    q = Decimal(1).scaleb(-stellen) if stellen else Decimal(1)
    d = d.quantize(q, rounding=ROUND_HALF_UP)
    text = f"{d:,.{stellen}f}"  # 1,234.56
    return text.replace(",", "§").replace(".", ",").replace("§", ".")


@register.filter
def zahl(wert, stellen=2):
    """1234.5 → „1.234,50“ (deutsches Zahlenformat)."""
    if wert in (None, ""):
        return ""
    return _deutsch(wert, int(stellen))


@register.filter
def menge(wert):
    """Menge ohne überflüssige Nachkommastellen: 2.000 → „2“, 1.250 → „1,25“."""
    if wert in (None, ""):
        return ""
    d = Decimal(str(wert)).normalize()
    stellen = max(0, -d.as_tuple().exponent)
    return _deutsch(d, min(stellen, 4))


@register.filter
def euro(wert):
    if wert in (None, ""):
        return ""
    return f"{_deutsch(wert, 2)} €"


@register.filter
def attr(obj, pfad):
    """Liest verschachtelte Attribute (`partner.name`), ruft Methoden auf, nutzt get_FOO_display."""
    for teil in str(pfad).split("."):
        if obj is None:
            return ""
        anzeige = f"get_{teil}_display"
        if isinstance(obj, Model) and hasattr(obj, anzeige) and getattr(obj, "_meta").get_field(teil).choices:
            obj = getattr(obj, anzeige)()
            continue
        obj = getattr(obj, teil, "")
        if callable(obj) and not isinstance(obj, Model):
            obj = obj()
    return obj


@register.simple_tag(takes_context=True)
def hat_recht(context, modul, aktion="lesen"):
    return _hat_recht(context["request"].user, modul, aktion)


@register.filter
def ist_bool(wert):
    return isinstance(wert, bool)

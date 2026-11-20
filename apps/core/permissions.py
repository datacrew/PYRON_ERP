"""Granulare Rechteprüfung pro Modul und Aktion.

Regeln:
* Inaktive Module sind für niemanden erreichbar (auch nicht für Superuser).
* Superuser haben in aktiven Modulen alle Rechte.
* Alle anderen erhalten die Vereinigung der Rechte ihrer Rollen.
"""
from functools import wraps

from django.contrib.auth.mixins import AccessMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

from .models import AKTION_CODES


def rechte_fuer(user):
    """Liefert {modul_code: {aktionen}} – pro Request am User-Objekt zwischengespeichert."""
    if getattr(user, "_erp_rechte", None) is not None:
        return user._erp_rechte
    from .models import Modul, Recht

    rechte = {}
    if user.is_authenticated and user.is_active:
        if user.is_superuser:
            for code in Modul.objects.filter(aktiv=True).values_list("code", flat=True):
                rechte[code] = set(AKTION_CODES)
        else:
            for r in Recht.objects.filter(rolle__benutzer=user, modul__aktiv=True).select_related("modul"):
                menge = rechte.setdefault(r.modul.code, set())
                menge.update(a for a in AKTION_CODES if getattr(r, a))
    user._erp_rechte = rechte
    return rechte


def rechte_cache_leeren(user):
    if hasattr(user, "_erp_rechte"):
        del user._erp_rechte


def hat_recht(user, modul, aktion="lesen"):
    if user is None or not user.is_authenticated or not user.is_active:
        return False
    if aktion not in AKTION_CODES:
        raise ValueError(f"Unbekannte Aktion: {aktion}")
    return aktion in rechte_fuer(user).get(modul, set())


def recht_erforderlich(modul, aktion="lesen"):
    """Decorator für funktionsbasierte Views."""

    def deco(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not hat_recht(request.user, modul, aktion):
                raise PermissionDenied(f"Kein Recht „{aktion}“ im Modul „{modul}“.")
            return view(request, *args, **kwargs)

        return wrapper

    return deco


class ModulRechtMixin(AccessMixin):
    """Mixin für klassenbasierte Views: `modul_code` und `recht_aktion` setzen."""

    modul_code = None
    recht_aktion = "lesen"

    def get_recht_aktion(self):
        return self.recht_aktion

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not hat_recht(request.user, self.modul_code, self.get_recht_aktion()):
            raise PermissionDenied(f"Kein Recht „{self.get_recht_aktion()}“ im Modul „{self.modul_code}“.")
        return super().dispatch(request, *args, **kwargs)


class ModulRecht(BasePermission):
    """DRF-Permission. ViewSets setzen `modul_code`; Sonderaktionen über `recht_aktionen`."""

    METHODEN = {
        "GET": "lesen", "HEAD": "lesen", "OPTIONS": "lesen",
        "POST": "anlegen", "PUT": "aendern", "PATCH": "aendern", "DELETE": "loeschen",
    }

    def has_permission(self, request, view):
        modul = getattr(view, "modul_code", None)
        if not modul:
            return False
        aktion = getattr(view, "recht_aktionen", {}).get(getattr(view, "action", None))
        aktion = aktion or self.METHODEN.get(request.method, "lesen")
        return hat_recht(request.user, modul, aktion)

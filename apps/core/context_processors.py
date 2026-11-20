from .models import Mandant, Modul
from .permissions import rechte_fuer


def erp_kontext(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    rechte = rechte_fuer(user)
    sichtbar = [m for m in Modul.objects.filter(aktiv=True) if "lesen" in rechte.get(m.code, set())]
    return {"erp_module": sichtbar, "rechte": rechte, "mandant": Mandant.aktuell()}

"""Modul-Registry: sammelt die `erp_modul`-Beschreibungen aller installierten Apps.

Ein neues Modul wird hinzugefügt, indem man eine Django-App mit `erp_modul` in ihrer AppConfig
anlegt und in settings.ERP_APPS einträgt. Nach `migrate` erscheint es automatisch in der
Modulverwaltung und der Rechte-Matrix.
"""
from django.apps import apps


def erp_module():
    module = []
    for cfg in apps.get_app_configs():
        beschreibung = getattr(cfg, "erp_modul", None)
        if beschreibung:
            module.append({**beschreibung, "app_label": cfg.label})
    return sorted(module, key=lambda m: m.get("reihenfolge", 100))


def module_synchronisieren(**kwargs):
    """Legt fehlende Module an und aktualisiert Metadaten; der Aktiv-Schalter bleibt erhalten."""
    from .models import Modul

    for m in erp_module():
        werte = {
            "name": m["name"],
            "beschreibung": m.get("beschreibung", ""),
            "icon": m.get("icon", ""),
            "reihenfolge": m.get("reihenfolge", 100),
            "url_name": m.get("url_name", ""),
            "pflicht": m.get("pflicht", False),
            "geplant": m.get("geplant", []),
        }
        obj, angelegt = Modul.objects.get_or_create(code=m["code"], defaults=werte)
        if not angelegt:
            for feld, wert in werte.items():
                setattr(obj, feld, wert)
            if obj.pflicht:
                obj.aktiv = True
            obj.save()

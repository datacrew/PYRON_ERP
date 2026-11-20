from django.apps import AppConfig


class VersandConfig(AppConfig):
    """Phase 5 – Vertrieb und Versand."""

    name = "apps.versand"
    label = "versand"
    verbose_name = "Versand"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "versand",
        "name": "Versand",
        "beschreibung": "Tourenplanung nach PLZ-Regionen und Liefertagen, Fahrzeugkapazität, Touren-/Ladelisten, LVS-Schnittstelle",
        "icon": "bi-truck",
        "reihenfolge": 70,
        "url_name": "versand:index",
        "geplant": [],
    }

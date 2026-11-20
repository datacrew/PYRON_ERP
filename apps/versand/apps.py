from django.apps import AppConfig


class VersandConfig(AppConfig):
    """Gerüst – fachliche Umsetzung folgt laut docs/ROADMAP.md."""

    name = "apps.versand"
    label = "versand"
    verbose_name = "Versand"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "versand",
        "name": "Versand",
        "beschreibung": "Versandlager, Tourenplanung nach Kundenstandort, LVS-Schnittstelle",
        "icon": "bi-truck",
        "reihenfolge": 70,
        "url_name": "",
        "geplant": ['Kommissionierung und Packstücke', 'Tourenplanung nach PLZ-Gebieten/Geokoordinaten', 'Ladelisten und Fahreraufträge', 'Schnittstelle zur Lagerverwaltungssoftware (LVS)', 'Versanddienstleister-Anbindung'],
    }

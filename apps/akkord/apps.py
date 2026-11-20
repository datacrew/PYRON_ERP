"""Rechte-Modul „Akkordlohn“: Lohnauswertung und Personal-/Lohnsätze. Die Oberfläche liegt in apps.produktion;
dieses Modul existiert nur für die getrennte Rechtevergabe (Lohndaten sind vertraulich)."""
from django.apps import AppConfig


class AkkordConfig(AppConfig):
    name = "apps.akkord"
    label = "akkord"
    verbose_name = "Akkordlohn"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "akkord",
        "name": "Akkordlohn",
        "beschreibung": "Akkordauswertung je Mitarbeiter, Auftrag und Zeitraum (Leistungsgrad, Akkordverdienst, "
                        "Mindestlohngarantie), Lohnübersicht als PDF/CSV; Pflege der Lohnsätze",
        "icon": "bi-cash-coin",
        "reihenfolge": 52,
        "url_name": "produktion:akkord",
        "geplant": [],
    }

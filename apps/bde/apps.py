"""Rechte-Modul „BDE-Terminal“: Stempeln am Terminal (lesen/anlegen) und Korrektur von Stempelungen (ändern).
Die Oberfläche liegt in apps.produktion; dieses Modul existiert nur für die getrennte Rechtevergabe."""
from django.apps import AppConfig


class BdeConfig(AppConfig):
    name = "apps.bde"
    label = "bde"
    verbose_name = "BDE-Terminal"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "bde",
        "name": "BDE-Terminal",
        "beschreibung": "Stempel-Terminal: Kommen/Gehen, An-/Abmelden am Arbeitsgang, Rückmeldung. "
                        "Recht „ändern“ = Korrektur von Stempelungen.",
        "icon": "bi-upc-scan",
        "reihenfolge": 51,
        "url_name": "produktion:terminal",
        "geplant": [],
    }

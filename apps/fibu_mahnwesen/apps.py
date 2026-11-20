"""Rechte-Modul „Mahnwesen" der Finanzbuchhaltung: lesen = ansehen, anlegen = ausführen.
Die Oberfläche liegt in apps.fibu; dieses Modul existiert nur für die getrennte Rechtevergabe."""
from django.apps import AppConfig


class FibuMahnwesenConfig(AppConfig):
    name = "apps.fibu_mahnwesen"
    label = "fibu_mahnwesen"
    verbose_name = "Mahnwesen"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "fibu_mahnwesen",
        "name": "Mahnwesen",
        "beschreibung": "Mahnvorschlag, Mahnlauf ausführen, Mahnschreiben",
        "icon": "bi-envelope-exclamation",
        "reihenfolge": 82,
        "url_name": "fibu:mahnwesen",
        "geplant": [],
    }

"""Rechte-Modul „Periodenabschluss" der Finanzbuchhaltung: lesen = ansehen, anlegen = ausführen.
Die Oberfläche liegt in apps.fibu; dieses Modul existiert nur für die getrennte Rechtevergabe."""
from django.apps import AppConfig


class FibuAbschlussConfig(AppConfig):
    name = "apps.fibu_abschluss"
    label = "fibu_abschluss"
    verbose_name = "Periodenabschluss"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "fibu_abschluss",
        "name": "Periodenabschluss",
        "beschreibung": "Periodensperre, Geschäftsjahresabschluss und Saldenvortrag",
        "icon": "bi-lock",
        "reihenfolge": 83,
        "url_name": "fibu:perioden",
        "geplant": [],
    }

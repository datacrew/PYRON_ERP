"""Rechte-Modul „Bankimport" der Finanzbuchhaltung: lesen = ansehen, anlegen = ausführen.
Die Oberfläche liegt in apps.fibu; dieses Modul existiert nur für die getrennte Rechtevergabe."""
from django.apps import AppConfig


class FibuBankConfig(AppConfig):
    name = "apps.fibu_bank"
    label = "fibu_bank"
    verbose_name = "Bankimport"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "fibu_bank",
        "name": "Bankimport",
        "beschreibung": "Kontoauszüge importieren (CAMT.053/MT940), Umsätze zuordnen und buchen",
        "icon": "bi-cloud-upload",
        "reihenfolge": 81,
        "url_name": "fibu:bank",
        "geplant": [],
    }

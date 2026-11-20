"""Rechte-Modul „EDI/EDIFACT“: lesen = Nachrichten ansehen/herunterladen, anlegen = ORDERS importieren und
Nachrichten erzeugen, aendern = EDI-Partner und Kundenartikelnummern pflegen.
Die Oberfläche liegt in apps.vertrieb; dieses Modul existiert nur für die getrennte Rechtevergabe."""
from django.apps import AppConfig


class EdiConfig(AppConfig):
    name = "apps.edi"
    label = "edi"
    verbose_name = "EDI/EDIFACT"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "edi",
        "name": "EDI/EDIFACT",
        "beschreibung": "ORDERS-Import, ORDRSP/DESADV/INVOIC-Export, EDI-Partner (GLN), Kundenartikelnummern",
        "icon": "bi-arrow-left-right",
        "reihenfolge": 62,
        "url_name": "vertrieb:edi",
        "geplant": [],
    }

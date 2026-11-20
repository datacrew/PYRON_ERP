from django.apps import AppConfig


class EinkaufConfig(AppConfig):
    """Gerüst – fachliche Umsetzung folgt laut docs/ROADMAP.md."""

    name = "apps.einkauf"
    label = "einkauf"
    verbose_name = "Einkauf"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "einkauf",
        "name": "Einkauf",
        "beschreibung": "Anfragen, Bestellungen und Lieferantenmanagement für Rohstoffe, Betriebsmittel und Handelsware",
        "icon": "bi-cart",
        "reihenfolge": 30,
        "url_name": "",
        "geplant": ['Bestellvorschläge aus Mindestbeständen und Bedarfen', 'Anfragen und Preisvergleich', 'Bestellungen mit Freigabestufen', 'Lieferantenbewertung', 'Rahmenverträge'],
    }

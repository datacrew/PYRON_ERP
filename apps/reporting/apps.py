from django.apps import AppConfig


class ReportingConfig(AppConfig):
    """Gerüst – fachliche Umsetzung folgt laut docs/ROADMAP.md."""

    name = "apps.reporting"
    label = "reporting"
    verbose_name = "Reporting"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "reporting",
        "name": "Reporting",
        "beschreibung": "Auswertungen und Kennzahlen über alle Module",
        "icon": "bi-bar-chart",
        "reihenfolge": 85,
        "url_name": "",
        "geplant": ['Umsatz-, Deckungsbeitrags- und Lieferantenauswertungen', 'Produktionskennzahlen (OEE, Akkord, Ausschuss)', 'Liquiditätsübersicht', 'Export nach Excel/CSV', 'Benutzerdefinierte Berichte'],
    }

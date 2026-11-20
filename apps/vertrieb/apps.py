from django.apps import AppConfig


class VertriebConfig(AppConfig):
    """Gerüst – fachliche Umsetzung folgt laut docs/ROADMAP.md."""

    name = "apps.vertrieb"
    label = "vertrieb"
    verbose_name = "Vertrieb"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "vertrieb",
        "name": "Vertrieb",
        "beschreibung": "CRM, Angebote, Aufträge, EDI/EDIFACT, Onlineshop-Schnittstelle, ZUGFeRD-Rechnung",
        "icon": "bi-graph-up-arrow",
        "reihenfolge": 60,
        "url_name": "",
        "geplant": ['CRM: Leads, Kontakthistorie, Wiedervorlagen', 'Auftragsabwicklung Angebot → Auftrag → Lieferschein → Rechnung', 'EDIFACT ORDERS/DESADV/INVOIC', 'REST-API für Onlineshops (Basis vorhanden)', 'E-Rechnung ZUGFeRD 2.x / XRechnung'],
    }

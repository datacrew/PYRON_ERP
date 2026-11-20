from django.apps import AppConfig


class VertriebConfig(AppConfig):
    """Phase 5 – Vertrieb und Versand."""

    name = "apps.vertrieb"
    label = "vertrieb"
    verbose_name = "Vertrieb"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "vertrieb",
        "name": "Vertrieb",
        "beschreibung": "CRM (Leads, Aktivitäten, Pipeline), Angebot → Auftrag → Lieferschein → Rechnung, Kundenpreise, ZUGFeRD/XRechnung",
        "icon": "bi-graph-up-arrow",
        "reihenfolge": 60,
        "url_name": "vertrieb:index",
        "geplant": [],
    }

from django.apps import AppConfig


class ReportingConfig(AppConfig):
    """Phase 6 – Reporting: Bericht-Registry, Standardberichte, Management-Cockpit, Exporte."""

    name = "apps.reporting"
    label = "reporting"
    verbose_name = "Reporting"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "reporting",
        "name": "Reporting",
        "beschreibung": "Management-Cockpit, Standardberichte aller Module, Export nach Excel, CSV und PDF",
        "icon": "bi-bar-chart",
        "reihenfolge": 85,
        "url_name": "reporting:index",
        "geplant": [],
    }

    def ready(self):
        from django.utils.module_loading import autodiscover_modules

        autodiscover_modules("berichte")

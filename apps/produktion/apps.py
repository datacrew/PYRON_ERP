from django.apps import AppConfig


class ProduktionConfig(AppConfig):
    name = "apps.produktion"
    label = "produktion"
    verbose_name = "Produktion"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "produktion",
        "name": "Produktion",
        "beschreibung": "Fertigungsaufträge mit Materialbedarf und Fehlteilen, Laufkarten, Rückmeldungen, "
                        "Materialentnahme, Fertigmeldung und Nachkalkulation",
        "icon": "bi-gear-wide-connected",
        "reihenfolge": 50,
        "url_name": "produktion:index",
        "dashboard": "apps.produktion.views.dashboard_hinweise",
        "geplant": [],
    }

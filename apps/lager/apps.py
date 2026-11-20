from django.apps import AppConfig


class LagerConfig(AppConfig):
    name = "apps.lager"
    label = "lager"
    verbose_name = "Lager"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "lager",
        "name": "Lager",
        "beschreibung": "Lagerorte, Bestände je Charge, Bewegungsjournal, Umbuchung, Inventur, Bewertung",
        "icon": "bi-boxes",
        "reihenfolge": 45,
        "url_name": "lager:uebersicht",
        "dashboard": "apps.lager.views.dashboard_hinweise",
    }

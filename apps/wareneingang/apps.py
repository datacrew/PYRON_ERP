from django.apps import AppConfig


class WareneingangConfig(AppConfig):
    name = "apps.wareneingang"
    label = "wareneingang"
    verbose_name = "Wareneingang"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "wareneingang",
        "name": "Wareneingang",
        "beschreibung": "Wareneingang gegen Bestellung mit Chargenerfassung, Qualitätsprüfung und Sperrbestand",
        "icon": "bi-box-arrow-in-down",
        "reihenfolge": 40,
        "url_name": "wareneingang:index",
        "geplant": [],
    }

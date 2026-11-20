from django.apps import AppConfig


class StammdatenConfig(AppConfig):
    name = "apps.stammdaten"
    label = "stammdaten"
    verbose_name = "Stammdaten"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "stammdaten",
        "name": "Stammdaten",
        "beschreibung": "Artikel, Geschäftspartner, Stücklisten, Arbeitspläne, Kontenrahmen, Steuersätze",
        "icon": "bi-database",
        "reihenfolge": 10,
        "url_name": "stammdaten:index",
    }

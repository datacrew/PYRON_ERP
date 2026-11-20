from django.apps import AppConfig


class FibuConfig(AppConfig):
    """Gerüst – fachliche Umsetzung folgt laut docs/ROADMAP.md."""

    name = "apps.fibu"
    label = "fibu"
    verbose_name = "Finanzbuchhaltung"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "fibu",
        "name": "Finanzbuchhaltung",
        "beschreibung": "Doppelte Buchführung, Kontoauszüge, Mahnwesen, Bilanz",
        "icon": "bi-bank",
        "reihenfolge": 80,
        "url_name": "",
        "geplant": ['Journal und Hauptbuch (SKR03/SKR04)', 'Automatische Buchung festgeschriebener Belege (Ereignis vorhanden)', 'Import CAMT.053 / MT940 mit Zuordnungsregeln', 'Offene Posten und Mahnwesen', 'UStVA, Bilanz, GuV, DATEV-Export'],
    }

    def ready(self):
        from . import ereignisse  # noqa: F401 – Ereignis-Abonnements registrieren

from django.apps import AppConfig


class WareneingangConfig(AppConfig):
    """Gerüst – fachliche Umsetzung folgt laut docs/ROADMAP.md."""

    name = "apps.wareneingang"
    label = "wareneingang"
    verbose_name = "Wareneingang"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "wareneingang",
        "name": "Wareneingang",
        "beschreibung": "Verbuchung und Qualitätskontrolle gelieferter Waren",
        "icon": "bi-box-arrow-in-down",
        "reihenfolge": 40,
        "url_name": "",
        "geplant": ['Wareneingang gegen Bestellung', 'Chargenerfassung und Etiketten', 'Prüfprotokolle / Sperrbestand', 'Reklamationen an Lieferanten', 'Lagerbestandsführung'],
    }

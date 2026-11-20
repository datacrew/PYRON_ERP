from django.apps import AppConfig


class ProduktionConfig(AppConfig):
    """Gerüst – fachliche Umsetzung folgt laut docs/ROADMAP.md."""

    name = "apps.produktion"
    label = "produktion"
    verbose_name = "Produktion"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "produktion",
        "name": "Produktion",
        "beschreibung": "Fertigungsaufträge, Stempelzeiten, Stückzahlen und Akkordlohn",
        "icon": "bi-gear-wide-connected",
        "reihenfolge": 50,
        "url_name": "",
        "geplant": ['Fertigungsaufträge aus Stücklisten und Arbeitsplänen', 'BDE-Terminal: Kommen/Gehen, An-/Abmelden am Arbeitsgang', 'Stückzahl- und Ausschussmeldung', 'Akkordberechnung (Vorgabezeit vs. Istzeit, Leistungsgrad)', 'Materialentnahme, Nachkalkulation'],
    }

from django.apps import AppConfig


class BelegeConfig(AppConfig):
    name = "apps.belege"
    label = "belege"
    verbose_name = "Belege"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "belege",
        "name": "Belege",
        "beschreibung": "Angebote, Aufträge, Lieferscheine, Rechnungen, Bestellungen – GoBD-konform mit PDF-Archiv",
        "icon": "bi-file-earmark-text",
        "reihenfolge": 20,
        "url_name": "belege:liste",
    }

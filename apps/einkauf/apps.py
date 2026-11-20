from django.apps import AppConfig


class EinkaufConfig(AppConfig):
    name = "apps.einkauf"
    label = "einkauf"
    verbose_name = "Einkauf"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "einkauf",
        "name": "Einkauf",
        "beschreibung": "Bestellanforderungen, Anfragen und Bestellungen an Lieferanten, Liefertermine, "
                        "Bestellvorschläge aus Meldebeständen",
        "icon": "bi-cart",
        "reihenfolge": 30,
        "url_name": "einkauf:index",
        "geplant": [],
    }

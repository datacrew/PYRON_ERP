"""Rechte-Modul „Shop-API“: lesen = Shops/Bestellungen ansehen, anlegen/aendern = Shops anlegen und
API-Token erzeugen. Die Shops selbst authentifizieren sich mit ihrem Token (nicht über Benutzerrechte).
Die Oberfläche liegt in apps.vertrieb; dieses Modul existiert nur für die getrennte Rechtevergabe."""
from django.apps import AppConfig


class ShopapiConfig(AppConfig):
    name = "apps.shopapi"
    label = "shopapi"
    verbose_name = "Shop-API"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "shopapi",
        "name": "Shop-API",
        "beschreibung": "Onlineshops, API-Token, eingegangene Shop-Bestellungen",
        "icon": "bi-cart",
        "reihenfolge": 64,
        "url_name": "vertrieb:shops",
        "geplant": [],
    }

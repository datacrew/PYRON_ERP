from django.apps import AppConfig


class FibuConfig(AppConfig):
    """Finanzbuchhaltung (Phase 4): Hauptbuch, Nebenbücher, Bankimport, Mahnwesen, Auswertungen.

Rechte: lesen = ansehen, anlegen = buchen, aendern = Fibu-Einstellungen, freigeben = festschreiben."""

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
        "url_name": "fibu:index",
        "dashboard": "apps.fibu.views.dashboard_hinweise",
        "geplant": [],
    }

    def ready(self):
        from . import ereignisse  # noqa: F401 – Ereignis-Abonnements registrieren

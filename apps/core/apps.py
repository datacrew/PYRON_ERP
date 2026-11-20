from django.apps import AppConfig
from django.db.models.signals import post_migrate


class CoreConfig(AppConfig):
    name = "apps.core"
    label = "core"
    verbose_name = "Basis & Verwaltung"
    default_auto_field = "django.db.models.BigAutoField"
    # Jede ERP-App beschreibt sich über `erp_modul`; die Registry liest diese Angaben aus.
    erp_modul = {
        "code": "verwaltung",
        "name": "Verwaltung",
        "beschreibung": "Benutzer, Rollen & Rechte, Module, Nummernkreise, Vorlagen, Audit-Log",
        "icon": "bi-gear",
        "reihenfolge": 90,
        "url_name": "core:verwaltung",
        "pflicht": True,
    }

    def ready(self):
        from . import signals  # noqa: F401 – Audit-Log-Empfänger registrieren
        from .registry import module_synchronisieren

        post_migrate.connect(module_synchronisieren, sender=self, dispatch_uid="erp_module_sync")

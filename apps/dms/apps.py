from django.apps import AppConfig


class DmsConfig(AppConfig):
    """Dokumenten-Management (DMS) mit OCR, Stapelverarbeitung und Belegerkennung für Eingangsrechnungen.

Rechte (Modul „dms“):
* lesen = Dokumente ansehen und durchsuchen
* anlegen = hochladen, verarbeiten (OCR/Erkennung), trennen, erkannte Felder korrigieren
* aendern = Metadaten/Tags pflegen, archivieren, Lernregeln pflegen
* freigeben = erkannte Eingangsrechnung verbuchen (zusätzlich Recht fibu/anlegen nötig)
* loeschen = noch nicht archivierte Dokumente löschen (archivierte sind unlöschbar)
"""

    name = "apps.dms"
    label = "dms"
    verbose_name = "Dokumentenmanagement"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "dms",
        "name": "Dokumente (DMS)",
        "beschreibung": "Revisionssichere Dokumentenablage mit OCR, Volltextsuche, Stapelverarbeitung und "
                        "automatischer Erkennung von Eingangsrechnungen",
        "icon": "bi-archive",
        "reihenfolge": 86,
        "url_name": "dms:index",
        "geplant": [],
    }

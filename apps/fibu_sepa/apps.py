"""SEPA-Zahlungsverkehr der Finanzbuchhaltung: Überweisungen (pain.001.001.09) und Lastschriften
(pain.008.001.08) inkl. Mandatsverwaltung, Geldtransit-Buchung, CAMT-Abgleich und Rücklastschriften.

Rechte (Modul „fibu_sepa“): lesen = ansehen und XML herunterladen, anlegen = Zahlungs-/Lastschriftlauf
vorschlagen und Posten auswählen, aendern = SEPA-Mandate pflegen, freigeben = Lauf freigeben, XML erzeugen und
buchen bzw. stornieren, loeschen = Entwurf verwerfen. Abgleich und Rücklastschriften laufen über das Recht
„anlegen“ im Modul Bankimport (fibu_bank).
"""
from django.apps import AppConfig


class FibuSepaConfig(AppConfig):
    name = "apps.fibu_sepa"
    label = "fibu_sepa"
    verbose_name = "SEPA-Zahlungsverkehr"
    default_auto_field = "django.db.models.BigAutoField"
    erp_modul = {
        "code": "fibu_sepa",
        "name": "SEPA-Zahlungsverkehr",
        "beschreibung": "SEPA-Mandate, Überweisungs- und Lastschriftläufe, XML-Export (pain.001/pain.008)",
        "icon": "bi-bank2",
        "reihenfolge": 84,
        "url_name": "fibu_sepa:laeufe",
        "geplant": [],
    }

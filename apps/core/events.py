"""Einfacher Ereignisbus für die Kommunikation zwischen Modulen (auf Basis von Django-Signals).

Module kennen sich so nicht direkt: Das Belegmodul veröffentlicht z. B. „beleg.festgeschrieben“,
die Finanzbuchhaltung abonniert das Ereignis und erzeugt (ab Phase 4) die Buchung.

    from apps.core.events import abonnieren, veroeffentlichen

    @abonnieren("beleg.festgeschrieben")
    def buchen(beleg, benutzer=None, **kwargs): ...

    veroeffentlichen("beleg.festgeschrieben", beleg=beleg, benutzer=user)
"""
import logging

from django.dispatch import Signal

logger = logging.getLogger("apps.core.events")
_ereignis = Signal()

# Bekannte Ereignisse (Dokumentation; weitere dürfen frei ergänzt werden)
EREIGNISSE = {
    "beleg.freigegeben": "Beleg wurde freigegeben und hat seine Nummer erhalten",
    "beleg.festgeschrieben": "Beleg wurde GoBD-konform festgeschrieben und archiviert",
    "beleg.storniert": "Beleg wurde storniert",
    "lager.bewegung": "Lagerbewegung gebucht (bewegung, benutzer)",
    "bestand.unterschritten": "Frei verfügbarer Bestand unter Meldebestand (artikel, verfuegbar, meldebestand)",
    "inventur.gebucht": "Inventur gebucht (inventur, benutzer)",
    "bestellung.freigegeben": "Einkaufsbestellung/Anfrage freigegeben, Nummer vergeben (bestellung, benutzer)",
    "bestellung.storniert": "Einkaufsbestellung storniert (bestellung, benutzer)",
    "bestellung.status": "Lieferstatus einer Bestellung geändert (bestellung, status)",
    "wareneingang.gebucht": "Wareneingang gebucht – Grundlage für spätere Rechnungsprüfung / WE-RE-Verrechnung "
                            "(wareneingang, bestellung, wert, benutzer)",
    "produktion.freigegeben": "Fertigungsauftrag freigegeben, Material und Arbeitsgänge kopiert (auftrag, benutzer)",
    "produktion.material_entnommen": "Material zum Fertigungsauftrag entnommen (auftrag, entnahmen, wert, benutzer)",
    "produktion.rueckmeldung": "Rückmeldung Gut/Ausschuss/Nacharbeit zu einem Arbeitsgang (rueckmeldung)",
    "produktion.fertiggemeldet": "Fertigmeldung mit Lagerzugang zu Herstellkosten (auftrag, fertigmeldung, wert, "
                                 "benutzer)",
    "wareneingang.qs_entschieden": "QS-Entscheid zu einer WE-Position (position, entscheid, ruecksendung, benutzer)",
    "vertrieb.lead_umgewandelt": "Lead in Kunde umgewandelt (lead, partner, benutzer)",
    "vertrieb.auftrag_angelegt": "Kundenauftrag angelegt (auftrag, benutzer)",
    "vertrieb.auftrag_bestaetigt": "Auftragsbestätigung festgeschrieben (auftrag, benutzer)",
    "vertrieb.lieferung_gebucht": "Lieferschein: Lagerabgang gebucht und festgeschrieben (lieferung, benutzer)",
    "vertrieb.rechnung_erstellt": "Rechnung/Sammelrechnung aus Lieferscheinen erstellt (rechnung, lieferungen, benutzer)",
    "versand.tour_verladen": "Tour verladen, Lieferscheine unterwegs (tour, benutzer)",
    "versand.zugestellt": "Lieferschein zugestellt (lieferung, benutzer)",
    "versand.lvs_rueckmeldung": "Kommissionier-Rückmeldung aus dem LVS verarbeitet (lieferung, benutzer)",
}


def veroeffentlichen(name, **daten):
    logger.debug("Ereignis %s", name)
    return _ereignis.send(sender=name, **daten)


def abonnieren(name):
    def deco(fn):
        def empfaenger(sender, **kwargs):
            if sender == name:
                return fn(**kwargs)

        _ereignis.connect(empfaenger, weak=False, dispatch_uid=f"{name}:{fn.__module__}.{fn.__qualname__}")
        return fn

    return deco

"""Verbuchung freigegebener Eingangsrechnungen über die bestehende Fibu (keine Parallelstruktur).

Ablauf in einer Transaktion:
1. Plausibilität erneut prüfen (Dubletten können inzwischen entstanden sein) – Fehler verhindern die Buchung.
2. `fibu.Eingangsrechnung` + `ERPosition`en anlegen (Nummer aus Nummernkreis FIBU_ER, Steuerschlüssel und
   Aufwandskonto aus der Prüfmaske).
   Mit Bestellbezug: Dreifach-Abgleich (abgleich.py) neu rechnen, Abweichungen nur mit Begründung + Recht;
   Positionen werden den Bestellpositionen zugeordnet und gegen das WE/RE-Konto gebucht.
3. Summenabgleich Fibu ↔ Rechnung (Brutto muss übereinstimmen).
4. `fibu.services.er_buchen` bucht Aufwand + Vorsteuer an Kreditor und legt den offenen Posten an.
5. Rückverweis Dokument ↔ Eingangsrechnung/Buchung, Lernregeln aktualisieren, Dokument revisionssicher
   archivieren (GoBD).
"""
import re

from django.db import transaction
from django.utils import timezone

from apps.core.nummernkreise import naechste_nummer
from apps.einkauf.models import BestellPosition
from apps.fibu import services as F
from apps.fibu.models import Eingangsrechnung
from apps.stammdaten.models import Geschaeftspartner

from . import abgleich as A
from . import erkennung as E
from . import pruefung as P
from . import services as S
from .models import Dokument, Kontierungsregel

STOPPWOERTER = {"und", "oder", "für", "fuer", "mit", "ohne", "inkl", "stück", "stk", "pauschal"}


def _stichwort(beschreibung):
    """Erstes aussagekräftiges Wort der Positionsbeschreibung (für gelernte Stichwortregeln)."""
    for wort in re.findall(r"[A-Za-zÄÖÜäöüß]{4,}", beschreibung or ""):
        if wort.lower() not in STOPPWOERTER:
            return wort.lower()[:60]
    return ""


def lernen(partner, positionen):
    """Lernregeln aktualisieren: Konto je Lieferant (allgemein) und – bei manueller Kontierung – je Stichwort."""
    jetzt = timezone.now()
    for p in positionen:
        if not p.get("konto"):
            continue
        konto = F.konto(p["konto"])
        stichworte = [""]
        if p.get("kontoquelle") == "manuell" and _stichwort(p.get("beschreibung")):
            stichworte.append(_stichwort(p.get("beschreibung")))
        for sw in stichworte:
            regel, _ = Kontierungsregel.objects.get_or_create(partner=partner, stichwort=sw, konto=konto,
                                                              steuerschluessel="", defaults={"gelernt": True})
            regel.anzahl += 1
            regel.zuletzt = jetzt
            regel.save(update_fields=["anzahl", "zuletzt"])


def _abgleich_pruefen(erg, dokument, partner, benutzer, begruendung):
    """Dreifach-Abgleich unmittelbar vor der Buchung neu rechnen; Abweichung nur mit Begründung + Recht."""
    abgleich = A.berechnen(erg, dokument, partner)
    if not abgleich.get("bestellung_id") or not abgleich["abweichung"] or abgleich.get("freigabe"):
        return abgleich, None
    begruendung = (begruendung or "").strip()
    if not begruendung:
        raise S.DmsFehler("Dreifach-Abgleich: Abweichung zu Bestellung/Wareneingang außerhalb der Toleranz – "
                          "Verbuchung nur mit Begründung (und Freigaberecht).")
    if len(begruendung) < 10:
        raise S.DmsFehler("Dreifach-Abgleich: Bitte die Freigabe der Abweichung nachvollziehbar begründen "
                          "(mindestens 10 Zeichen).")
    # Ohne Benutzer (Systemaufruf, z. B. Demodaten) genügt die Begründung
    if benutzer is not None and not A.darf_abweichung_freigeben(benutzer):
        raise S.DmsFehler("Dreifach-Abgleich: Für die Freigabe einer Abweichung fehlt das Recht (Einkauf oder Fibu "
                          "„freigeben“).")
    A.freigabe_erteilen(abgleich, benutzer, begruendung)
    return abgleich, begruendung


def verbuchen(dokument, benutzer=None, begruendung=""):
    """Erkannte und geprüfte Eingangsrechnung verbuchen → fibu.Eingangsrechnung.

    Mit Bestellbezug (Dreifach-Abgleich) werden Eingangsrechnung ↔ Bestellung und Positionen ↔ Bestellpositionen
    verknüpft; Abweichungen außerhalb der Toleranz erfordern `begruendung` und ein Freigaberecht.
    """
    with transaction.atomic():
        dokument = Dokument.objects.select_for_update().get(pk=dokument.pk)
        if dokument.eingangsrechnung_id:
            raise S.DmsFehler(f"{dokument.nummer} ist bereits als {dokument.eingangsrechnung.nummer} verbucht.")
        if not P.bearbeitbar(dokument):
            raise S.DmsFehler(f"{dokument.nummer} kann im Status „{dokument.get_status_display()}“ nicht verbucht "
                              "werden.")
        erg = dict(dokument.erkennung or {})
        erg["plausibilitaet"] = E.plausibilitaet(erg, dokument)
        fehler = P.fehler(erg)
        if fehler:
            Dokument.objects.filter(pk=dokument.pk).update(erkennung=erg, status=Dokument.Status.PRUEFEN)
            raise S.DmsFehler("Verbuchung nicht möglich: " + " ".join(fehler))
        f = erg["felder"]
        partner = Geschaeftspartner.objects.get(pk=erg["lieferant"]["partner_id"])
        rechnungsdatum = E.feldwert(f, "rechnungsdatum", "datum")
        brutto = E.feldwert(f, "brutto", "betrag")
        abgleich, freigabe = _abgleich_pruefen(erg, dokument, partner, benutzer, begruendung)
        bezug = {z["index"]: z["bestellposition_id"] for z in abgleich.get("positionen", [])}
        bestellpositionen = {bp.pk: bp for bp in BestellPosition.objects.filter(pk__in=bezug.values())
                             .select_related("artikel")}
        try:
            er = Eingangsrechnung.objects.create(
                nummer=naechste_nummer("FIBU_ER", timezone.localdate()), lieferant=partner,
                rechnungsnummer=E.feldwert(f, "rechnungsnummer")[:60], rechnungsdatum=rechnungsdatum,
                eingangsdatum=timezone.localdate(dokument.erstellt_am),
                faellig_am=E.feldwert(f, "faellig_am", "datum"), zahlungsbedingung=partner.zahlungsbedingung,
                bestellung_id=abgleich.get("bestellung_id"),
                # Prüfung gegen Bestellung/WE ist im DMS erfolgt (Toleranzen bzw. begründete Freigabe)
                abweichung_akzeptiert=bool(abgleich.get("bestellung_id")),
                erfasst_von=benutzer if getattr(benutzer, "pk", None) else None)
            for i, p in enumerate(erg["positionen"], start=1):
                bp = bestellpositionen.get(bezug.get(i - 1))
                er.positionen.create(
                    position=i * 10, bezeichnung=(p.get("beschreibung") or f"Position {i}")[:200],
                    bestellposition=bp, artikel=bp.artikel if bp else None,
                    menge=E.zahl(p["menge"]), einzelpreis=E.zahl(p["einzelpreis"]),
                    steuerschluessel=F.steuerschluessel(p["steuerschluessel"]),
                    konto=F.konto(p["konto"]) if p.get("konto") and not bp else None)
            F.er_summen(er)
            if brutto is not None and abs(er.summe_brutto - brutto) > E.CENT:
                raise S.DmsFehler(f"Summenabgleich: die Fibu errechnet brutto {er.summe_brutto} €, die Rechnung "
                                  f"weist {brutto} € aus (Rundung/Steuerschlüssel prüfen).")
            buchung = F.er_buchen(er, benutzer)
        except F.FibuFehler as exc:
            raise S.DmsFehler(f"Fibu: {exc}") from exc
        er.refresh_from_db()
        lernen(partner, erg["positionen"])
        erg["verbucht_am"] = timezone.now().isoformat()
        dokument.erkennung = erg
        dokument.eingangsrechnung, dokument.buchung, dokument.partner = er, buchung, partner
        dokument.status = Dokument.Status.GEBUCHT
        dokument.belegdatum = dokument.belegdatum or rechnungsdatum
        dokument.save(update_fields=["erkennung", "eingangsrechnung", "buchung", "partner", "status", "belegdatum"])
        S.protokollieren(dokument, "Verbucht",
                         f"Eingangsrechnung {er.nummer}, Buchung {buchung.nummer}: Brutto {er.summe_brutto} € an "
                         f"Kreditor {partner.name}, offener Posten angelegt."
                         + (f" Bestellbezug {abgleich['bestellnummer']} ({len(bezug)} Position(en) gegen WE/RE)."
                            if abgleich.get("bestellung_id") else ""), benutzer)
        if freigabe:
            S.protokollieren(dokument, "Abweichung freigegeben",
                             f"Dreifach-Abgleich mit Bestellung {abgleich['bestellnummer']}: Abweichung außerhalb der "
                             f"Toleranz freigegeben. Begründung: {freigabe}", benutzer)
        S.archivieren(dokument, benutzer)
    return er

"""Workflow des SEPA-Zahlungsverkehrs: Vorschlag → Freigabe → Export (XML + Geldtransit-Buchung) → ggf. Storno.

Grundsätze:
* Vorschläge nur aus offenen Posten, die in keinem offenen Lauf (Entwurf/freigegeben) stecken.
* Export bucht über die vorhandene Fibu (`op_ausgleichen`) Personenkonto an/von Geldtransit; der Abgleich mit
  dem Kontoauszug bucht später Geldtransit gegen Bank. Geldtransit ist je Kontenrahmen genau ein Konto:
  SKR03 1360, SKR04 1460 (im SKR03 gibt es kein Konto 1460 – daher keine Trennung nach Zahlungsart).
* Nach dem Export ist der Lauf eingefroren (ORM + PostgreSQL-Trigger, Migration 0002). Korrektur nur per Storno
  = Gegenbuchung über `fibu.stornieren`, das die OP-Ausgleiche ebenfalls per Gegen-Ausgleich zurücknimmt.
* Laufnummern ausschließlich über die Nummernkreise SEPA_UEBERWEISUNG / SEPA_LASTSCHRIFT.
"""
import hashlib
from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.bankdaten import iban_pruefen
from apps.core.models import Mandant
from apps.core.nummernkreise import naechste_nummer
from apps.fibu import services as fibu
from apps.fibu.models import OffenerPosten

from . import sepa
from .models import Ruecklastschrift, SepaMandat, Zahlungslauf, ZahlungslaufPosten

GELDTRANSIT = {"SKR03": "1360", "SKR04": "1460"}
VORLAUF_BANKARBEITSTAGE = {"CORE": 1, "B2B": 1}  # seit SEPA-Rulebook 2016 einheitlich D-1 (TARGET2-Arbeitstage)
OFFENE_STATUS = (Zahlungslauf.Status.ENTWURF, Zahlungslauf.Status.FREIGEGEBEN)


class SepaWorkflowFehler(fibu.FibuFehler):
    """Fachlicher Fehler im Zahlungsverkehr (wird dem Benutzer angezeigt)."""


# ------------------------------------------------------------------ Bankarbeitstage (TARGET2-Kalender)

def _ostersonntag(jahr):
    a, b, c = jahr % 19, jahr // 100, jahr % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    monat, tag = divmod(h + l_ - 7 * m + 114, 31)
    return date(jahr, monat, tag + 1)


def ist_bankarbeitstag(d):
    """TARGET2: Mo–Fr außer Neujahr, Karfreitag, Ostermontag, 1. Mai, 25. und 26. Dezember."""
    if d.weekday() >= 5:
        return False
    ostern = _ostersonntag(d.year)
    feiertage = {date(d.year, 1, 1), ostern - timedelta(days=2), ostern + timedelta(days=1), date(d.year, 5, 1),
                 date(d.year, 12, 25), date(d.year, 12, 26)}
    return d not in feiertage


def bankarbeitstag_addieren(d, tage):
    while tage > 0:
        d += timedelta(days=1)
        if ist_bankarbeitstag(d):
            tage -= 1
    return d


def naechster_bankarbeitstag(d):
    while not ist_bankarbeitstag(d):
        d += timedelta(days=1)
    return d


def fruehestes_einzugsdatum(typ="CORE", heute=None):
    """Einreichung heute → frühestes Fälligkeitsdatum = heute + Vorlaufzeit in Bankarbeitstagen."""
    return bankarbeitstag_addieren(heute or timezone.localdate(), VORLAUF_BANKARBEITSTAGE[typ])


# ------------------------------------------------------------------ Hilfsfunktionen

def geldtransit_konto():
    return fibu.konto(GELDTRANSIT.get(fibu.rahmen(), "1360"))


def iban_gueltig(iban):
    try:
        iban_pruefen(iban or "")
        return True
    except ValidationError:
        return False


def _benutzer(benutzer):
    return benutzer if getattr(benutzer, "pk", None) else None


def _ops_in_offenen_laeufen():
    return ZahlungslaufPosten.objects.filter(lauf__status__in=OFFENE_STATUS, ausgewaehlt=True).values("op_id")


def _zweck(op, skonto=None):
    text = f"Rechnung {op.fremdbelegnr or op.belegnr} vom {op.belegdatum:%d.%m.%Y}"
    if skonto:
        text += f" abzgl. Skonto {fibu.euro(skonto)}"
    return sepa.sepa_text(text)


def _summen(lauf):
    posten = list(lauf.ausgewaehlte())
    lauf.summe = sum((p.betrag for p in posten), fibu.q(0))
    lauf.anzahl = len(posten)
    return posten


# ------------------------------------------------------------------ 1. Überweisungsvorschlag

def ueberweisungsvorschlag(bankkonto, ausfuehrungsdatum=None, bis=None, skonto_nutzen=True, benutzer=None):
    """Kreditoren-OPs nach Fälligkeit; Skonto, solange die Skontofrist am Ausführungstag noch läuft.

    Berücksichtigt werden OPs, die bis `bis` fällig sind oder deren Skontofrist bis dahin endet. Partner ohne
    gültige IBAN werden übersprungen (Liste in `lauf.uebersprungen`).
    """
    heute = timezone.localdate()
    datum = naechster_bankarbeitstag(ausfuehrungsdatum or heute)
    if datum < heute:
        raise SepaWorkflowFehler("Das Ausführungsdatum darf nicht in der Vergangenheit liegen.")
    bis = bis or datum
    kandidaten = (OffenerPosten.objects.filter(art=OffenerPosten.Art.KREDITOR, offen__gt=0)
                  .exclude(pk__in=_ops_in_offenen_laeufen()).select_related("partner").order_by("faellig_am", "pk"))
    posten, uebersprungen = [], []
    for op in kandidaten:
        skonto = op.skonto_betrag(datum) if skonto_nutzen else fibu.q(0)
        if op.faellig_am > bis and not (skonto and op.skonto_bis <= bis):
            continue
        if not iban_gueltig(op.partner.iban):
            uebersprungen.append(f"{op.belegnr} {op.partner.name}: keine gültige IBAN")
            continue
        posten.append((op, skonto))
    if not posten:
        raise SepaWorkflowFehler("Keine fälligen Eingangsrechnungen mit gültiger Bankverbindung gefunden.")
    with transaction.atomic():
        lauf = Zahlungslauf.objects.create(
            nummer=naechste_nummer("SEPA_UEBERWEISUNG", heute), art=Zahlungslauf.Art.UEBERWEISUNG,
            bankkonto=bankkonto, ausfuehrungsdatum=datum, beruecksichtigen_bis=bis, skonto_nutzen=skonto_nutzen,
            erstellt_von=_benutzer(benutzer),
            bemerkung=f"{len(uebersprungen)} Posten ohne gültige IBAN übersprungen" if uebersprungen else "")
        for op, skonto in posten:
            ZahlungslaufPosten.objects.create(
                lauf=lauf, op=op, partner=op.partner, name=sepa.sepa_text(op.partner.name, 70),
                iban=iban_pruefen(op.partner.iban), bic=(op.partner.bic or "").replace(" ", "").upper(),
                offen=op.offen, skonto=skonto, betrag=op.offen - skonto, verwendungszweck=_zweck(op, skonto))
        _summen(lauf)
        lauf.save(update_fields=["summe", "anzahl"])
    lauf.uebersprungen = uebersprungen
    return lauf


# ------------------------------------------------------------------ 2. Lastschriftvorschlag

def gueltiges_mandat(partner, stichtag):
    """Jüngstes gültiges (aktiv, unterschrieben, nicht verfallen) Mandat des Kunden oder None."""
    for m in partner.sepa_mandate.filter(status=SepaMandat.Status.AKTIV).order_by("-unterschrieben_am", "-pk"):
        if m.ist_gueltig(stichtag):
            return m
    return None


def lastschriftvorschlag(bankkonto, einzugsdatum=None, bis=None, benutzer=None):
    """Debitoren-OPs von Kunden mit gültigem Mandat; Sequenz (FRST/RCUR/FNAL/OOFF) aus dem Mandat.

    Das Einzugsdatum wird auf das früheste zulässige Datum (Vorlaufzeit in Bankarbeitstagen) angehoben.
    """
    mandant = Mandant.aktuell()
    if not mandant or not (mandant.glaeubiger_id or "").strip():
        raise SepaWorkflowFehler("Für Lastschriften muss die Gläubiger-ID beim Mandanten hinterlegt sein.")
    heute = timezone.localdate()
    fruehestens = max(fruehestes_einzugsdatum(t, heute) for t in VORLAUF_BANKARBEITSTAGE)
    datum = naechster_bankarbeitstag(max(einzugsdatum or fruehestens, fruehestens))
    bis = bis or datum
    kandidaten = (OffenerPosten.objects.filter(art=OffenerPosten.Art.DEBITOR, offen__gt=0, faellig_am__lte=bis)
                  .exclude(pk__in=_ops_in_offenen_laeufen()).select_related("partner").order_by("faellig_am", "pk"))
    posten, uebersprungen, mandate, einmalig_genutzt = [], [], {}, set()
    for op in kandidaten:
        if op.partner_id not in mandate:
            mandate[op.partner_id] = gueltiges_mandat(op.partner, datum)
        m = mandate[op.partner_id]
        if m is None:
            uebersprungen.append(f"{op.belegnr} {op.partner.name}: kein gültiges SEPA-Mandat")
            continue
        if not iban_gueltig(m.iban):
            uebersprungen.append(f"{op.belegnr} {op.partner.name}: IBAN im Mandat ungültig")
            continue
        seq = m.naechste_sequenz()
        if seq in ("OOFF", "FNAL"):  # einmaliger bzw. letzter Einzug: nur ein Posten je Mandat
            if m.pk in einmalig_genutzt:
                uebersprungen.append(f"{op.belegnr} {op.partner.name}: Mandat erlaubt nur einen Einzug ({seq})")
                continue
            einmalig_genutzt.add(m.pk)
        posten.append((op, m, seq))
    if not posten:
        raise SepaWorkflowFehler("Keine fälligen Ausgangsrechnungen von Kunden mit gültigem SEPA-Mandat gefunden.")
    with transaction.atomic():
        lauf = Zahlungslauf.objects.create(
            nummer=naechste_nummer("SEPA_LASTSCHRIFT", heute), art=Zahlungslauf.Art.LASTSCHRIFT,
            bankkonto=bankkonto, ausfuehrungsdatum=datum, beruecksichtigen_bis=bis, skonto_nutzen=False,
            erstellt_von=_benutzer(benutzer),
            bemerkung=f"{len(uebersprungen)} Posten ohne gültiges Mandat übersprungen" if uebersprungen else "")
        for op, m, seq in posten:
            ZahlungslaufPosten.objects.create(
                lauf=lauf, op=op, partner=op.partner, mandat=m, name=sepa.sepa_text(m.name_zahlungspflichtiger, 70),
                iban=m.iban, bic=m.bic, offen=op.offen, betrag=op.offen, verwendungszweck=_zweck(op),
                sequenz=seq, mandatsreferenz=m.mandatsreferenz, mandat_datum=m.unterschrieben_am, lastschrift_typ=m.typ)
        _summen(lauf)
        lauf.save(update_fields=["summe", "anzahl"])
    lauf.uebersprungen = uebersprungen
    return lauf


# ------------------------------------------------------------------ 3. Freigabe und Export

def freigeben(lauf, benutzer):
    if not _benutzer(benutzer):
        raise SepaWorkflowFehler("Die Freigabe erfordert einen angemeldeten Benutzer.")
    with transaction.atomic():
        lauf = Zahlungslauf.objects.select_for_update().get(pk=lauf.pk)
        if lauf.status != Zahlungslauf.Status.ENTWURF:
            raise SepaWorkflowFehler(f"Lauf {lauf.nummer} ist nicht im Entwurf ({lauf.get_status_display()}).")
        if not _summen(lauf):
            raise SepaWorkflowFehler("Der Lauf enthält keine ausgewählten Posten.")
        lauf.status = Zahlungslauf.Status.FREIGEGEBEN
        lauf.freigegeben_von, lauf.freigegeben_am = benutzer, timezone.now()
        lauf.save()
    return lauf


def _xml_erzeugen(lauf, posten):
    m = Mandant.aktuell()
    bk = lauf.bankkonto
    name = m.name if m else bk.bezeichnung
    if lauf.ist_lastschrift:
        tx = [{"e2e": p.end_to_end_id, "betrag": p.betrag, "name": p.name, "iban": p.iban, "bic": p.bic,
               "zweck": p.verwendungszweck, "mandat": p.mandatsreferenz, "mandat_datum": p.mandat_datum,
               "typ": p.lastschrift_typ, "sequenz": p.sequenz} for p in posten]
        return sepa.pain008(lauf.msg_id, name, m.glaeubiger_id, bk.iban, bk.bic, lauf.ausfuehrungsdatum, tx,
                            lauf.sammelbuchung), "pain.008"
    tx = [{"e2e": p.end_to_end_id, "betrag": p.betrag, "name": p.name, "iban": p.iban, "bic": p.bic,
           "zweck": p.verwendungszweck} for p in posten]
    return sepa.pain001(lauf.msg_id, f"{lauf.msg_id}-1", name, bk.iban, bk.bic, lauf.ausfuehrungsdatum, tx,
                        lauf.sammelbuchung), "pain.001"


def exportieren(lauf, benutzer):
    """XML erzeugen und prüfen, OPs gegen Geldtransit ausgleichen, Lauf einfrieren, Mandate fortschreiben."""
    if not _benutzer(benutzer):
        raise SepaWorkflowFehler("Der Export erfordert einen angemeldeten Benutzer.")
    with transaction.atomic():
        lauf = Zahlungslauf.objects.select_for_update().select_related("bankkonto").get(pk=lauf.pk)
        if lauf.status != Zahlungslauf.Status.FREIGEGEBEN:
            raise SepaWorkflowFehler(f"Lauf {lauf.nummer} muss vor dem Export freigegeben sein.")
        posten = list(lauf.ausgewaehlte().select_related("op", "mandat").order_by("pk"))
        if not posten:
            raise SepaWorkflowFehler("Der Lauf enthält keine ausgewählten Posten.")
        if lauf.ist_lastschrift and not (getattr(Mandant.aktuell(), "glaeubiger_id", "") or "").strip():
            raise SepaWorkflowFehler("Für Lastschriften muss die Gläubiger-ID beim Mandanten hinterlegt sein.")
        for p in posten:
            op = OffenerPosten.objects.select_for_update().get(pk=p.op_id)
            if op.offen != p.offen:
                raise SepaWorkflowFehler(f"OP {op.belegnr} wurde seit dem Vorschlag verändert – bitte neu vorschlagen.")
            if p.mandat_id and not p.mandat.ist_gueltig(lauf.ausfuehrungsdatum):
                raise SepaWorkflowFehler(f"Mandat {p.mandatsreferenz} ist nicht mehr gültig.")
        lauf.msg_id = sepa.sepa_id(lauf.nummer)
        for i, p in enumerate(posten, 1):  # IDs vor dem Einfrieren setzen
            p.end_to_end_id = sepa.sepa_id(f"{lauf.nummer}-{i}")
            p.pmtinf_id = sepa.sepa_id(f"{lauf.msg_id}-{p.lastschrift_typ}-{p.sequenz}" if lauf.ist_lastschrift
                                       else f"{lauf.msg_id}-1")
            p.save(update_fields=["end_to_end_id", "pmtinf_id"])
        xml, schema = _xml_erzeugen(lauf, posten)
        fehler = sepa.xsd_pruefen(xml, schema)
        if fehler:
            raise SepaWorkflowFehler(f"XML entspricht nicht dem Schema {schema}: {'; '.join(fehler[:3])}")
        summe = sum((p.betrag for p in posten), fibu.q(0))
        art = "Lastschrifteinzug" if lauf.ist_lastschrift else "Überweisung"
        buchung = fibu.op_ausgleichen(
            [p.op for p in posten], summe, lauf.ausfuehrungsdatum, geldtransit_konto(), benutzer=benutzer,
            belegnr=lauf.nummer, text=f"SEPA-{art} {lauf.nummer}", quelle="sepa_lauf", quelle_id=lauf.pk,
            skonto=lauf.skonto_nutzen)
        lauf.xml_inhalt = xml.decode("utf-8")
        lauf.xml_dateiname = f"{lauf.nummer}_{'pain.008.001.08' if lauf.ist_lastschrift else 'pain.001.001.09'}.xml"
        lauf.xml_sha256 = hashlib.sha256(xml).hexdigest()
        lauf.xsd_geprueft = True
        lauf.summe, lauf.anzahl, lauf.buchung = summe, len(posten), buchung
        lauf.status = Zahlungslauf.Status.EXPORTIERT
        lauf.exportiert_von, lauf.exportiert_am = benutzer, timezone.now()
        lauf._export = True
        try:
            lauf.save()
        finally:
            lauf._export = False  # ORM-Sperre für das zurückgegebene Objekt sofort wieder aktiv
        for p in posten:
            if p.mandat_id:  # Folgeeinzüge laufen ab jetzt als RCUR (naechste_sequenz → wurde_genutzt)
                m = SepaMandat.objects.select_for_update().get(pk=p.mandat_id)
                m.letzte_nutzung = lauf.ausfuehrungsdatum
                if p.sequenz in ("OOFF", "FNAL"):
                    m.status = SepaMandat.Status.BEENDET
                m.save(update_fields=["letzte_nutzung", "status"])
    return lauf


# ------------------------------------------------------------------ 4. Storno

def stornieren(lauf, benutzer, grund=""):
    """Storno eines exportierten Laufs per Gegenbuchung; OP-Ausgleiche werden per Gegen-Ausgleich zurückgenommen.

    Der Lauf selbst bleibt unverändert erhalten (nur Status/Storno-Verweis), die XML-Datei bleibt archiviert.
    """
    if not _benutzer(benutzer):
        raise SepaWorkflowFehler("Das Storno erfordert einen angemeldeten Benutzer.")
    with transaction.atomic():
        lauf = Zahlungslauf.objects.select_for_update().get(pk=lauf.pk)
        if lauf.status != Zahlungslauf.Status.EXPORTIERT or not lauf.buchung_id:
            raise SepaWorkflowFehler(f"Nur exportierte, noch nicht abgeglichene Läufe können storniert werden "
                                     f"({lauf.nummer}: {lauf.get_status_display()}).")
        if lauf.posten.exclude(status=ZahlungslaufPosten.Status.OFFEN).exists():
            raise SepaWorkflowFehler("Posten sind bereits abgeglichen bzw. zurückgegeben – Storno nicht möglich.")
        st = fibu.stornieren(lauf.buchung, benutzer, grund=grund or f"SEPA-Lauf {lauf.nummer} storniert")
        lauf.storno_buchung, lauf.status = st, Zahlungslauf.Status.STORNIERT
        lauf.storniert_von, lauf.storniert_am = benutzer, timezone.now()
        lauf.bemerkung = (grund or lauf.bemerkung)[:200]
        lauf.save()
        for p in lauf.ausgewaehlte().filter(mandat__isnull=False):
            m = SepaMandat.objects.select_for_update().get(pk=p.mandat_id)
            letzte = (ZahlungslaufPosten.objects.filter(mandat=m, ausgewaehlt=True, lauf__exportiert_am__isnull=False)
                      .exclude(lauf__status=Zahlungslauf.Status.STORNIERT).order_by("-lauf__ausfuehrungsdatum")
                      .values_list("lauf__ausfuehrungsdatum", flat=True).first())
            m.letzte_nutzung = letzte
            if p.sequenz in ("OOFF", "FNAL") and m.status == SepaMandat.Status.BEENDET:
                m.status = SepaMandat.Status.AKTIV
            m.save(update_fields=["letzte_nutzung", "status"])
    return lauf


# ------------------------------------------------------------------ 6. Rückgabegründe → Mandat

def rueckgabe_mandat_behandeln(mandat, grund, datum=None):
    """Mandat nach Rücklastschrift sperren (MANDAT_SPERREN) oder nur vermerken (MANDAT_VERMERKEN).

    Liefert "gesperrt", "vermerkt" oder "" (kein Einfluss auf das Mandat, z. B. AM04 mangels Deckung).
    """
    grund = (grund or "").upper()
    datum = datum or timezone.localdate()
    if grund in Ruecklastschrift.MANDAT_SPERREN:
        ergebnis = "gesperrt"
        mandat.status = SepaMandat.Status.GESPERRT
    elif grund in Ruecklastschrift.MANDAT_VERMERKEN:
        ergebnis = "vermerkt"
    else:
        return ""
    notiz = f"{datum:%d.%m.%Y} Rückgabe {grund} ({Ruecklastschrift.GRUENDE.get(grund, '')})"
    mandat.bemerkung = f"{notiz} | {mandat.bemerkung}"[:200] if mandat.bemerkung else notiz[:200]
    mandat.save(update_fields=["status", "bemerkung"])
    return ergebnis

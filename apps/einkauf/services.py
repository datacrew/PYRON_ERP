"""Workflow des Einkaufs: Freigabe (Nummernvergabe), Storno, Statusfortschreibung, Bestellvorschläge."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from apps.belege.services import _partner_snapshot
from apps.core.events import veroeffentlichen
from apps.core.nummernkreise import naechste_nummer

from .models import Bestellanforderung, Bestellung, BestellPosition, EinkaufFehler

S = Bestellung.Status
NUMMERNKREIS = {Bestellung.Typ.ANFRAGE: "EK_ANFRAGE", Bestellung.Typ.BESTELLUNG: "EK_BESTELLUNG"}


def _benutzer(u):
    return u if getattr(u, "pk", None) else None


def position_vorbelegen(pos):
    """Übernimmt leere Felder (Bezeichnung, Einheit, Preis, USt) aus dem Artikelstamm."""
    a = pos.artikel
    pos.bezeichnung = pos.bezeichnung or a.bezeichnung
    pos.einheit = pos.einheit or a.einheit.code
    if pos.einzelpreis in (None, ""):
        pos.einzelpreis = a.einkaufspreis
    if pos.steuersatz in (None, ""):
        pos.steuersatz = a.steuersatz.satz
    return pos


def neu_nummerieren(bestellung):
    for i, pos in enumerate(bestellung.positionen.order_by("position", "pk"), start=1):
        if pos.position != i * 10:
            pos.position = i * 10
            pos.save(update_fields=["position"])


def freigeben(bestellung, benutzer=None):
    """ENTWURF → BESTELLT (bzw. ANGEFRAGT bei Anfragen); vergibt die Nummer und friert die Anschrift ein."""
    with transaction.atomic():
        b = Bestellung.objects.select_for_update().get(pk=bestellung.pk)
        if b.status != S.ENTWURF:
            raise EinkaufFehler(f"{b} ist nicht im Entwurf und kann nicht freigegeben werden.")
        if not b.positionen.exists():
            raise EinkaufFehler("Eine Bestellung ohne Positionen kann nicht freigegeben werden.")
        if b.positionen.filter(menge__lte=0).exists():
            raise EinkaufFehler("Alle Positionen benötigen eine Menge größer 0.")
        b.nummer = naechste_nummer(NUMMERNKREIS[b.typ], b.datum)
        b.partner_daten = _partner_snapshot(b.lieferant)
        b.status = S.ANGEFRAGT if b.typ == Bestellung.Typ.ANFRAGE else S.BESTELLT
        b.freigegeben_von, b.freigegeben_am = _benutzer(benutzer), timezone.now()
        b.berechne_summen()
        b.save()
    veroeffentlichen("bestellung.freigegeben", bestellung=b, benutzer=benutzer)
    return b


def stornieren(bestellung, benutzer=None, grund=""):
    with transaction.atomic():
        b = Bestellung.objects.select_for_update().get(pk=bestellung.pk)
        if b.status in (S.STORNIERT, S.GELIEFERT):
            raise EinkaufFehler(f"{b} ist {b.get_status_display().lower()} und kann nicht storniert werden.")
        if b.positionen.filter(geliefert_menge__gt=0).exists():
            raise EinkaufFehler("Es wurden bereits Wareneingänge gebucht – bitte offene Restmengen abschließen "
                                "statt zu stornieren.")
        if not grund.strip():
            raise EinkaufFehler("Bitte einen Stornogrund angeben.")
        b.status, b.storniert_am, b.storno_grund = S.STORNIERT, timezone.now(), grund.strip()[:300]
        b.save()
        Bestellanforderung.objects.filter(bestellposition__bestellung=b).update(
            status=Bestellanforderung.Status.OFFEN, bestellposition=None)
    veroeffentlichen("bestellung.storniert", bestellung=b, benutzer=benutzer)
    return b


def status_fortschreiben(bestellung):
    """Leitet den Lieferstatus aus den Positionen ab (nach jeder Wareneingangsbuchung aufgerufen)."""
    b = bestellung
    if b.typ != Bestellung.Typ.BESTELLUNG or b.status in (S.ENTWURF, S.STORNIERT):
        return b
    positionen = list(b.positionen.all())
    if positionen and all(p.offen_menge <= 0 for p in positionen):
        neu = S.GELIEFERT
    elif any(p.geliefert_menge > 0 for p in positionen):
        neu = S.TEILGELIEFERT
    else:
        neu = S.BESTELLT
    if neu != b.status:
        b.status = neu
        b.save(update_fields=["status"])
        veroeffentlichen("bestellung.status", bestellung=b, status=neu)
    return b


def restmenge_abschliessen(position):
    """Akzeptiert eine Unterlieferung: Position gilt als erledigt."""
    if position.bestellung.status not in Bestellung.OFFENE_STATI:
        raise EinkaufFehler("Nur bei offenen Bestellungen möglich.")
    position.erledigt = True
    position.save(update_fields=["erledigt"])
    return status_fortschreiben(position.bestellung)


def anfrage_in_bestellung(anfrage, benutzer=None):
    """Kopiert eine Anfrage in einen neuen Bestellentwurf (Preise ggf. anschließend anpassen)."""
    if anfrage.typ != Bestellung.Typ.ANFRAGE:
        raise EinkaufFehler("Nur Anfragen können in eine Bestellung übernommen werden.")
    if anfrage.status == S.STORNIERT:
        raise EinkaufFehler("Die Anfrage ist storniert.")
    with transaction.atomic():
        b = Bestellung.objects.create(
            typ=Bestellung.Typ.BESTELLUNG, lieferant=anfrage.lieferant, liefertermin=anfrage.liefertermin,
            lieferort=anfrage.lieferort, zahlungsbedingung=anfrage.zahlungsbedingung,
            lieferbedingung=anfrage.lieferbedingung, ihre_referenz=anfrage.ihre_referenz, anfrage=anfrage,
            erstellt_von=_benutzer(benutzer))
        for p in anfrage.positionen.all():
            BestellPosition.objects.create(
                bestellung=b, position=p.position, artikel=p.artikel, bezeichnung=p.bezeichnung,
                lieferanten_artikelnummer=p.lieferanten_artikelnummer, menge=p.menge, einheit=p.einheit,
                einzelpreis=p.einzelpreis, rabatt_prozent=p.rabatt_prozent, steuersatz=p.steuersatz,
                liefertermin=p.liefertermin)
        b.berechne_summen()
        b.save()
    return b


# ---------------------------------------------------------------- Bestellvorschlag

def offene_bestellmengen():
    """Offene Mengen je Artikel aus freigegebenen Bestellungen und Bestellentwürfen."""
    werte = defaultdict(Decimal)
    qs = (BestellPosition.objects.filter(bestellung__typ=Bestellung.Typ.BESTELLUNG, erledigt=False,
                                         bestellung__status__in=[S.ENTWURF, S.BESTELLT, S.TEILGELIEFERT])
          .values("artikel").annotate(m=Sum("menge"), g=Sum("geliefert_menge")))
    for z in qs:
        werte[z["artikel"]] += max((z["m"] or 0) - (z["g"] or 0), Decimal(0))
    return werte


def bestellvorschlaege():
    """Artikel mit frei verfügbarem Bestand + offener Bestellmenge unter Meldebestand.

    Vorschlagsmenge = max(Standard-Bestellmenge, Meldebestand − frei − offen).
    """
    from apps.lager.services import unterschreitungen

    offen = offene_bestellmengen()
    ergebnis = []
    for u in unterschreitungen():
        ld, artikel = u["lagerdaten"], u["artikel"]
        o = offen.get(artikel.pk, Decimal(0))
        bedarf = max(ld.meldebestand, ld.mindestbestand) - u["frei"] - o
        if bedarf <= 0:
            continue
        ergebnis.append({
            "artikel": artikel, "lagerdaten": ld, "frei": u["frei"], "offen": o, "kritisch": u["kritisch"],
            "menge": max(ld.bestellmenge, bedarf), "lieferant": ld.bevorzugter_lieferant,
            "termin": timezone.localdate() + timedelta(days=ld.wiederbeschaffungszeit_tage),
        })
    return ergebnis


def vorschlaege_bestellen(artikel_ids, benutzer=None):
    """Erzeugt je bevorzugtem Lieferanten einen Bestellentwurf. Liefert (bestellungen, übersprungen)."""
    gruppen, uebersprungen = defaultdict(list), []
    for v in bestellvorschlaege():
        if v["artikel"].pk not in artikel_ids:
            continue
        if not v["lieferant"]:
            uebersprungen.append(v["artikel"])
        else:
            gruppen[v["lieferant"]].append(v)
    bestellungen = []
    with transaction.atomic():
        for lieferant, vorschlaege in gruppen.items():
            b = Bestellung.objects.create(lieferant=lieferant, zahlungsbedingung=lieferant.zahlungsbedingung,
                                          lieferort=vorschlaege[0]["lagerdaten"].standard_lagerort,
                                          kopftext="Bestellvorschlag aus Meldebestand-Unterschreitung",
                                          erstellt_von=_benutzer(benutzer))
            for i, v in enumerate(vorschlaege, start=1):
                position_vorbelegen(BestellPosition(bestellung=b, position=i * 10, artikel=v["artikel"],
                                                    menge=v["menge"], liefertermin=v["termin"],
                                                    einzelpreis=None, steuersatz=None)).save()
            b.berechne_summen()
            b.save()
            bestellungen.append(b)
    return bestellungen, uebersprungen


def banf_anlegen(artikel, menge, bedarfstermin=None, begruendung="", benutzer=None):
    return Bestellanforderung.objects.create(
        nummer=naechste_nummer("EK_BANF"), artikel=artikel, menge=menge, bedarfstermin=bedarfstermin,
        begruendung=begruendung, angefordert_von=_benutzer(benutzer))


def banf_uebernehmen(banfs, lieferant, benutzer=None):
    """Übernimmt offene Bestellanforderungen in einen Bestellentwurf beim gewählten Lieferanten."""
    banfs = [b for b in banfs if b.status == Bestellanforderung.Status.OFFEN]
    if not banfs:
        raise EinkaufFehler("Keine offenen Bestellanforderungen ausgewählt.")
    with transaction.atomic():
        b = Bestellung.objects.create(lieferant=lieferant, zahlungsbedingung=lieferant.zahlungsbedingung,
                                      erstellt_von=_benutzer(benutzer))
        for i, banf in enumerate(banfs, start=1):
            pos = position_vorbelegen(BestellPosition(bestellung=b, position=i * 10, artikel=banf.artikel,
                                                      menge=banf.menge, liefertermin=banf.bedarfstermin,
                                                      einzelpreis=None, steuersatz=None))
            pos.save()
            banf.status, banf.bestellposition = Bestellanforderung.Status.BESTELLT, pos
            banf.save(update_fields=["status", "bestellposition"])
        b.berechne_summen()
        b.save()
    return b


def offene_positionen():
    """Offene Bestellpositionen (für Liefertermin-Übersicht und Wareneingang)."""
    return (BestellPosition.objects.filter(bestellung__typ=Bestellung.Typ.BESTELLUNG,
                                           bestellung__status__in=Bestellung.OFFENE_STATI, erledigt=False,
                                           geliefert_menge__lt=F("menge"))
            .select_related("bestellung", "bestellung__lieferant", "artikel", "artikel__einheit"))

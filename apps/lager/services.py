"""Buchungslogik des Lagers – einzige Stelle, an der Bestände verändert werden.

Serialisierung: Pro Artikel wird die Zeile der Dispositionsdaten (ArtikelLager) per SELECT … FOR UPDATE
gesperrt. Parallele Buchungen desselben Artikels laufen dadurch nacheinander; Bestand, Gesamtbestand und
gleitender Durchschnittspreis bleiben konsistent.

Bewertung (gleitender Durchschnitt):
* Zugang:   Ø_neu = (Bestand_alt × Ø_alt + Menge × Preis) / (Bestand_alt + Menge)
* Abgang:   bewertet mit Ø_alt, Ø bleibt unverändert
* Umbuchung/QS-Freigabe: kein Werteffekt
* Inventur: Mehrmengen zum Ø (falls 0: Einkaufspreis), Mindermengen zum Ø
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.events import veroeffentlichen
from apps.core.nummernkreise import naechste_nummer

from .models import (
    ArtikelLager, Bestand, Bestandsart, Charge, Inventur, InventurPosition, Lagerbewegung, LagerFehler,
)

Art = Lagerbewegung.Art
DREI, VIER, ZWEI = Decimal("0.001"), Decimal("0.0001"), Decimal("0.01")


def _d(wert):
    return Decimal(str(wert)) if wert is not None else None


def lagerdaten(artikel, sperren=False):
    ld, _ = ArtikelLager.objects.get_or_create(
        artikel=artikel, defaults={"durchschnittspreis": artikel.einkaufspreis or 0})
    if sperren:
        ld = ArtikelLager.objects.select_for_update().get(pk=ld.pk)
    return ld


def _zeile(artikel, ort, platz, charge, bestandsart):
    obj, _ = Bestand.objects.get_or_create(artikel=artikel, lagerort=ort, lagerplatz=platz, charge=charge,
                                           bestandsart=bestandsart)
    return Bestand.objects.select_for_update().get(pk=obj.pk)


def _ort_pruefen(ort):
    lagerort, platz, art = ort
    if lagerort is None:
        raise LagerFehler("Bitte einen Lagerort angeben.")
    if platz is not None and platz.lagerort_id != lagerort.pk:
        raise LagerFehler(f"Lagerplatz {platz} gehört nicht zu Lagerort {lagerort.code}.")
    if art not in Bestandsart.values:
        raise LagerFehler(f"Unbekannte Bestandsart: {art}")


def buchen(art, artikel, menge, *, nach=None, von=None, charge=None, preis=None, benutzer=None,
           quelle="manuell", quelle_id=None, beleg_nummer="", grund="", buchungsdatum=None):
    """Bucht eine Lagerbewegung. `von`/`nach` sind Tupel (Lagerort, Lagerplatz|None, Bestandsart)."""
    menge = _d(menge)
    if menge is None or menge <= 0:
        raise LagerFehler("Die Menge muss größer als 0 sein.")
    erwartet = {Art.ZUGANG: (False, True), Art.ABGANG: (True, False), Art.UMBUCHUNG: (True, True)}
    if art == Art.INVENTUR:
        if bool(von) == bool(nach):
            raise LagerFehler("Inventurkorrektur braucht genau eine Seite (Mehr- oder Mindermenge).")
    elif art in erwartet:
        if (bool(von), bool(nach)) != erwartet[art]:
            raise LagerFehler(f"Ungültige Angaben für {Art(art).label}.")
    else:
        raise LagerFehler(f"Unbekannte Bewegungsart: {art}")
    for ort in (von, nach):
        if ort:
            _ort_pruefen(ort)
    if artikel.chargenpflicht and charge is None:
        raise LagerFehler(f"Artikel {artikel.artikelnummer} ist chargenpflichtig – bitte eine Charge angeben.")
    if charge is not None and charge.artikel_id != artikel.pk:
        raise LagerFehler(f"Charge {charge.chargennummer} gehört nicht zu Artikel {artikel.artikelnummer}.")

    with transaction.atomic():
        ld = lagerdaten(artikel, sperren=True)
        alt_bestand, alt_preis = ld.bestand_gesamt, ld.durchschnittspreis
        if von:
            z = _zeile(artikel, von[0], von[1], charge, von[2])
            if z.menge < menge:
                ort_text = f"{von[1] or von[0].code}" + (" (gesperrt)" if von[2] == Bestandsart.GESPERRT else "")
                raise LagerFehler(
                    f"Nicht genügend Bestand von {artikel.artikelnummer}"
                    f"{' Charge ' + charge.chargennummer if charge else ''} auf {ort_text}: "
                    f"vorhanden {z.menge.normalize():f}, benötigt {menge.normalize():f}.")
            z.menge -= menge
            z.save(update_fields=["menge", "aktualisiert"])
        if nach:
            z = _zeile(artikel, nach[0], nach[1], charge, nach[2])
            z.menge += menge
            z.save(update_fields=["menge", "aktualisiert"])

        if nach and not von:  # Zugang oder Inventur-Mehrmenge
            if art == Art.ZUGANG and preis is not None:
                p = _d(preis)
            else:
                p = alt_preis or _d(preis) or artikel.einkaufspreis or Decimal(0)
            neu_bestand = alt_bestand + menge
            neu_preis = p if alt_bestand <= 0 else (alt_bestand * alt_preis + menge * p) / neu_bestand
            wert = menge * p
        elif von and not nach:  # Abgang oder Inventur-Mindermenge
            p, neu_bestand, neu_preis = alt_preis, alt_bestand - menge, alt_preis
            wert = -(menge * p)
        else:  # Umbuchung
            p, neu_bestand, neu_preis, wert = alt_preis, alt_bestand, alt_preis, Decimal(0)

        ld.bestand_gesamt = neu_bestand
        ld.durchschnittspreis = Decimal(neu_preis).quantize(VIER, ROUND_HALF_UP)
        ld.save(update_fields=["bestand_gesamt", "durchschnittspreis"])
        bewegung = Lagerbewegung.objects.create(
            art=art, artikel=artikel, charge=charge, menge=menge,
            von_lagerort=von[0] if von else None, von_lagerplatz=von[1] if von else None,
            von_bestandsart=von[2] if von else "",
            nach_lagerort=nach[0] if nach else None, nach_lagerplatz=nach[1] if nach else None,
            nach_bestandsart=nach[2] if nach else "",
            bewertungspreis=Decimal(p).quantize(VIER, ROUND_HALF_UP),
            wert=Decimal(wert).quantize(ZWEI, ROUND_HALF_UP),
            durchschnittspreis_neu=ld.durchschnittspreis, bestand_neu=neu_bestand,
            quelle=quelle, quelle_id=quelle_id, beleg_nummer=beleg_nummer, grund=(grund or "")[:300],
            benutzer=benutzer if getattr(benutzer, "pk", None) else None,
            buchungsdatum=buchungsdatum or timezone.localdate(),
        )
    veroeffentlichen("lager.bewegung", bewegung=bewegung, benutzer=benutzer)
    if von and von[2] == Bestandsart.FREI and ld.meldebestand > 0:
        frei = verfuegbar(artikel)
        if frei < ld.meldebestand:
            veroeffentlichen("bestand.unterschritten", artikel=artikel, verfuegbar=frei,
                             meldebestand=ld.meldebestand, mindestbestand=ld.mindestbestand)
    return bewegung


# ---------------------------------------------------------------- Komfortfunktionen

def charge_holen(artikel, chargennummer, anlegen=False, **daten):
    """Liefert die Charge eines Artikels (legt sie bei `anlegen=True` an). Leere Nummer → None."""
    nummer = (chargennummer or "").strip()
    if not nummer:
        return None
    if anlegen:
        charge, neu = Charge.objects.get_or_create(artikel=artikel, chargennummer=nummer,
                                                   defaults={k: v for k, v in daten.items() if v})
        return charge
    try:
        return Charge.objects.get(artikel=artikel, chargennummer=nummer)
    except Charge.DoesNotExist as exc:
        raise LagerFehler(f"Charge „{nummer}“ zu Artikel {artikel.artikelnummer} ist nicht vorhanden.") from exc


def verfuegbar(artikel):
    return Bestand.objects.filter(artikel=artikel, bestandsart=Bestandsart.FREI).aggregate(
        s=Sum("menge"))["s"] or Decimal(0)


def bestand_summen(artikel):
    werte = {Bestandsart.FREI: Decimal(0), Bestandsart.GESPERRT: Decimal(0)}
    for zeile in Bestand.objects.filter(artikel=artikel).values("bestandsart").annotate(s=Sum("menge")):
        werte[zeile["bestandsart"]] = zeile["s"] or Decimal(0)
    return werte


def unterschreitungen():
    """Artikel mit frei verfügbarem Bestand unter Melde- bzw. Mindestbestand."""
    frei = dict(Bestand.objects.filter(bestandsart=Bestandsart.FREI).values("artikel")
                .annotate(s=Sum("menge")).values_list("artikel", "s"))
    ergebnis = []
    for ld in ArtikelLager.objects.select_related("artikel", "artikel__einheit").filter(artikel__aktiv=True):
        if ld.meldebestand <= 0 and ld.mindestbestand <= 0:
            continue
        menge = frei.get(ld.artikel_id) or Decimal(0)
        if menge < ld.meldebestand or menge < ld.mindestbestand:
            ergebnis.append({"lagerdaten": ld, "artikel": ld.artikel, "frei": menge,
                             "kritisch": menge < ld.mindestbestand})
    return ergebnis


# ---------------------------------------------------------------- Inventur

def inventur_anlegen(lagerort, stichtag=None, bezeichnung="", benutzer=None):
    """Erzeugt eine Zählliste aus allen Beständen (> 0) des Lagerorts."""
    with transaction.atomic():
        stichtag = stichtag or timezone.localdate()
        inv = Inventur.objects.create(nummer=naechste_nummer("INVENTUR", stichtag), lagerort=lagerort,
                                      stichtag=stichtag, bezeichnung=bezeichnung,
                                      erstellt_von=benutzer if getattr(benutzer, "pk", None) else None)
        for b in Bestand.objects.filter(lagerort=lagerort, menge__gt=0).select_related("artikel"):
            InventurPosition.objects.create(inventur=inv, artikel=b.artikel, lagerplatz=b.lagerplatz,
                                            charge=b.charge, bestandsart=b.bestandsart, sollmenge=b.menge)
    return inv


def inventur_buchen(inventur, benutzer=None):
    """Bucht für jede gezählte Position die Differenz zum *aktuellen* Buchbestand als Inventurkorrektur."""
    with transaction.atomic():
        inv = Inventur.objects.select_for_update().get(pk=inventur.pk)
        if inv.status != Inventur.Status.OFFEN:
            raise LagerFehler(f"{inv} ist bereits gebucht.")
        anzahl = 0
        for pos in inv.positionen.select_related("artikel", "charge", "lagerplatz"):
            if pos.istmenge is None:
                continue
            if pos.istmenge < 0:
                raise LagerFehler(f"Negative Zählmenge bei {pos.artikel.artikelnummer}.")
            aktuell = Bestand.objects.filter(
                artikel=pos.artikel, lagerort=inv.lagerort, lagerplatz=pos.lagerplatz, charge=pos.charge,
                bestandsart=pos.bestandsart).values_list("menge", flat=True).first() or Decimal(0)
            diff = pos.istmenge - aktuell
            ort = (inv.lagerort, pos.lagerplatz, pos.bestandsart)
            if diff:
                buchen(Art.INVENTUR, pos.artikel, abs(diff), nach=ort if diff > 0 else None,
                       von=ort if diff < 0 else None, charge=pos.charge, benutzer=benutzer, quelle="inventur",
                       quelle_id=inv.pk, beleg_nummer=inv.nummer, grund=f"Inventur {inv.nummer}",
                       buchungsdatum=inv.stichtag)
                anzahl += 1
            pos.differenz = diff
            pos.save(update_fields=["differenz"])
        inv.status = Inventur.Status.GEBUCHT
        inv.gebucht_von = benutzer if getattr(benutzer, "pk", None) else None
        inv.gebucht_am = timezone.now()
        inv.save()
    veroeffentlichen("inventur.gebucht", inventur=inv, benutzer=benutzer)
    return anzahl

"""Standardberichte Lager (nur lesend)."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Max, Sum

from apps.reporting.registry import Spalte, bericht
from apps.stammdaten.models import Artikel

from . import services
from .models import ArtikelLager, Bestand, Charge, Lagerbewegung

NULL = Decimal(0)
ARTEN = dict(Artikel._meta.get_field("art").choices)


def _preise():
    return dict(ArtikelLager.objects.values_list("artikel_id", "durchschnittspreis"))


def lagerwert():
    preise = _preise()
    return sum(((b.menge * preise.get(b.artikel_id, NULL)) for b in Bestand.objects.only("menge", "artikel_id")),
               NULL).quantize(Decimal("0.01"))


@bericht("lager.bestandswert", "Bestandswert", modul="lager", filter=["lager_gruppe", "artikelart"],
         spalten=[Spalte("gruppe", "Artikelart / Lagerort"), Spalte("artikel", "Artikel", "zahl"),
                  Spalte("menge", "Menge", "menge", summe=True), Spalte("frei", "davon frei (Wert)", "geld", summe=True),
                  Spalte("gesperrt", "davon gesperrt (Wert)", "geld", summe=True),
                  Spalte("wert", "Bestandswert", "geld", summe=True)],
         beschreibung="Aktueller Bestand bewertet zum gleitenden Durchschnittspreis",
         diagramm={"typ": "pie", "label": "gruppe", "wert": "wert"})
def bestandswert(f):
    preise = _preise()
    qs = Bestand.objects.exclude(menge=0).select_related("artikel", "lagerort")
    if f.get("artikelart"):
        qs = qs.filter(artikel__art=f["artikelart"])
    daten = defaultdict(lambda: {"a": set(), "m": NULL, "frei": NULL, "gesperrt": NULL})
    for b in qs:
        key = f"{b.lagerort.code} {b.lagerort.bezeichnung}" if f.get("lager_gruppe") == "lagerort" \
            else ARTEN.get(b.artikel.art, b.artikel.art)
        d = daten[key]
        wert = (b.menge * preise.get(b.artikel_id, NULL)).quantize(Decimal("0.01"))
        d["a"].add(b.artikel_id)
        d["m"] += b.menge
        d["frei" if b.bestandsart == "FREI" else "gesperrt"] += wert
    zeilen = [{"gruppe": k, "artikel": len(d["a"]), "menge": d["m"], "frei": d["frei"], "gesperrt": d["gesperrt"],
               "wert": d["frei"] + d["gesperrt"]} for k, d in daten.items()]
    return sorted(zeilen, key=lambda z: -z["wert"])


@bericht("lager.meldebestand", "Meldebestands-Unterschreitungen", modul="lager", filter=[],
         spalten=[Spalte("artikel", "Artikel"), Spalte("bezeichnung", "Bezeichnung"),
                  Spalte("frei", "Frei verfügbar", "menge"), Spalte("meldebestand", "Meldebestand", "menge"),
                  Spalte("mindestbestand", "Mindestbestand", "menge"), Spalte("bestellmenge", "Bestellmenge", "menge"),
                  Spalte("lieferant", "Bevorzugter Lieferant"), Spalte("status", "Status")],
         beschreibung="Artikel, deren freier Bestand unter Melde- oder Mindestbestand liegt")
def meldebestand(f):
    zeilen = []
    for u in services.unterschreitungen():
        ld = u["lagerdaten"]
        zeilen.append({"artikel": u["artikel"].artikelnummer, "bezeichnung": u["artikel"].bezeichnung,
                       "frei": u["frei"], "meldebestand": ld.meldebestand, "mindestbestand": ld.mindestbestand,
                       "bestellmenge": ld.bestellmenge,
                       "lieferant": ld.bevorzugter_lieferant.name if ld.bevorzugter_lieferant_id else "",
                       "status": "kritisch (unter Mindestbestand)" if u["kritisch"] else "Meldebestand erreicht"})
    return zeilen


@bericht("lager.umschlag", "Lagerumschlag und Reichweite", modul="lager", filter=["von", "bis", "artikelart"],
         spalten=[Spalte("artikel", "Artikel"), Spalte("bezeichnung", "Bezeichnung"),
                  Spalte("abgang", "Abgang im Zeitraum", "menge"), Spalte("bestand", "Bestand aktuell", "menge"),
                  Spalte("wert", "Bestandswert", "geld", summe=True), Spalte("umschlag", "Umschlag (Faktor)", "menge"),
                  Spalte("reichweite", "Reichweite (Tage)", "zahl")],
         beschreibung="Umschlag = Abgang / Bestand; Reichweite = Bestand / Ø Tagesverbrauch")
def umschlag(f):
    tage = max((f["bis"] - f["von"]).days + 1, 1)
    abgang = dict(Lagerbewegung.objects.filter(art=Lagerbewegung.Art.ABGANG, buchungsdatum__gte=f["von"],
                                               buchungsdatum__lte=f["bis"])
                  .values("artikel").annotate(s=Sum("menge")).values_list("artikel", "s"))
    qs = ArtikelLager.objects.select_related("artikel").filter(artikel__aktiv=True)
    if f.get("artikelart"):
        qs = qs.filter(artikel__art=f["artikelart"])
    zeilen = []
    for ld in qs.order_by("artikel__artikelnummer"):
        ab = abs(abgang.get(ld.artikel_id) or NULL)
        bestand = ld.bestand_gesamt
        if not ab and not bestand:
            continue
        verbrauch = ab / tage
        zeilen.append({"artikel": ld.artikel.artikelnummer, "bezeichnung": ld.artikel.bezeichnung, "abgang": ab,
                       "bestand": bestand, "wert": (bestand * ld.durchschnittspreis).quantize(Decimal("0.01")),
                       "umschlag": (ab / bestand).quantize(Decimal("0.01")) if bestand else None,
                       "reichweite": int(bestand / verbrauch) if verbrauch else None})
    return {"zeilen": zeilen, "hinweise": [
        "Vereinfachung: Basis ist der aktuelle Bestand statt eines Durchschnittsbestands. "
        "Reichweite leer = kein Verbrauch im Zeitraum."]}


@bericht("lager.ladenhueter", "Ladenhüter", modul="lager", filter=["tage", "artikelart"],
         spalten=[Spalte("artikel", "Artikel"), Spalte("bezeichnung", "Bezeichnung"),
                  Spalte("bestand", "Bestand", "menge"), Spalte("wert", "Bestandswert", "geld", summe=True),
                  Spalte("letzte", "Letzte Bewegung", "datum"), Spalte("tage", "Tage ohne Bewegung", "zahl")],
         beschreibung="Artikel mit Bestand, aber ohne Lagerbewegung seit X Tagen")
def ladenhueter(f):
    from django.utils import timezone

    heute = timezone.localdate()
    grenze = heute - timedelta(days=f.get("tage") or 0)
    letzte = dict(Lagerbewegung.objects.values("artikel").annotate(d=Max("buchungsdatum"))
                  .values_list("artikel", "d"))
    qs = ArtikelLager.objects.select_related("artikel").filter(bestand_gesamt__gt=0)
    if f.get("artikelart"):
        qs = qs.filter(artikel__art=f["artikelart"])
    zeilen = []
    for ld in qs:
        d = letzte.get(ld.artikel_id)
        if d is None or d <= grenze:
            zeilen.append({"artikel": ld.artikel.artikelnummer, "bezeichnung": ld.artikel.bezeichnung,
                           "bestand": ld.bestand_gesamt,
                           "wert": (ld.bestand_gesamt * ld.durchschnittspreis).quantize(Decimal("0.01")),
                           "letzte": d, "tage": (heute - d).days if d else None})
    return sorted(zeilen, key=lambda z: -(z["tage"] or 99999))


@bericht("lager.mhd", "Chargen mit MHD-Ablauf", modul="lager", filter=["stichtag", "tage", "artikel"],
         spalten=[Spalte("charge", "Charge"), Spalte("artikel", "Artikel"), Spalte("mhd", "MHD", "datum"),
                  Spalte("resttage", "Resttage", "zahl"), Spalte("menge", "Bestand", "menge"),
                  Spalte("wert", "Bestandswert", "geld", summe=True), Spalte("status", "Status")],
         beschreibung="Chargen mit Bestand, deren MHD bis Stichtag + X Tage abläuft (inkl. bereits abgelaufener)")
def mhd(f):
    grenze = f["stichtag"] + timedelta(days=f.get("tage") or 0)
    preise = _preise()
    qs = Charge.objects.filter(mhd__isnull=False, mhd__lte=grenze).select_related("artikel").order_by("mhd")
    if f.get("artikel"):
        qs = qs.filter(artikel=f["artikel"])
    mengen = dict(Bestand.objects.filter(charge__in=qs).values("charge").annotate(s=Sum("menge"))
                  .values_list("charge", "s"))
    zeilen = []
    for c in qs:
        menge = mengen.get(c.pk) or NULL
        if menge <= 0:
            continue
        rest = (c.mhd - f["stichtag"]).days
        zeilen.append({"charge": c.chargennummer, "artikel": c.artikel.artikelnummer, "mhd": c.mhd, "resttage": rest,
                       "menge": menge, "wert": (menge * preise.get(c.artikel_id, NULL)).quantize(Decimal("0.01")),
                       "status": "abgelaufen" if rest < 0 else "läuft ab"})
    return zeilen

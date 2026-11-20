"""Standardberichte Einkauf (nur lesend)."""
from collections import defaultdict
from decimal import Decimal

from apps.reporting.registry import Spalte, bericht

from .models import BestellPosition, Bestellung

NULL = Decimal(0)


def _prozent(teil, ganzes):
    return (Decimal(teil) / Decimal(ganzes) * 100).quantize(Decimal("0.1")) if ganzes else None


def _bestellpositionen(f):
    qs = (BestellPosition.objects.filter(bestellung__typ=Bestellung.Typ.BESTELLUNG,
                                         bestellung__datum__gte=f["von"], bestellung__datum__lte=f["bis"])
          .exclude(bestellung__status__in=[Bestellung.Status.ENTWURF, Bestellung.Status.STORNIERT])
          .select_related("bestellung", "bestellung__lieferant", "artikel"))
    if f.get("lieferant"):
        qs = qs.filter(bestellung__lieferant=f["lieferant"])
    if f.get("artikelart"):
        qs = qs.filter(artikel__art=f["artikelart"])
    return qs


@bericht("einkauf.volumen", "Einkaufsvolumen", modul="einkauf",
         filter=["von", "bis", "ek_gruppe", "lieferant", "artikelart"],
         spalten=[Spalte("gruppe", "Lieferant / Artikelart"), Spalte("bestellungen", "Bestellungen", "zahl"),
                  Spalte("positionen", "Positionen", "zahl", summe=True),
                  Spalte("volumen", "Volumen netto", "geld", summe=True), Spalte("anteil", "Anteil", "prozent")],
         beschreibung="Bestellwert freigegebener Bestellungen nach Lieferant oder Artikelart",
         diagramm={"typ": "bar", "label": "gruppe", "wert": "volumen"})
def volumen(f):
    arten = dict(BestellPosition._meta.get_field("artikel").related_model._meta.get_field("art").choices)
    daten = defaultdict(lambda: {"b": set(), "p": 0, "v": NULL})
    for p in _bestellpositionen(f):
        if f.get("ek_gruppe") == "art":
            key = arten.get(p.artikel.art, p.artikel.art) if p.artikel else "(ohne Artikel)"
        else:
            key = p.bestellung.lieferant.name
        d = daten[key]
        d["b"].add(p.bestellung_id)
        d["p"] += 1
        d["v"] += p.betrag_netto
    gesamt = sum((d["v"] for d in daten.values()), NULL)
    zeilen = [{"gruppe": k, "bestellungen": len(d["b"]), "positionen": d["p"], "volumen": d["v"],
               "anteil": _prozent(d["v"], gesamt)} for k, d in daten.items()]
    return {"zeilen": sorted(zeilen, key=lambda z: -z["volumen"]),
            "summen_extra": {"anteil": Decimal(100) if zeilen else NULL}}


@bericht("einkauf.offene_bestellungen", "Offene Bestellungen", modul="einkauf", filter=["stichtag", "lieferant"],
         spalten=[Spalte("bestellung", "Bestellung"), Spalte("lieferant", "Lieferant"), Spalte("artikel", "Artikel"),
                  Spalte("termin", "Liefertermin", "datum"), Spalte("bestellt", "Bestellt", "menge"),
                  Spalte("geliefert", "Geliefert", "menge"), Spalte("offen", "Offen", "menge"),
                  Spalte("wert", "Offener Wert netto", "geld", summe=True), Spalte("status", "Termin")],
         beschreibung="Noch nicht vollständig gelieferte Bestellpositionen")
def offene_bestellungen(f):
    qs = (BestellPosition.objects.filter(bestellung__typ=Bestellung.Typ.BESTELLUNG,
                                         bestellung__status__in=Bestellung.OFFENE_STATI, erledigt=False)
          .select_related("bestellung", "bestellung__lieferant", "artikel").order_by("bestellung__nummer", "position"))
    if f.get("lieferant"):
        qs = qs.filter(bestellung__lieferant=f["lieferant"])
    zeilen = []
    for p in qs:
        offen = p.offen_menge
        if offen <= 0:
            continue
        termin = p.liefertermin or p.bestellung.liefertermin
        anteil = offen / p.menge if p.menge else 0
        zeilen.append({"bestellung": p.bestellung.nummer, "lieferant": p.bestellung.lieferant.name,
                       "artikel": p.artikel.artikelnummer if p.artikel else p.bezeichnung, "termin": termin,
                       "bestellt": p.menge, "geliefert": p.geliefert_menge, "offen": offen,
                       "wert": (p.betrag_netto * anteil).quantize(Decimal("0.01")),
                       "status": "überfällig" if termin and termin < f["stichtag"] else "im Plan"})
    return zeilen


@bericht("einkauf.liefertreue", "Liefertreue Lieferanten", modul="einkauf", filter=["von", "bis", "lieferant"],
         spalten=[Spalte("lieferant", "Lieferant"), Spalte("lieferungen", "WE-Positionen", "zahl", summe=True),
                  Spalte("termin_ok", "termingerecht", "zahl", summe=True),
                  Spalte("termin_quote", "Termintreue", "prozent"),
                  Spalte("menge_ok", "mengengerecht", "zahl", summe=True),
                  Spalte("menge_quote", "Mengentreue", "prozent"),
                  Spalte("verzug", "Ø Verzug (Tage)", "zahl")],
         beschreibung="Termingerecht: Wareneingang spätestens zum Liefertermin; mengengerecht: Menge = offene Menge")
def liefertreue(f):
    from apps.wareneingang.models import WEPosition

    qs = (WEPosition.objects.filter(wareneingang__datum__gte=f["von"], wareneingang__datum__lte=f["bis"],
                                    bestellposition__isnull=False)
          .select_related("wareneingang", "wareneingang__lieferant", "bestellposition__bestellung"))
    if f.get("lieferant"):
        qs = qs.filter(wareneingang__lieferant=f["lieferant"])
    daten = defaultdict(lambda: [0, 0, 0, 0])
    for p in qs:
        bp = p.bestellposition
        termin = bp.liefertermin or bp.bestellung.liefertermin
        tage = (p.wareneingang.datum - termin).days if termin else 0
        d = daten[p.wareneingang.lieferant.name]
        d[0] += 1
        d[1] += tage <= 0
        d[2] += p.mengendifferenz == 0
        d[3] += max(tage, 0)
    zeilen = [{"lieferant": k, "lieferungen": n, "termin_ok": t, "termin_quote": _prozent(t, n), "menge_ok": m,
               "menge_quote": _prozent(m, n), "verzug": round(v / n) if n else 0}
              for k, (n, t, m, v) in sorted(daten.items())]
    n = sum(z["lieferungen"] for z in zeilen)
    return {"zeilen": zeilen, "summen_extra": {
        "termin_quote": _prozent(sum(z["termin_ok"] for z in zeilen), n) or NULL,
        "menge_quote": _prozent(sum(z["menge_ok"] for z in zeilen), n) or NULL}}

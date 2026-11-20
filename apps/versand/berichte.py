"""Standardberichte Versand (nur lesend)."""
from collections import defaultdict
from decimal import Decimal

from apps.reporting.registry import Spalte, bericht

from .models import Tour

NULL = Decimal(0)
EIN = Decimal("0.1")


def _touren(f):
    return (Tour.objects.filter(datum__gte=f["von"], datum__lte=f["bis"])
            .select_related("region", "fahrzeug").prefetch_related("stopps__lieferung"))


def _last(tour):
    kg = sum((s.lieferung.gewicht_kg or NULL for s in tour.stopps.all()), NULL)
    pal = sum((s.lieferung.paletten or NULL for s in tour.stopps.all()), NULL)
    return kg, pal


def _quote(teil, ganzes):
    return (teil / ganzes * 100).quantize(EIN) if ganzes else None


@bericht("versand.touren_region", "Touren je Region", modul="versand", filter=["von", "bis"],
         spalten=[Spalte("region", "Region"), Spalte("touren", "Touren", "zahl", summe=True),
                  Spalte("stopps", "Stopps", "zahl", summe=True), Spalte("gewicht", "Gewicht (kg)", "menge", summe=True),
                  Spalte("paletten", "Paletten", "menge", summe=True), Spalte("km", "Entfernung (km)", "menge", summe=True)],
         beschreibung="Anzahl Touren, Stopps und Ladung je Versandregion im Zeitraum",
         diagramm={"typ": "bar", "label": "region", "wert": "stopps"})
def touren_region(f):
    daten = defaultdict(lambda: [0, 0, NULL, NULL, NULL])
    for t in _touren(f):
        kg, pal = _last(t)
        d = daten[t.region.name if t.region else "(ohne Region)"]
        d[0] += 1
        d[1] += len(t.stopps.all())
        d[2] += kg
        d[3] += pal
        d[4] += sum((s.entfernung_km or NULL for s in t.stopps.all()), NULL)
    return [{"region": k, "touren": n, "stopps": s, "gewicht": kg, "paletten": p, "km": km}
            for k, (n, s, kg, p, km) in sorted(daten.items())]


@bericht("versand.fahrzeugauslastung", "Fahrzeugauslastung", modul="versand", filter=["von", "bis"],
         spalten=[Spalte("fahrzeug", "Fahrzeug"), Spalte("touren", "Touren", "zahl", summe=True),
                  Spalte("max_kg", "Nutzlast (kg)", "menge"), Spalte("kg", "Ø Gewicht (kg)", "menge"),
                  Spalte("auslastung_kg", "Ø Auslastung Gewicht", "prozent"),
                  Spalte("spitze_kg", "Max. Auslastung Gewicht", "prozent"),
                  Spalte("auslastung_pal", "Ø Auslastung Paletten", "prozent")],
         beschreibung="Ladung der Touren gegen Gewichts- und Palettenkapazität des Fahrzeugs")
def fahrzeugauslastung(f):
    daten = defaultdict(list)
    for t in _touren(f):
        if t.fahrzeug:
            daten[t.fahrzeug].append(_last(t))
    zeilen = []
    for fz, lasten in sorted(daten.items(), key=lambda e: e[0].kennzeichen):
        n = len(lasten)
        kg = sum((l[0] for l in lasten), NULL) / n
        pal = sum((l[1] for l in lasten), NULL) / n
        zeilen.append({"fahrzeug": f"{fz.kennzeichen} {fz.bezeichnung}", "touren": n, "max_kg": fz.max_kg,
                       "kg": kg.quantize(EIN), "auslastung_kg": _quote(kg, fz.max_kg),
                       "spitze_kg": _quote(max(l[0] for l in lasten), fz.max_kg),
                       "auslastung_pal": _quote(pal, fz.max_paletten)})
    return zeilen


@bericht("versand.liefertreue", "Liefertreue gegenüber Kunden", modul="versand", filter=["von", "bis", "kunde"],
         spalten=[Spalte("kunde", "Kunde"), Spalte("lieferungen", "Lieferungen", "zahl", summe=True),
                  Spalte("puenktlich", "termingerecht", "zahl", summe=True), Spalte("quote", "Liefertreue", "prozent"),
                  Spalte("verzug", "Ø Verzug verspäteter (Tage)", "menge")],
         beschreibung="Gebuchte Lieferscheine: Lieferdatum gegen frühesten zugesagten Termin des Auftrags")
def liefertreue(f):
    from apps.vertrieb.models import Lieferung

    qs = (Lieferung.objects.filter(lieferdatum__gte=f["von"], lieferdatum__lte=f["bis"],
                                   status__in=[Lieferung.Status.GEBUCHT, Lieferung.Status.VERLADEN,
                                               Lieferung.Status.ZUGESTELLT])
          .select_related("auftrag", "auftrag__partner").prefetch_related("positionen__auftrag_position"))
    if f.get("kunde"):
        qs = qs.filter(auftrag__partner=f["kunde"])
    daten = defaultdict(lambda: [0, 0, 0])
    for l in qs:
        termine = [p.auftrag_position.liefertermin for p in l.positionen.all() if p.auftrag_position.liefertermin]
        termin = min(termine) if termine else l.auftrag.liefertermin
        tage = (l.lieferdatum - termin).days if termin else 0
        d = daten[l.auftrag.partner.name]
        d[0] += 1
        d[1] += tage <= 0
        d[2] += max(tage, 0)
    zeilen = [{"kunde": k, "lieferungen": n, "puenktlich": p, "quote": _quote(Decimal(p), Decimal(n)),
               "verzug": (Decimal(v) / (n - p)).quantize(EIN) if n - p else NULL}
              for k, (n, p, v) in sorted(daten.items())]
    n = sum(z["lieferungen"] for z in zeilen)
    p = sum(z["puenktlich"] for z in zeilen)
    return {"zeilen": zeilen, "summen_extra": {"quote": _quote(Decimal(p), Decimal(n)) or NULL},
            "hinweise": ["Ohne Termin am Auftrag gilt die Lieferung als termingerecht."]}

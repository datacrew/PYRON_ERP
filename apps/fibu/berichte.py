"""Standardberichte Finanzbuchhaltung (nur lesend)."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from apps.reporting.registry import Spalte, bericht

from . import auswertungen as A
from .models import NULL, OffenerPosten

RASTER = [("nicht_faellig", "nicht fällig", None, -1), ("t0_30", "0–30 Tage", 0, 30), ("t31_60", "31–60 Tage", 31, 60),
          ("t61_90", "61–90 Tage", 61, 90), ("t90", "> 90 Tage", 91, None)]


def _offene_posten(art, stichtag):
    return (OffenerPosten.objects.filter(art=art, belegdatum__lte=stichtag).exclude(offen=0)
            .select_related("partner"))


def altersstruktur(art="D", stichtag=None):
    """{raster_key: Summe} über alle offenen Posten."""
    from django.utils import timezone

    stichtag = stichtag or timezone.localdate()
    werte = {k: NULL for k, *_ in RASTER}
    for op in _offene_posten(art, stichtag):
        werte[_raster(op.tage_ueberfaellig(stichtag))] += op.offen
    return werte


def _raster(tage):
    for key, _, ab, bis in RASTER:
        if (ab is None and tage < 0) or (ab is not None and tage >= ab and (bis is None or tage <= bis)):
            return key
    return RASTER[-1][0]


@bericht("fibu.op_altersstruktur", "Offene Posten mit Altersstruktur", modul="fibu", filter=["op_art", "stichtag"],
         spalten=[Spalte("partner", "Geschäftspartner"), Spalte("posten", "Posten", "zahl", summe=True),
                  Spalte("offen", "Offen gesamt", "geld", summe=True)]
         + [Spalte(k, label, "geld", summe=True) for k, label, *_ in RASTER],
         beschreibung="Debitoren/Kreditoren je Partner, gegliedert nach Tagen seit Fälligkeit", quer=True,
         diagramm={"typ": "bar", "label": "partner", "wert": "offen"})
def op_altersstruktur(f):
    daten = defaultdict(lambda: {"posten": 0, "offen": NULL, **{k: NULL for k, *_ in RASTER}})
    for op in _offene_posten(f.get("op_art") or "D", f["stichtag"]):
        d = daten[f"{op.partner.nummer} {op.partner.name}"]
        d["posten"] += 1
        d["offen"] += op.offen
        d[_raster(op.tage_ueberfaellig(f["stichtag"]))] += op.offen
    return {"zeilen": [{"partner": k, **d} for k, d in sorted(daten.items())],
            "hinweise": ["Tage = Stichtag − Fälligkeitsdatum; „nicht fällig“ = Fälligkeit nach dem Stichtag. "
                         "Offen = aktueller Restbetrag des Postens."]}


@bericht("fibu.liquiditaet", "Liquiditätsübersicht", modul="fibu", filter=["stichtag"],
         spalten=[Spalte("zeitraum", "Zeitraum"), Spalte("einzahlungen", "Erwartete Einzahlungen", "geld", summe=True),
                  Spalte("auszahlungen", "Erwartete Auszahlungen", "geld", summe=True),
                  Spalte("saldo", "Saldo", "geld", summe=True), Spalte("kumuliert", "Liquidität kumuliert", "geld")],
         beschreibung="Bankbestand plus erwartete Zahlungen aus offenen Posten nach Fälligkeit")
def liquiditaet(f):
    from . import bank as BK
    from .models import Bankkonto

    st = f["stichtag"]
    bank = sum((BK.bankstand(b) for b in Bankkonto.objects.filter(aktiv=True).select_related("konto")), NULL)
    perioden = [("Überfällig (bis Stichtag)", None, st), ("1–30 Tage", st + timedelta(days=1), st + timedelta(days=30)),
                ("31–60 Tage", st + timedelta(days=31), st + timedelta(days=60)),
                ("61–90 Tage", st + timedelta(days=61), st + timedelta(days=90)),
                ("> 90 Tage", st + timedelta(days=91), None)]
    ops = list(OffenerPosten.objects.exclude(offen=0))
    zeilen = [{"zeitraum": "Bankbestand aktuell", "einzahlungen": NULL, "auszahlungen": NULL, "saldo": bank,
               "kumuliert": bank}]
    kum = bank
    for name, ab, bis in perioden:
        def passt(op):
            return (ab is None or op.faellig_am >= ab) and (bis is None or op.faellig_am <= bis)
        ein = sum((op.offen for op in ops if op.art == "D" and passt(op)), NULL)
        aus = sum((op.offen for op in ops if op.art == "K" and passt(op)), NULL)
        kum += ein - aus
        zeilen.append({"zeitraum": name, "einzahlungen": ein, "auszahlungen": aus, "saldo": ein - aus, "kumuliert": kum})
    return {"zeilen": zeilen, "summen_extra": {"kumuliert": kum},
            "hinweise": ["Ohne Skontoabzug, Zahlungsverhalten oder Planwerte (Löhne, Steuern) – rein OP-basiert."]}


def _monate(von, bis):
    m = date(von.year, von.month, 1)
    while m <= bis:
        naechster = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
        yield m, min(naechster - timedelta(days=1), bis)
        m = naechster


@bericht("fibu.guv_monate", "GuV-Monatsvergleich", modul="fibu", filter=["von", "bis"],
         spalten=[Spalte("monat", "Monat"), Spalte("erloese", "Umsatzerlöse", "geld", summe=True),
                  Spalte("sonst_ertrag", "Sonstige Erträge/BV", "geld", summe=True),
                  Spalte("material", "Materialaufwand", "geld", summe=True),
                  Spalte("personal", "Personalaufwand", "geld", summe=True),
                  Spalte("sonst_aufwand", "Sonstiger Aufwand", "geld", summe=True),
                  Spalte("ergebnis", "Ergebnis", "geld", summe=True)],
         beschreibung="Gliederung nach § 275 HGB (GKV), zusammengefasst je Monat", quer=True,
         diagramm={"typ": "bar", "label": "monat", "wert": "ergebnis"})
def guv_monate(f):
    zeilen = []
    for anfang, ende in _monate(f["von"], f["bis"]):
        w = A.guv_daten(anfang, ende)["werte"]
        erl = w.get("1", NULL)
        sonst = w.get("2", NULL) + w.get("4", NULL) + w.get("11", NULL)
        mat = -(w.get("5a", NULL) + w.get("5b", NULL))
        pers = -(w.get("6a", NULL) + w.get("6b", NULL))
        ergebnis = sum(w.values(), NULL)
        zeilen.append({"monat": anfang.strftime("%Y-%m"), "erloese": erl, "sonst_ertrag": sonst, "material": mat,
                       "personal": pers, "sonst_aufwand": erl + sonst - mat - pers - ergebnis, "ergebnis": ergebnis})
    return zeilen


@bericht("fibu.deckungsbeitrag", "Deckungsbeitrag (einfach)", modul="fibu", filter=["von", "bis", "db_gruppe", "kunde"],
         spalten=[Spalte("gruppe", "Artikel / Kunde"), Spalte("umsatz", "Umsatz netto", "geld", summe=True),
                  Spalte("einsatz", "Waren-/Materialeinsatz", "geld", summe=True),
                  Spalte("db", "Deckungsbeitrag I", "geld", summe=True), Spalte("db_quote", "DB-Quote", "prozent")],
         beschreibung="Umsatz festgeschriebener Rechnungen − Menge × aktueller Durchschnittspreis (ersatzweise EK-Preis)")
def deckungsbeitrag(f):
    from apps.lager.models import ArtikelLager
    from apps.vertrieb.berichte import umsatz_positionen

    preise = dict(ArtikelLager.objects.values_list("artikel_id", "durchschnittspreis"))
    daten = defaultdict(lambda: [NULL, NULL])
    for p, betrag, menge in umsatz_positionen(f["von"], f["bis"], f.get("kunde")):
        if f.get("db_gruppe") == "kunde":
            key = p.beleg.partner.name
        else:
            key = f"{p.artikel.artikelnummer} {p.artikel.bezeichnung}" if p.artikel else "(ohne Artikel)"
        preis = (preise.get(p.artikel_id) or p.artikel.einkaufspreis or NULL) if p.artikel else NULL
        daten[key][0] += betrag
        daten[key][1] += (menge * preis).quantize(Decimal("0.01"))

    def quote(u, db):
        return (db / u * 100).quantize(Decimal("0.1")) if u else None
    zeilen = [{"gruppe": k, "umsatz": u, "einsatz": e, "db": u - e, "db_quote": quote(u, u - e)}
              for k, (u, e) in daten.items()]
    u = sum((z["umsatz"] for z in zeilen), NULL)
    db = sum((z["db"] for z in zeilen), NULL)
    return {"zeilen": sorted(zeilen, key=lambda z: -z["db"]), "summen_extra": {"db_quote": quote(u, db) or NULL},
            "hinweise": ["Vereinfachung: Einsatz zum aktuellen gleitenden Durchschnittspreis, keine Fixkosten."]}

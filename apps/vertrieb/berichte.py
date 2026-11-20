"""Standardberichte Vertrieb (Registrierung in der Reporting-Registry, nur lesend)."""
from collections import defaultdict
from decimal import Decimal

from django.db.models import F

from apps.belege.models import Beleg, BelegPosition
from apps.reporting.registry import Spalte, bericht

from .models import Auftrag, AuftragPosition

NULL = Decimal(0)
HUNDERT = Decimal(100)
OFFEN = [Auftrag.Status.ERFASST, Auftrag.Status.BESTAETIGT, Auftrag.Status.TEILGELIEFERT]


def prozent(teil, ganzes):
    return (Decimal(teil) / Decimal(ganzes) * HUNDERT).quantize(Decimal("0.1")) if ganzes else None


def umsatz_positionen(von, bis, kunde=None, artikel=None):
    """Positionen festgeschriebener Rechnungen/Gutschriften im Zeitraum (Gutschriften negativ)."""
    qs = BelegPosition.objects.filter(
        beleg__belegart__in=[Beleg.Belegart.RECHNUNG, Beleg.Belegart.GUTSCHRIFT],
        beleg__status=Beleg.Status.FESTGESCHRIEBEN, beleg__belegdatum__gte=von, beleg__belegdatum__lte=bis)
    if kunde:
        qs = qs.filter(beleg__partner=kunde)
    if artikel:
        qs = qs.filter(artikel=artikel)
    for p in qs.select_related("beleg", "beleg__partner", "beleg__erstellt_von", "artikel"):
        vorzeichen = -1 if p.beleg.belegart == Beleg.Belegart.GUTSCHRIFT else 1
        yield p, p.betrag_netto * vorzeichen, p.menge * vorzeichen


def umsatz_summe(von, bis):
    return sum((b for _, b, _ in umsatz_positionen(von, bis)), NULL)


def _mitarbeiter_je_rechnung():
    from .models import Lieferung

    return {l["rechnung_id"]: l["auftrag__zustaendig__username"]
            for l in Lieferung.objects.filter(rechnung__isnull=False, auftrag__zustaendig__isnull=False)
            .values("rechnung_id", "auftrag__zustaendig__username")}


def _umsatz(f, gruppe):
    daten = defaultdict(lambda: {"umsatz": NULL, "menge": NULL, "belege": set()})
    ma = _mitarbeiter_je_rechnung() if gruppe == "mitarbeiter" else {}
    for p, betrag, menge in umsatz_positionen(f["von"], f["bis"], f.get("kunde"), f.get("artikel")):
        b = p.beleg
        if gruppe == "monat":
            key = b.belegdatum.strftime("%Y-%m")
        elif gruppe == "kunde":
            key = f"{b.partner.nummer} {b.partner.name}"
        elif gruppe == "artikel":
            key = f"{p.artikel.artikelnummer} {p.artikel.bezeichnung}" if p.artikel else f"(ohne Artikel) {p.bezeichnung}"
        else:
            key = ma.get(b.pk) or (b.erstellt_von.username if b.erstellt_von else "(unbekannt)")
        d = daten[key]
        d["umsatz"] += betrag
        d["menge"] += menge
        d["belege"].add(b.pk)
    gesamt = sum((d["umsatz"] for d in daten.values()), NULL)
    zeilen = [{"gruppe": k, "belege": len(d["belege"]), "menge": d["menge"], "umsatz": d["umsatz"],
               "anteil": prozent(d["umsatz"], gesamt)} for k, d in daten.items()]
    zeilen.sort(key=lambda z: z["gruppe"] if gruppe == "monat" else -z["umsatz"])
    return {"zeilen": zeilen, "summen_extra": {"anteil": HUNDERT if zeilen else NULL},
            "hinweise": ["Umsatz = Nettobeträge festgeschriebener Rechnungen abzüglich Gutschriften."]}


def _umsatz_spalten(label, mit_menge=False):
    sp = [Spalte("gruppe", label), Spalte("belege", "Belege", "zahl", summe=True)]
    if mit_menge:
        sp.append(Spalte("menge", "Menge", "menge", summe=True))
    return sp + [Spalte("umsatz", "Umsatz netto", "geld", summe=True), Spalte("anteil", "Anteil", "prozent")]


FILTER_UMSATZ = ["von", "bis", "kunde", "artikel"]


@bericht("vertrieb.umsatz_monat", "Umsatz nach Monat", modul="vertrieb", filter=FILTER_UMSATZ,
         spalten=_umsatz_spalten("Monat"), beschreibung="Nettoumsatz je Monat (Rechnungen − Gutschriften)",
         diagramm={"typ": "line", "label": "gruppe", "wert": "umsatz"})
def umsatz_monat(f):
    return _umsatz(f, "monat")


@bericht("vertrieb.umsatz_kunde", "Umsatz nach Kunde", modul="vertrieb", filter=FILTER_UMSATZ,
         spalten=_umsatz_spalten("Kunde"), beschreibung="Nettoumsatz je Kunde, absteigend",
         diagramm={"typ": "bar", "label": "gruppe", "wert": "umsatz"})
def umsatz_kunde(f):
    return _umsatz(f, "kunde")


@bericht("vertrieb.umsatz_artikel", "Umsatz nach Artikel", modul="vertrieb", filter=FILTER_UMSATZ,
         spalten=_umsatz_spalten("Artikel", True), beschreibung="Nettoumsatz und Menge je Artikel")
def umsatz_artikel(f):
    return _umsatz(f, "artikel")


@bericht("vertrieb.umsatz_mitarbeiter", "Umsatz nach Vertriebsmitarbeiter", modul="vertrieb", filter=FILTER_UMSATZ,
         spalten=_umsatz_spalten("Mitarbeiter"),
         beschreibung="Zuordnung über den zuständigen Mitarbeiter des Auftrags, sonst Ersteller der Rechnung")
def umsatz_mitarbeiter(f):
    return _umsatz(f, "mitarbeiter")


def _offene_positionen(kunde=None):
    qs = (AuftragPosition.objects.filter(auftrag__status__in=OFFEN, menge__gt=F("menge_geliefert"))
          .select_related("auftrag", "auftrag__partner", "artikel").order_by("auftrag__nummer", "position"))
    if kunde:
        qs = qs.filter(auftrag__partner=kunde)
    for p in qs:
        offen = p.offen_liefern
        wert = (offen * p.einzelpreis * (1 - p.rabatt_prozent / HUNDERT)).quantize(Decimal("0.01"))
        yield p, offen, wert


def auftragsbestand_wert():
    return sum((w for _, _, w in _offene_positionen()), NULL)


@bericht("vertrieb.auftragsbestand", "Auftragsbestand", modul="vertrieb", filter=["kunde"],
         spalten=[Spalte("auftrag", "Auftrag"), Spalte("kunde", "Kunde"), Spalte("datum", "Bestelldatum", "datum"),
                  Spalte("termin", "Liefertermin", "datum"), Spalte("artikel", "Artikel"),
                  Spalte("offen", "Offene Menge", "menge"), Spalte("wert", "Offener Wert netto", "geld", summe=True)],
         beschreibung="Noch nicht gelieferte Auftragspositionen offener Aufträge")
def auftragsbestand(f):
    return [{"auftrag": p.auftrag.nummer, "kunde": p.auftrag.partner.name, "datum": p.auftrag.bestelldatum,
             "termin": p.liefertermin or p.auftrag.liefertermin, "artikel": p.artikel.artikelnummer if p.artikel
             else p.bezeichnung, "offen": offen, "wert": wert} for p, offen, wert in _offene_positionen(f.get("kunde"))]


@bericht("vertrieb.lieferrueckstand", "Lieferrückstand", modul="vertrieb", filter=["stichtag", "kunde"],
         spalten=[Spalte("auftrag", "Auftrag"), Spalte("kunde", "Kunde"), Spalte("artikel", "Artikel"),
                  Spalte("termin", "Liefertermin", "datum"), Spalte("verzug", "Verzug (Tage)", "zahl"),
                  Spalte("offen", "Offene Menge", "menge"), Spalte("wert", "Offener Wert netto", "geld", summe=True)],
         beschreibung="Offene Positionen, deren Liefertermin vor dem Stichtag liegt")
def lieferrueckstand(f):
    zeilen = []
    for p, offen, wert in _offene_positionen(f.get("kunde")):
        termin = p.liefertermin or p.auftrag.liefertermin
        if termin and termin < f["stichtag"]:
            zeilen.append({"auftrag": p.auftrag.nummer, "kunde": p.auftrag.partner.name,
                           "artikel": p.artikel.artikelnummer if p.artikel else p.bezeichnung, "termin": termin,
                           "verzug": (f["stichtag"] - termin).days, "offen": offen, "wert": wert})
    return sorted(zeilen, key=lambda z: -z["verzug"])


@bericht("vertrieb.angebotsquote", "Angebotserfolgsquote", modul="vertrieb", filter=["von", "bis", "kunde"],
         spalten=[Spalte("monat", "Monat"), Spalte("angebote", "Angebote", "zahl", summe=True),
                  Spalte("gewonnen", "davon Auftrag", "zahl", summe=True), Spalte("quote", "Quote (Anzahl)", "prozent"),
                  Spalte("wert", "Angebotswert netto", "geld", summe=True),
                  Spalte("wert_gewonnen", "Auftragswert netto", "geld", summe=True),
                  Spalte("quote_wert", "Quote (Wert)", "prozent")],
         beschreibung="Angebote im Zeitraum und Anteil, der in einen Auftrag übernommen wurde")
def angebotsquote(f):
    qs = Beleg.objects.filter(belegart=Beleg.Belegart.ANGEBOT, belegdatum__gte=f["von"], belegdatum__lte=f["bis"]
                              ).exclude(status=Beleg.Status.STORNIERT)
    if f.get("kunde"):
        qs = qs.filter(partner=f["kunde"])
    gewonnen = set(Auftrag.objects.filter(angebot__in=qs).exclude(status=Auftrag.Status.STORNIERT)
                   .values_list("angebot_id", flat=True))
    daten = defaultdict(lambda: [0, 0, NULL, NULL])
    for b in qs:
        d = daten[b.belegdatum.strftime("%Y-%m")]
        d[0] += 1
        d[2] += b.summe_netto
        if b.pk in gewonnen:
            d[1] += 1
            d[3] += b.summe_netto
    zeilen = [{"monat": m, "angebote": a, "gewonnen": g, "quote": prozent(g, a), "wert": w, "wert_gewonnen": wg,
               "quote_wert": prozent(wg, w)} for m, (a, g, w, wg) in sorted(daten.items())]
    n, g = sum(z["angebote"] for z in zeilen), sum(z["gewonnen"] for z in zeilen)
    w, wg = sum((z["wert"] for z in zeilen), NULL), sum((z["wert_gewonnen"] for z in zeilen), NULL)
    return {"zeilen": zeilen, "summen_extra": {"quote": prozent(g, n) or NULL, "quote_wert": prozent(wg, w) or NULL}}


@bericht("vertrieb.abc_kunden", "ABC-Analyse Kunden", modul="vertrieb", filter=["von", "bis"],
         spalten=[Spalte("rang", "Rang", "zahl"), Spalte("kunde", "Kunde"),
                  Spalte("umsatz", "Umsatz netto", "geld", summe=True), Spalte("anteil", "Anteil", "prozent"),
                  Spalte("kumuliert", "Kumuliert", "prozent"), Spalte("klasse", "Klasse")],
         beschreibung="A = bis 80 % des Umsatzes, B = bis 95 %, C = Rest",
         diagramm={"typ": "bar", "label": "kunde", "wert": "umsatz"})
def abc_kunden(f):
    umsatz = defaultdict(lambda: NULL)
    for p, betrag, _ in umsatz_positionen(f["von"], f["bis"]):
        umsatz[p.beleg.partner.name] += betrag
    gesamt = sum(umsatz.values(), NULL)
    zeilen, kum = [], NULL
    for rang, (kunde, wert) in enumerate(sorted(umsatz.items(), key=lambda e: -e[1]), start=1):
        vorher = prozent(kum, gesamt) or NULL
        kum += wert
        zeilen.append({"rang": rang, "kunde": kunde, "umsatz": wert, "anteil": prozent(wert, gesamt),
                       "kumuliert": prozent(kum, gesamt),
                       "klasse": "A" if vorher < 80 else ("B" if vorher < 95 else "C")})
    return {"zeilen": zeilen, "summen_extra": {"anteil": HUNDERT if zeilen else NULL},
            "hinweise": ["Klasse nach kumuliertem Umsatzanteil vor dem jeweiligen Kunden (der größte Kunde ist stets A)."]}


def top_kunden(von, bis, n=10):
    return abc_kunden({"von": von, "bis": bis})["zeilen"][:n]


def auftragseingang(von, bis):
    """Nettowert der im Zeitraum erfassten Aufträge (ohne Stornos)."""
    summe = NULL
    for p in AuftragPosition.objects.filter(auftrag__bestelldatum__gte=von, auftrag__bestelldatum__lte=bis).exclude(
            auftrag__status=Auftrag.Status.STORNIERT):
        summe += (p.menge * p.einzelpreis * (1 - p.rabatt_prozent / HUNDERT)).quantize(Decimal("0.01"))
    return summe


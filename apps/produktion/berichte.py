"""Standardberichte Produktion (nur lesend). Personenbezogene Leistungsgrade erfordern das Recht `akkord`."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.conf import settings

from apps.reporting.registry import Spalte, bericht

from . import services
from .models import FAArbeitsgang, Fertigungsauftrag, Rueckmeldung, Stempelung

NULL = Decimal(0)
EIN = Decimal("0.1")


def _lg(vorgabe, ist):
    return (vorgabe / ist * 100).quantize(EIN) if ist else None


def _stempelungen(von, bis):
    anfang, ende = services._zeitraum(von, bis)
    return Stempelung.objects.filter(beginn__gte=anfang, beginn__lt=ende, ende__isnull=False)


@bericht("produktion.leistungsgrad_mitarbeiter", "Leistungsgrad je Mitarbeiter", modul="akkord",
         filter=["von", "bis"],
         spalten=[Spalte("personalnummer", "Pers.-Nr."), Spalte("name", "Mitarbeiter"),
                  Spalte("ist", "Ist-Zeit Akkord", "stunden", summe=True),
                  Spalte("vorgabe", "Vorgabezeit", "stunden", summe=True),
                  Spalte("leistungsgrad", "Leistungsgrad", "prozent"),
                  Spalte("zeitlohn", "Zeitlohn-Stunden", "stunden", summe=True)],
         beschreibung="Aus der Akkordauswertung (Recht „Akkord“ erforderlich, personenbezogen)")
def leistungsgrad_mitarbeiter(f):
    daten = services.akkord_auswertung(f["von"], f["bis"])
    zeilen = [{"personalnummer": z["mitarbeiter"].personalnummer, "name": f"{z['mitarbeiter'].vorname} {z['mitarbeiter'].nachname}",
               "ist": (z["ist_akkord_min"] / 60).quantize(EIN), "vorgabe": (z["vorgabe_min"] / 60).quantize(EIN),
               "leistungsgrad": z["leistungsgrad"], "zeitlohn": (z["ist_zeit_min"] / 60).quantize(EIN)}
              for z in daten["zeilen"]]
    ist = sum((z["ist"] for z in zeilen), NULL)
    vorgabe = sum((z["vorgabe"] for z in zeilen), NULL)
    return {"zeilen": sorted(zeilen, key=lambda z: z["name"]), "summen_extra": {"leistungsgrad": _lg(vorgabe, ist) or NULL},
            "hinweise": ["Leistungsgrad = Vorgabezeit (Rüsten + Stückzeit × Gutmenge) / Ist-Zeit. "
                         "Mitbestimmungspflichtig und datenschutzrelevant."]}


def _arbeitsgang_werte(von, bis):
    """{arbeitsgang: Ist-Minuten} für Stempelungen im Zeitraum."""
    ist = defaultdict(lambda: NULL)
    for st in _stempelungen(von, bis).select_related("arbeitsgang"):
        ist[st.arbeitsgang_id] += st.dauer_min
    return ist


def leistungsgrad_gesamt(von, bis):
    ist_min = _arbeitsgang_werte(von, bis)
    vorgabe = ist = NULL
    for ag in FAArbeitsgang.objects.filter(pk__in=ist_min.keys(), gut_menge__gt=0):
        vorgabe += ag.ruestzeit_min + ag.stueckzeit_min * ag.gut_menge
        ist += ist_min[ag.pk]
    return _lg(vorgabe, ist)


@bericht("produktion.leistungsgrad_arbeitsplatz", "Leistungsgrad je Arbeitsplatz", modul="produktion",
         filter=["von", "bis", "kostenstelle"],
         spalten=[Spalte("arbeitsplatz", "Arbeitsplatz"), Spalte("kostenstelle", "Kostenstelle"),
                  Spalte("gaenge", "Arbeitsgänge", "zahl", summe=True), Spalte("gut", "Gutmenge", "menge", summe=True),
                  Spalte("ist", "Ist-Zeit", "stunden", summe=True), Spalte("vorgabe", "Vorgabezeit", "stunden", summe=True),
                  Spalte("leistungsgrad", "Leistungsgrad", "prozent")],
         beschreibung="Auftragsbezogen ohne Personenbezug: Vorgabe der Gutmenge / gestempelte Ist-Zeit")
def leistungsgrad_arbeitsplatz(f):
    ist_min = _arbeitsgang_werte(f["von"], f["bis"])
    daten = defaultdict(lambda: {"n": 0, "gut": NULL, "ist": NULL, "vg": NULL, "ks": ""})
    for ag in FAArbeitsgang.objects.filter(pk__in=ist_min.keys()).select_related("arbeitsplatz"):
        if f.get("kostenstelle") and (not ag.arbeitsplatz or ag.arbeitsplatz.kostenstelle != f["kostenstelle"]):
            continue
        d = daten[str(ag.arbeitsplatz) if ag.arbeitsplatz else "(ohne Arbeitsplatz)"]
        d["ks"] = ag.arbeitsplatz.kostenstelle if ag.arbeitsplatz else ""
        d["n"] += 1
        d["gut"] += ag.gut_menge
        d["ist"] += ist_min[ag.pk]
        if ag.gut_menge:
            d["vg"] += ag.ruestzeit_min + ag.stueckzeit_min * ag.gut_menge
    zeilen = [{"arbeitsplatz": k, "kostenstelle": d["ks"], "gaenge": d["n"], "gut": d["gut"],
               "ist": (d["ist"] / 60).quantize(EIN), "vorgabe": (d["vg"] / 60).quantize(EIN),
               "leistungsgrad": _lg(d["vg"], d["ist"])} for k, d in sorted(daten.items())]
    ist = sum((d["ist"] for d in daten.values()), NULL)
    vg = sum((d["vg"] for d in daten.values()), NULL)
    return {"zeilen": zeilen, "summen_extra": {"leistungsgrad": _lg(vg, ist) or NULL}}


def ausschuss_summen(von, bis):
    anfang, ende = services._zeitraum(von, bis)
    gut = schlecht = NULL
    for r in Rueckmeldung.objects.filter(zeitpunkt__gte=anfang, zeitpunkt__lt=ende):
        gut += r.gut_menge
        schlecht += r.ausschuss_menge
    return gut, schlecht


@bericht("produktion.ausschuss", "Ausschussquote", modul="produktion",
         filter=["von", "bis", "ausschuss_gruppe", "artikel"],
         spalten=[Spalte("gruppe", "Artikel / Grund"), Spalte("gut", "Gutmenge", "menge", summe=True),
                  Spalte("ausschuss", "Ausschuss", "menge", summe=True),
                  Spalte("nacharbeit", "Nacharbeit", "menge", summe=True), Spalte("quote", "Ausschussquote", "prozent")],
         beschreibung="Quote = Ausschuss / (Gut + Ausschuss) aus den Rückmeldungen",
         diagramm={"typ": "bar", "label": "gruppe", "wert": "ausschuss"})
def ausschuss(f):
    anfang, ende = services._zeitraum(f["von"], f["bis"])
    qs = Rueckmeldung.objects.filter(zeitpunkt__gte=anfang, zeitpunkt__lt=ende).select_related(
        "arbeitsgang__auftrag__artikel")
    if f.get("artikel"):
        qs = qs.filter(arbeitsgang__auftrag__artikel=f["artikel"])
    gruende = dict(Rueckmeldung.Grund.choices)
    nach_grund = f.get("ausschuss_gruppe") == "grund"
    daten = defaultdict(lambda: [NULL, NULL, NULL])
    for r in qs:
        if nach_grund:
            if not r.ausschuss_menge:
                continue
            key = gruende.get(r.ausschuss_grund, r.ausschuss_grund or "(ohne Grund)")
        else:
            a = r.arbeitsgang.auftrag.artikel
            key = f"{a.artikelnummer} {a.bezeichnung}"
        d = daten[key]
        d[0] += r.gut_menge
        d[1] += r.ausschuss_menge
        d[2] += r.nacharbeit_menge
    def quote(g, s):
        return (s / (g + s) * 100).quantize(EIN) if (g + s) else None
    zeilen = [{"gruppe": k, "gut": g, "ausschuss": s, "nacharbeit": n, "quote": quote(g, s)}
              for k, (g, s, n) in sorted(daten.items())]
    g, s = sum((z["gut"] for z in zeilen), NULL), sum((z["ausschuss"] for z in zeilen), NULL)
    hinweise = ["Bei Gruppierung nach Grund wird die Gutmenge nur aus Rückmeldungen mit Ausschuss gezählt."] \
        if nach_grund else []
    return {"zeilen": zeilen, "summen_extra": {"quote": quote(g, s) or NULL}, "hinweise": hinweise}


@bericht("produktion.nachkalkulation", "Nachkalkulation Soll/Ist", modul="produktion", filter=["von", "bis", "artikel"],
         spalten=[Spalte("auftrag", "Auftrag"), Spalte("artikel", "Artikel"), Spalte("status", "Status"),
                  Spalte("menge", "Menge", "menge"), Spalte("gut", "Gutmenge", "menge"),
                  Spalte("soll", "Soll-Kosten", "geld", summe=True), Spalte("ist", "Ist-Kosten", "geld", summe=True),
                  Spalte("abweichung", "Abweichung", "geld", summe=True), Spalte("prozent", "Abw. %", "prozent")],
         beschreibung="Herstellkosten je Auftrag; Soll-Basis: Gutmenge (abgeschlossen) bzw. Auftragsmenge (offen)")
def nachkalkulation(f):
    qs = (Fertigungsauftrag.objects.exclude(status__in=[Fertigungsauftrag.Status.ENTWURF,
                                                         Fertigungsauftrag.Status.STORNIERT])
          .filter(erstellt_am__date__gte=f["von"], erstellt_am__date__lte=f["bis"])
          .select_related("artikel").order_by("nummer"))
    if f.get("artikel"):
        qs = qs.filter(artikel=f["artikel"])
    zeilen = []
    for fa in qs:
        summe = services.nachkalkulation(fa)["zeilen"][-1]
        soll = summe["ist"] - summe["abweichung"]
        zeilen.append({"auftrag": fa.nummer, "artikel": fa.artikel.artikelnummer, "status": fa.get_status_display(),
                       "menge": fa.menge, "gut": fa.gut_menge, "soll": soll, "ist": summe["ist"],
                       "abweichung": summe["abweichung"], "prozent": summe["prozent"]})
    soll = sum((z["soll"] for z in zeilen), NULL)
    abw = sum((z["abweichung"] for z in zeilen), NULL)
    return {"zeilen": zeilen, "summen_extra": {"prozent": (abw / soll * 100).quantize(EIN) if soll else NULL}}


@bericht("produktion.auslastung", "Auslastung der Arbeitsplätze", modul="produktion",
         filter=["von", "bis", "kostenstelle"],
         spalten=[Spalte("arbeitsplatz", "Arbeitsplatz"), Spalte("kostenstelle", "Kostenstelle"),
                  Spalte("ruesten", "Rüsten", "stunden", summe=True), Spalte("fertigen", "Fertigen", "stunden", summe=True),
                  Spalte("belegt", "Belegt gesamt", "stunden", summe=True),
                  Spalte("kapazitaet", "Kapazität", "stunden", summe=True), Spalte("auslastung", "Auslastung", "prozent")],
         beschreibung="Gestempelte Zeit je Arbeitsplatz gegen Kapazität (Arbeitstage Mo–Fr × Stunden je Tag)")
def auslastung(f):
    from apps.stammdaten.models import Arbeitsplatz

    std_tag = Decimal(str(getattr(settings, "ERP_KAPAZITAET_STUNDEN_TAG", 8)))
    tage = sum(1 for i in range((f["bis"] - f["von"]).days + 1) if (f["von"] + timedelta(days=i)).weekday() < 5)
    kapazitaet = std_tag * tage
    belegt = defaultdict(lambda: [NULL, NULL])
    for st in _stempelungen(f["von"], f["bis"]).select_related("arbeitsgang"):
        belegt[st.arbeitsgang.arbeitsplatz_id][0 if st.taetigkeit == Stempelung.Taetigkeit.RUESTEN else 1] += st.dauer_min
    qs = Arbeitsplatz.objects.filter(aktiv=True).order_by("code")
    if f.get("kostenstelle"):
        qs = qs.filter(kostenstelle=f["kostenstelle"])
    zeilen = []
    for ap in qs:
        r, fe = belegt.get(ap.pk, (NULL, NULL))
        summe = ((r + fe) / 60).quantize(EIN)
        zeilen.append({"arbeitsplatz": str(ap), "kostenstelle": ap.kostenstelle, "ruesten": (r / 60).quantize(EIN),
                       "fertigen": (fe / 60).quantize(EIN), "belegt": summe, "kapazitaet": kapazitaet,
                       "auslastung": (summe / kapazitaet * 100).quantize(EIN) if kapazitaet else None})
    gesamt_b = sum((z["belegt"] for z in zeilen), NULL)
    gesamt_k = kapazitaet * len(zeilen)
    return {"zeilen": zeilen,
            "summen_extra": {"auslastung": (gesamt_b / gesamt_k * 100).quantize(EIN) if gesamt_k else NULL},
            "hinweise": [f"Kapazität: {tage} Arbeitstage × {std_tag} h (Einstellung ERP_KAPAZITAET_STUNDEN_TAG), "
                         "ohne Schichtmodell/Feiertage."]}

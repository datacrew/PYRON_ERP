"""Tourenplanung und LVS-Schnittstelle.

Tourenbildung: offene Lieferscheine (noch keiner Tour zugeordnet, Lieferdatum ≤ Tourdatum) werden der Region
mit dem spezifischsten passenden PLZ-Bereich zugeordnet – nur, wenn die Region am Tourdatum beliefert wird.
Je Region werden die Stopps nach PLZ bzw. Entfernung (Nächster-Nachbar ab Firmensitz, Haversine über lokale
PLZ-Koordinaten) sortiert und auf Fahrzeuge verteilt, ohne Gewicht/Paletten zu überschreiten.

LVS (Lagerverwaltungssoftware): Export der Kommissionieraufträge als JSON/CSV, Import der Rückmeldungen
(kommissionierte Mengen, Chargen, Packstücke). Der Lagerabgang wird danach – optional sofort – über
apps.vertrieb.services.warenausgang_buchen gebucht (chargengenau, Lagerjournal).
"""
import csv
import io
import json
import math
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from apps.core.events import veroeffentlichen
from apps.core.models import Mandant
from apps.lager.models import Charge
from apps.vertrieb import services as VS
from apps.vertrieb.models import LieferPosition, Lieferung, VertriebFehler

from .models import Fahrzeug, LvsProtokoll, PlzKoordinate, Region, Tour, TourStopp, VersandFehler

PLANBAR = [Lieferung.Status.ERFASST, Lieferung.Status.KOMMISSIONIERUNG, Lieferung.Status.KOMMISSIONIERT,
           Lieferung.Status.GEBUCHT]


def _user(b):
    return b if getattr(b, "pk", None) else None


# ---------------------------------------------------------------- Regionen / Geodaten

def region_fuer(plz, land="DE", regionen=None):
    bester, wert = None, 0
    for r in regionen if regionen is not None else Region.objects.filter(aktiv=True):
        n = r.passt(plz, land)
        if n > wert:
            bester, wert = r, n
    return bester


def naechster_liefertag(region, ab=None):
    tag = ab or timezone.localdate()
    if not region.tage():
        return None
    for i in range(8):
        if region.liefert_am(tag + timedelta(days=i)):
            return tag + timedelta(days=i)
    return None


def koordinate(plz, land="DE"):
    k = PlzKoordinate.objects.filter(land=land or "DE", plz=(plz or "").strip()).first()
    return (float(k.breite), float(k.laenge)) if k else None


def entfernung_km(a, b):
    """Luftlinie (Haversine) zwischen zwei (Breite, Länge)-Paaren in km."""
    if not a or not b:
        return None
    r = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def sortieren(lieferungen, art="PLZ"):
    """Liste von Lieferungen → [(Lieferung, km vom Vorgänger | None)] sortiert nach PLZ oder Entfernung."""
    nach_plz = sorted(lieferungen, key=lambda lf: (lf.plz, lf.ort, lf.pk))
    if art != "ENTFERNUNG":
        return [(lf, None) for lf in nach_plz]
    m = Mandant.aktuell()
    pos = koordinate(m.plz, m.land) if m else None
    mit = [(lf, koordinate(lf.plz, lf.land)) for lf in nach_plz]
    if pos is None or any(k is None for _, k in mit):
        return [(lf, None) for lf in nach_plz]  # ohne vollständige Koordinaten: PLZ-Sortierung
    rest, ergebnis = mit, []
    while rest:
        lf, k = min(rest, key=lambda x: entfernung_km(pos, x[1]))
        ergebnis.append((lf, Decimal(str(round(entfernung_km(pos, k), 1)))))
        rest = [x for x in rest if x[0].pk != lf.pk]
        pos = k
    return ergebnis


# ---------------------------------------------------------------- Touren

def offene_lieferungen(bis_datum=None):
    qs = Lieferung.objects.filter(status__in=PLANBAR, tourstopp__isnull=True).select_related("auftrag__partner",
                                                                                           "beleg")
    if bis_datum:
        qs = qs.filter(lieferdatum__lte=bis_datum)
    return qs


def _passt_auf(fahrzeug, summe_kg, summe_pal, lf):
    if fahrzeug is None:
        return True
    return summe_kg + lf.gewicht <= fahrzeug.max_kg and summe_pal + lf.paletten <= fahrzeug.max_paletten


def kapazitaet_pruefen(tour, lieferung=None):
    """Liste der Überschreitungen (leer = passt) – optional mit zusätzlichem Lieferschein."""
    a = tour.auslastung()
    f = tour.fahrzeug
    if not f:
        return []
    kg = a["kg"] + (lieferung.gewicht if lieferung else 0)
    pal = a["paletten"] + (lieferung.paletten if lieferung else 0)
    fehler = []
    if kg > f.max_kg:
        fehler.append(f"Gewicht {kg:.0f} kg überschreitet die Zuladung von {f.kennzeichen} ({f.max_kg:.0f} kg).")
    if pal > f.max_paletten:
        fehler.append(f"{pal:.1f} Paletten überschreiten die Stellplätze von {f.kennzeichen} "
                      f"({f.max_paletten:.1f}).")
    return fehler


def stopp_hinzufuegen(tour, lieferung, erzwingen=False):
    if tour.status not in (Tour.Status.GEPLANT, Tour.Status.FREIGEGEBEN):
        raise VersandFehler(f"Tour {tour} ist bereits {tour.get_status_display().lower()}.")
    if lieferung.status not in PLANBAR:
        raise VersandFehler(f"{lieferung} kann nicht verplant werden (Status {lieferung.get_status_display()}).")
    if hasattr(lieferung, "tourstopp"):
        raise VersandFehler(f"{lieferung} ist bereits Tour {lieferung.tourstopp.tour} zugeordnet.")
    fehler = kapazitaet_pruefen(tour, lieferung)
    if fehler and not erzwingen:
        raise VersandFehler(" ".join(fehler))
    letzte = tour.stopps.order_by("-reihenfolge").first()
    return TourStopp.objects.create(tour=tour, lieferung=lieferung,
                                    reihenfolge=(letzte.reihenfolge + 10) if letzte else 10)


def stopp_entfernen(stopp):
    if stopp.tour.status not in (Tour.Status.GEPLANT, Tour.Status.FREIGEGEBEN):
        raise VersandFehler("Stopps können nur aus geplanten Touren entfernt werden.")
    stopp.delete()


def stopps_sortieren(tour):
    stopps = {s.lieferung_id: s for s in tour.stopps.select_related("lieferung")}
    for i, (lf, km) in enumerate(sortieren([s.lieferung for s in stopps.values()], tour.region.sortierung), 1):
        s = stopps[lf.pk]
        s.reihenfolge, s.entfernung_km = i * 10, km
        s.save(update_fields=["reihenfolge", "entfernung_km"])
    return tour


def touren_bilden(datum=None, benutzer=None, regionen=None):
    """Verplant offene Lieferscheine auf Touren am `datum`. Rückgabe: dict(touren, nicht_zugeordnet)."""
    datum = datum or timezone.localdate()
    alle_regionen = list(Region.objects.filter(aktiv=True).select_related("standard_fahrzeug"))
    if regionen is not None:
        erlaubt = {r.pk for r in regionen}
    else:
        erlaubt = {r.pk for r in alle_regionen}
    gruppen, nicht = {}, []
    for lf in offene_lieferungen(datum):
        r = region_fuer(lf.plz, lf.land, alle_regionen)
        if r is None:
            nicht.append((lf, f"Keine Region für PLZ {lf.plz} ({lf.land})."))
        elif r.pk not in erlaubt:
            continue
        elif not r.liefert_am(datum):
            nicht.append((lf, f"Region {r.code} wird am {datum:%d.%m.%Y} nicht beliefert "
                              f"(Liefertage {r.liefertage_text}, nächster {naechster_liefertag(r, datum):%d.%m.}).")
                         if naechster_liefertag(r, datum) else (lf, f"Region {r.code} hat keine Liefertage."))
        else:
            gruppen.setdefault(r, []).append(lf)
    touren = []
    with transaction.atomic():
        belegt = set(Tour.objects.filter(datum=datum).exclude(fahrzeug=None).values_list("fahrzeug_id", flat=True))
        for region, lfs in sorted(gruppen.items(), key=lambda x: x[0].code):
            offen = [lf for lf, _ in sortieren(lfs, region.sortierung)]
            # bestehende geplante Touren der Region an diesem Tag zuerst auffüllen
            kandidaten = list(Tour.objects.filter(datum=datum, region=region, status=Tour.Status.GEPLANT))
            while offen:
                if kandidaten:
                    tour = kandidaten.pop(0)
                else:
                    fahrzeug = region.standard_fahrzeug if region.standard_fahrzeug_id and \
                        region.standard_fahrzeug.aktiv and region.standard_fahrzeug_id not in belegt else None
                    if fahrzeug is None:
                        fahrzeug = Fahrzeug.objects.filter(aktiv=True).exclude(pk__in=belegt).order_by(
                            "-max_kg").first()
                    if fahrzeug is None:
                        nicht += [(lf, "Kein freies Fahrzeug mehr verfügbar.") for lf in offen]
                        break
                    belegt.add(fahrzeug.pk)
                    tour = Tour.objects.create(datum=datum, region=region, fahrzeug=fahrzeug,
                                               fahrer=fahrzeug.fahrer, erstellt_von=_user(benutzer))
                s = tour.summen()
                kg, pal = s["kg"], s["paletten"]
                rest = []
                for lf in offen:
                    if _passt_auf(tour.fahrzeug, kg, pal, lf):
                        stopp_hinzufuegen(tour, lf, erzwingen=True)
                        kg += lf.gewicht
                        pal += lf.paletten
                    else:
                        rest.append(lf)
                if len(rest) == len(offen):
                    if not tour.stopps.exists():
                        # einzelner Lieferschein größer als das Fahrzeug
                        nicht += [(lf, "Lieferschein überschreitet allein die Fahrzeugkapazität.") for lf in rest[:1]]
                        rest = rest[1:]
                        tour.delete()
                        belegt.discard(tour.fahrzeug_id)
                        offen = rest
                        continue
                stopps_sortieren(tour)
                if tour not in touren:
                    touren.append(tour)
                offen = rest
    return {"touren": touren, "nicht_zugeordnet": nicht}


def tour_freigeben(tour, benutzer=None):
    if tour.status != Tour.Status.GEPLANT:
        raise VersandFehler("Nur geplante Touren können freigegeben werden.")
    if not tour.stopps.exists():
        raise VersandFehler("Die Tour hat keine Stopps.")
    fehler = kapazitaet_pruefen(tour)
    if fehler:
        raise VersandFehler("Kapazität überschritten: " + " ".join(fehler))
    tour.status = Tour.Status.FREIGEGEBEN
    tour.save(update_fields=["status"])
    return tour


def tour_verladen(tour, benutzer=None):
    """Alle Lieferscheine müssen gebucht sein (Warenausgang); Status → VERLADEN."""
    if tour.status not in (Tour.Status.GEPLANT, Tour.Status.FREIGEGEBEN):
        raise VersandFehler("Die Tour ist bereits unterwegs oder abgeschlossen.")
    fehler = kapazitaet_pruefen(tour)
    if fehler:
        raise VersandFehler("Kapazität überschritten: " + " ".join(fehler))
    with transaction.atomic():
        for s in tour.stopps.select_related("lieferung"):
            if s.lieferung.status != Lieferung.Status.GEBUCHT:
                raise VersandFehler(f"{s.lieferung}: Warenausgang noch nicht gebucht "
                                    f"({s.lieferung.get_status_display()}).")
        Lieferung.objects.filter(tourstopp__tour=tour).update(status=Lieferung.Status.VERLADEN)
        tour.status = Tour.Status.UNTERWEGS
        tour.save(update_fields=["status"])
    veroeffentlichen("versand.tour_verladen", tour=tour, benutzer=benutzer)
    return tour


def zugestellt(lieferung, benutzer=None):
    if lieferung.status not in (Lieferung.Status.VERLADEN, Lieferung.Status.GEBUCHT):
        raise VersandFehler(f"{lieferung} ist nicht unterwegs.")
    lieferung.status, lieferung.zugestellt_am = Lieferung.Status.ZUGESTELLT, timezone.now()
    lieferung.save(update_fields=["status", "zugestellt_am"])
    veroeffentlichen("versand.zugestellt", lieferung=lieferung, benutzer=benutzer)
    stopp = getattr(lieferung, "tourstopp", None)
    if stopp and not stopp.tour.stopps.exclude(lieferung__status=Lieferung.Status.ZUGESTELLT).exists():
        stopp.tour.status = Tour.Status.ABGESCHLOSSEN
        stopp.tour.save(update_fields=["status"])
    return lieferung


# ---------------------------------------------------------------- LVS-Schnittstelle

CSV_EXPORT = ["referenz", "lieferschein_id", "position_id", "artikelnummer", "bezeichnung", "menge", "einheit",
              "charge_vorgabe", "chargenpflicht", "lagerort", "empfaenger", "plz", "ort", "lieferdatum", "tour"]
CSV_IMPORT = ["referenz", "position_id", "menge", "charge", "packstueck", "packstuecke", "gewicht_kg", "paletten"]


def kommissionierauftraege(lieferungen):
    daten = []
    for lf in lieferungen:
        tour = getattr(lf, "tourstopp", None)
        daten.append({
            "referenz": lf.kommissionier_referenz, "lieferschein_id": lf.pk, "auftrag": lf.auftrag.nummer,
            "kunde": lf.auftrag.partner.name, "lagerort": lf.lagerort.code, "lieferdatum": lf.lieferdatum.isoformat(),
            "tour": tour.tour.nummer if tour else "", "empfaenger": {"name": lf.name, "strasse": lf.strasse,
                                                                      "plz": lf.plz, "ort": lf.ort, "land": lf.land},
            "positionen": [{"position_id": lp.pk, "artikelnummer": lp.auftrag_position.artikel.artikelnummer,
                            "bezeichnung": lp.auftrag_position.bezeichnung, "menge": f"{lp.menge.normalize():f}",
                            "einheit": lp.auftrag_position.einheit,
                            "charge_vorgabe": lp.charge.chargennummer if lp.charge_id else "",
                            "chargenpflicht": lp.auftrag_position.artikel.chargenpflicht}
                           for lp in lf.positionen.select_related("auftrag_position__artikel", "charge")]})
    return daten


def lvs_export(lieferungen, format="JSON", benutzer=None):
    """Kommissionieraufträge exportieren; Lieferscheine im Status ERFASST gehen auf KOMMISSIONIERUNG."""
    lieferungen = [lf for lf in lieferungen if lf.status in (Lieferung.Status.ERFASST,
                                                             Lieferung.Status.KOMMISSIONIERUNG)]
    daten = kommissionierauftraege(lieferungen)
    if format.upper() == "CSV":
        puffer = io.StringIO()
        w = csv.writer(puffer, delimiter=";", lineterminator="\n")
        w.writerow(CSV_EXPORT)
        for a in daten:
            for p in a["positionen"]:
                w.writerow([a["referenz"], a["lieferschein_id"], p["position_id"], p["artikelnummer"],
                            p["bezeichnung"], p["menge"], p["einheit"], p["charge_vorgabe"],
                            "1" if p["chargenpflicht"] else "0", a["lagerort"], a["empfaenger"]["name"],
                            a["empfaenger"]["plz"], a["empfaenger"]["ort"], a["lieferdatum"], a["tour"]])
        text = puffer.getvalue()
    else:
        text = json.dumps({"kommissionierauftraege": daten}, ensure_ascii=False, indent=2)
    Lieferung.objects.filter(pk__in=[lf.pk for lf in lieferungen], status=Lieferung.Status.ERFASST).update(
        status=Lieferung.Status.KOMMISSIONIERUNG)
    LvsProtokoll.objects.create(richtung=LvsProtokoll.Richtung.EXPORT, format=format.upper(), anzahl=len(daten),
                                inhalt=text[:200000], benutzer=_user(benutzer))
    return text


def _csv_zu_rueckmeldungen(text):
    auftraege = {}
    leser = csv.DictReader(io.StringIO(text.lstrip("\ufeff")), delimiter=";")
    fehlend = {"referenz", "position_id", "menge"} - set(leser.fieldnames or [])
    if fehlend:
        raise VersandFehler(f"CSV-Spalten fehlen: {', '.join(sorted(fehlend))}")
    for z in leser:
        a = auftraege.setdefault(z["referenz"].strip(), {"referenz": z["referenz"].strip(), "positionen": []})
        for feld in ("packstuecke", "gewicht_kg", "paletten"):
            if (z.get(feld) or "").strip():
                a[feld] = z[feld].strip()
        a["positionen"].append({"position_id": z["position_id"], "menge": z["menge"],
                                "charge": (z.get("charge") or "").strip(),
                                "packstueck": (z.get("packstueck") or "").strip()})
    return list(auftraege.values())


def _dez(wert, feld):
    try:
        return Decimal(str(wert).replace(",", "."))
    except (InvalidOperation, TypeError) as exc:
        raise VersandFehler(f"{feld}: ungültige Zahl „{wert}“.") from exc


def _lieferung_zu_referenz(ref):
    ref = str(ref).strip()
    pk = ref[4:] if ref.upper().startswith("KOM-") else ref
    if not str(pk).isdigit():
        raise VersandFehler(f"Unbekannte Referenz „{ref}“.")
    lf = Lieferung.objects.filter(pk=int(pk)).first()
    if not lf:
        raise VersandFehler(f"Kommissionierauftrag „{ref}“ nicht gefunden.")
    return lf


def rueckmeldung_verarbeiten(eintrag, benutzer=None, buchen=False):
    """Eine Rückmeldung: dict(referenz, positionen=[dict(position_id, menge, charge?, packstueck?)],
    packstuecke?, gewicht_kg?, paletten?). Nicht gemeldete Positionen gelten als 0 (Fehlmenge)."""
    with transaction.atomic():
        lf = _lieferung_zu_referenz(eintrag.get("referenz", ""))
        lf = Lieferung.objects.select_for_update().get(pk=lf.pk)
        if lf.status not in (Lieferung.Status.ERFASST, Lieferung.Status.KOMMISSIONIERUNG,
                             Lieferung.Status.KOMMISSIONIERT):
            raise VersandFehler(f"{lf} ist bereits gebucht – Rückmeldung nicht mehr möglich.")
        positionen = {lp.pk: lp for lp in lf.positionen.select_related("auftrag_position__artikel")}
        gemeldet = {}
        for p in eintrag.get("positionen") or []:
            pid = int(_dez(p.get("position_id"), "position_id"))
            lp = positionen.get(pid)
            if lp is None:
                raise VersandFehler(f"Position {pid} gehört nicht zu {lf.kommissionier_referenz}.")
            menge = _dez(p.get("menge", 0), "menge")
            if menge < 0:
                raise VersandFehler(f"Position {pid}: negative Menge.")
            artikel = lp.auftrag_position.artikel
            charge = None
            if p.get("charge"):
                charge = Charge.objects.filter(artikel=artikel, chargennummer=p["charge"]).first()
                if not charge:
                    raise VersandFehler(f"Position {pid}: Charge {p['charge']} zu {artikel.artikelnummer} unbekannt.")
            elif artikel.chargenpflicht and menge > 0 and not lp.charge_id:
                raise VersandFehler(f"Position {pid}: {artikel.artikelnummer} ist chargenpflichtig – Charge fehlt.")
            gemeldet.setdefault(pid, []).append((menge, charge, (p.get("packstueck") or "")[:50]))
        for pid, lp in positionen.items():
            zeilen = gemeldet.get(pid, [(Decimal(0), None, "")])
            summe = sum((m for m, _, _ in zeilen), Decimal(0))
            if summe > lp.menge:
                raise VersandFehler(f"Position {pid}: kommissioniert {summe:f} > Liefermenge {lp.menge:f}.")
            # mehrere Chargen je Position → Position aufteilen
            for i, (menge, charge, packstueck) in enumerate(zeilen):
                ziel = lp if i == 0 else LieferPosition(lieferung=lf, auftrag_position=lp.auftrag_position)
                ziel.menge = menge if len(zeilen) > 1 else lp.menge
                ziel.kommissioniert = menge
                ziel.charge = charge or (lp.charge if i == 0 else None)
                ziel.packstueck = packstueck
                ziel.save()
        for feld in ("packstuecke", "gewicht_kg", "paletten"):
            if eintrag.get(feld) not in (None, ""):
                wert = _dez(eintrag[feld], feld)
                setattr(lf, feld, int(wert) if feld == "packstuecke" else wert)
        lf.status = Lieferung.Status.KOMMISSIONIERT
        lf.save()
    veroeffentlichen("versand.lvs_rueckmeldung", lieferung=lf, benutzer=benutzer)
    if buchen:
        try:
            lf = VS.warenausgang_buchen(lf, benutzer)
        except VertriebFehler as exc:
            raise VersandFehler(f"{lf}: Rückmeldung gespeichert, Warenausgang nicht gebucht – {exc}") from exc
    return lf


def lvs_import(daten, format="JSON", benutzer=None, buchen=False):
    """Importiert Rückmeldungen (JSON-Text/-Objekt oder CSV-Text). Jede Rückmeldung in eigener Transaktion.
    Rückgabe: (verarbeitete Lieferungen, Fehlermeldungen)."""
    try:
        if format.upper() == "CSV":
            eintraege = _csv_zu_rueckmeldungen(daten if isinstance(daten, str) else daten.decode("utf-8"))
        else:
            obj = json.loads(daten) if isinstance(daten, (str, bytes)) else daten
            eintraege = obj.get("rueckmeldungen", []) if isinstance(obj, dict) else obj
    except (ValueError, VersandFehler) as exc:
        eintraege, fehler = [], [f"Datei nicht lesbar: {exc}"]
    else:
        fehler = []
    ok = []
    for e in eintraege:
        try:
            ok.append(rueckmeldung_verarbeiten(e, benutzer, buchen))
        except VersandFehler as exc:
            fehler.append(str(exc))
    LvsProtokoll.objects.create(
        richtung=LvsProtokoll.Richtung.IMPORT, format=format.upper(), anzahl=len(ok), erfolgreich=not fehler,
        inhalt=(daten if isinstance(daten, str) else json.dumps(daten, ensure_ascii=False, default=str))[:200000],
        meldungen="\n".join(fehler), benutzer=_user(benutzer))
    return ok, fehler


def kennzahlen():
    heute = timezone.localdate()
    morgen = heute + timedelta(days=1)
    touren = {}
    for tag, label in ((heute, "heute"), (morgen, "morgen")):
        liste = list(Tour.objects.filter(datum=tag).select_related("region", "fahrzeug"))
        touren[label] = [(t, t.auslastung()) for t in liste]
    return {"touren_heute": touren["heute"], "touren_morgen": touren["morgen"],
            "unverplant": offene_lieferungen().count(),
            "kommissionierung": Lieferung.objects.filter(status=Lieferung.Status.KOMMISSIONIERUNG).count(),
            "versandbereit": Lieferung.objects.filter(status=Lieferung.Status.GEBUCHT).count(),
            "unterwegs": Lieferung.objects.filter(status=Lieferung.Status.VERLADEN).count()}

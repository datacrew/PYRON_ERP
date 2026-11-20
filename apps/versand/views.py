"""Web-Oberfläche Versand: Dashboard, Tourenplanung, Tourenliste/Ladeliste (PDF), LVS-Schnittstelle.

Rechte (Modul versand): lesen = Ansicht; anlegen = Touren bilden/bearbeiten, LVS-Export/-Import;
freigeben = Tour freigeben, verladen, Zustellung bestätigen; aendern = Regionen/Fahrzeuge/Koordinaten.
"""
from datetime import date, timedelta

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.models import Mandant
from apps.core.permissions import recht_erforderlich
from apps.vertrieb.models import Lieferung, VertriebFehler

from . import services as S
from .models import LvsProtokoll, Region, Tour, TourStopp, VersandFehler

M = "versand"
FEHLER = (VersandFehler, VertriebFehler)


def _datum(wert, standard=None):
    try:
        return date.fromisoformat(wert) if wert else standard
    except ValueError:
        return standard


@recht_erforderlich(M)
def index(request):
    k = S.kennzahlen()
    kacheln = [("Touren", "versand:touren", "bi-truck", "Tourenplanung, Stopps, Auslastung"),
               ("Touren bilden", "versand:planen", "bi-diagram-3", "Offene Lieferscheine nach Region verplanen"),
               ("LVS-Schnittstelle", "versand:lvs", "bi-upc-scan", "Kommissionieraufträge, Rückmeldungen"),
               ("Regionen", "versand:regionen_liste", "bi-map", "PLZ-Gebiete und Liefertage"),
               ("Fahrzeuge", "versand:fahrzeuge_liste", "bi-truck-front", "Kapazität kg / Paletten"),
               ("PLZ-Koordinaten", "versand:plzkoordinaten_liste", "bi-geo-alt", "Optional für Entfernungssortierung")]
    return render(request, "versand/index.html", {"k": k, "kacheln": kacheln,
                                                  "offen": S.offene_lieferungen().order_by("lieferdatum", "plz")[:15]})


@recht_erforderlich(M)
def touren(request):
    qs = Tour.objects.select_related("region", "fahrzeug").prefetch_related("stopps")
    von = _datum(request.GET.get("von"), timezone.localdate() - timedelta(days=7))
    qs = qs.filter(datum__gte=von)
    if request.GET.get("status"):
        qs = qs.filter(status=request.GET["status"])
    zeilen = [(t, t.auslastung()) for t in qs.order_by("datum", "region__code")[:200]]
    return render(request, "versand/touren.html", {"zeilen": zeilen, "von": von, "stati": Tour.Status.choices})


@recht_erforderlich(M)
def tour_detail(request, pk):
    t = get_object_or_404(Tour.objects.select_related("region", "fahrzeug"), pk=pk)
    stopps = t.stopps.select_related("lieferung__auftrag__partner", "lieferung__beleg").order_by("reihenfolge")
    kandidaten = S.offene_lieferungen().order_by("plz") if t.status in ("GEPLANT", "FREIGEGEBEN") else []
    return render(request, "versand/tour.html", {"t": t, "stopps": stopps, "a": t.auslastung(),
                                                 "kandidaten": kandidaten})


@require_POST
@recht_erforderlich(M, "anlegen")
def tour_aktion(request, pk, aktion):
    from apps.core.permissions import hat_recht

    t = get_object_or_404(Tour, pk=pk)
    if aktion in ("freigeben", "verladen", "zugestellt") and not hat_recht(request.user, M, "freigeben"):
        messages.error(request, "Kein Recht „freigeben“ im Modul Versand.")
        return redirect("versand:tour", t.pk)
    try:
        if aktion == "freigeben":
            S.tour_freigeben(t, request.user)
            messages.success(request, f"Tour {t.nummer} freigegeben.")
        elif aktion == "verladen":
            S.tour_verladen(t, request.user)
            messages.success(request, f"Tour {t.nummer} verladen – unterwegs.")
        elif aktion == "zugestellt":
            S.zugestellt(get_object_or_404(Lieferung, pk=request.POST.get("lieferung")), request.user)
            messages.success(request, "Zustellung bestätigt.")
        elif aktion == "sortieren":
            S.stopps_sortieren(t)
            messages.success(request, "Stopps neu sortiert.")
        elif aktion == "entfernen":
            S.stopp_entfernen(get_object_or_404(TourStopp, pk=request.POST.get("stopp"), tour=t))
            messages.success(request, "Stopp entfernt.")
        elif aktion == "hinzufuegen":
            S.stopp_hinzufuegen(t, get_object_or_404(Lieferung, pk=request.POST.get("lieferung")),
                                erzwingen=bool(request.POST.get("erzwingen")))
            messages.success(request, "Lieferschein hinzugefügt.")
        else:
            messages.error(request, "Unbekannte Aktion.")
    except FEHLER as exc:
        messages.error(request, str(exc))
    return redirect("versand:tour", t.pk)


def tour_pdf_bytes(tour):
    """Touren- und Ladeliste als PDF (WeasyPrint)."""
    from weasyprint import HTML

    stopps = tour.stopps.select_related("lieferung__auftrag__partner", "lieferung__beleg").order_by("reihenfolge")
    ladeliste = {}
    for s in reversed(list(stopps)):  # Ladereihenfolge: letzter Stopp zuerst
        for lp in s.lieferung.positionen.select_related("auftrag_position__artikel", "charge"):
            ladeliste.setdefault((lp.auftrag_position.artikel.artikelnummer, lp.auftrag_position.bezeichnung,
                                  lp.auftrag_position.einheit), []).append((s, lp))
    html = render_to_string("versand/pdf/tourenliste.html", {
        "t": tour, "stopps": stopps, "a": tour.auslastung(), "mandant": Mandant.aktuell(),
        "ladeliste": [(k, z, sum(lp.menge for _, lp in z)) for k, z in ladeliste.items()],
        "erstellt": timezone.localtime()})
    return HTML(string=html).write_pdf()


@recht_erforderlich(M)
def tour_pdf(request, pk):
    t = get_object_or_404(Tour, pk=pk)
    r = HttpResponse(tour_pdf_bytes(t), content_type="application/pdf")
    r["Content-Disposition"] = f'inline; filename="tourenliste_{t.nummer}.pdf"'
    return r


@recht_erforderlich(M, "anlegen")
def planen(request):
    datum = _datum(request.POST.get("datum") or request.GET.get("datum"), timezone.localdate())
    ergebnis = None
    regionen = Region.objects.filter(aktiv=True)
    if request.method == "POST":
        auswahl = request.POST.getlist("region")
        try:
            ergebnis = S.touren_bilden(datum, request.user,
                                       regionen=regionen.filter(pk__in=auswahl) if auswahl else None)
            messages.success(request, f"{len(ergebnis['touren'])} Tour(en) gebildet/ergänzt, "
                                      f"{len(ergebnis['nicht_zugeordnet'])} Lieferschein(e) nicht zugeordnet.")
        except FEHLER as exc:
            messages.error(request, str(exc))
    vorschau = []
    for lf in S.offene_lieferungen(datum).order_by("plz"):
        r = S.region_fuer(lf.plz, lf.land)
        vorschau.append((lf, r, r.liefert_am(datum) if r else False))
    return render(request, "versand/planen.html", {"datum": datum, "regionen": regionen, "ergebnis": ergebnis,
                                                   "vorschau": vorschau})


@recht_erforderlich(M)
def lvs(request):
    from apps.core.permissions import hat_recht

    if request.method == "POST":
        if not hat_recht(request.user, M, "anlegen"):
            messages.error(request, "Kein Recht „anlegen“ im Modul Versand.")
            return redirect("versand:lvs")
        if request.POST.get("aktion") == "export":
            fmt = request.POST.get("format", "JSON").upper()
            ids = request.POST.getlist("lieferung")
            qs = Lieferung.objects.filter(pk__in=ids) if ids else \
                Lieferung.objects.filter(status__in=["ERFASST", "KOMMISSIONIERUNG"])
            text = S.lvs_export(list(qs.select_related("auftrag__partner", "lagerort")), fmt, request.user)
            r = HttpResponse(text.encode("utf-8"), content_type="text/csv" if fmt == "CSV" else "application/json")
            r["Content-Disposition"] = (f'attachment; filename="kommissionierauftraege_'
                                        f'{timezone.localtime():%Y%m%d_%H%M}.{fmt.lower()}"')
            return r
        datei = request.FILES.get("datei")
        if not datei:
            messages.error(request, "Bitte eine Rückmeldedatei auswählen.")
        else:
            fmt = "CSV" if datei.name.lower().endswith(".csv") else "JSON"
            ok, fehler = S.lvs_import(datei.read().decode("utf-8-sig", errors="replace"), fmt, request.user,
                                      buchen=bool(request.POST.get("buchen")))
            if ok:
                messages.success(request, f"{len(ok)} Rückmeldung(en) verarbeitet.")
            for f in fehler:
                messages.error(request, f)
        return redirect("versand:lvs")
    return render(request, "versand/lvs.html", {
        "offen": Lieferung.objects.filter(status__in=["ERFASST", "KOMMISSIONIERUNG", "KOMMISSIONIERT"]).select_related(
            "auftrag__partner", "beleg").order_by("lieferdatum"),
        "protokoll": LvsProtokoll.objects.select_related("benutzer").order_by("-zeitpunkt")[:30],
        "csv_import": ";".join(S.CSV_IMPORT)})


@recht_erforderlich(M)
def lvs_protokoll(request, pk):
    p = get_object_or_404(LvsProtokoll, pk=pk)
    return render(request, "versand/lvs_protokoll.html", {"p": p})

"""Reporting: Berichtsliste, Management-Cockpit, Berichtsanzeige mit Export und Berichtsvarianten."""
import json

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.permissions import recht_erforderlich

from . import export, kennzahlen, registry
from .filter import als_json, bereinigen, formular, klartext
from .models import Berichtsvariante

MIME = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
    "pdf": "application/pdf",
}


def _bericht_oder_fehler(user, code):
    b = registry.holen(code)
    if b is None:
        raise Http404("Unbekannter Bericht.")
    if not registry.darf(user, b):
        raise PermissionDenied("Kein Leserecht auf das Modul dieses Berichts.")
    return b


@recht_erforderlich("reporting")
def index(request):
    gruppen = {}
    for b in registry.fuer_benutzer(request.user):
        gruppen.setdefault(b.kategorie, []).append(b)
    erlaubt = {b.code: b for b in registry.fuer_benutzer(request.user)}
    varianten = [(v, erlaubt[v.bericht]) for v in Berichtsvariante.objects.filter(benutzer=request.user)
                 if v.bericht in erlaubt]
    return render(request, "reporting/index.html", {"gruppen": sorted(gruppen.items()), "varianten": varianten})


@recht_erforderlich("reporting")
def cockpit(request):
    d = kennzahlen.diagramme(request.user)
    return render(request, "reporting/cockpit.html", {
        "kacheln": kennzahlen.kacheln(request.user), "diagramme": d, "diagramme_json": json.dumps(d),
        "favoriten": favoriten(request.user)})


def favoriten(user):
    """Favorisierte Berichtsvarianten des Benutzers (nur erlaubte Berichte) – auch für das Dashboard."""
    aus = []
    for v in Berichtsvariante.objects.filter(benutzer=user, favorit=True):
        b = registry.holen(v.bericht)
        if b and registry.darf(user, b):
            aus.append({"name": v.name, "bericht": b.name,
                        "url": reverse("reporting:bericht", args=[b.code]) + "?" + v.query()})
    return aus


@recht_erforderlich("reporting")
def bericht(request, code):
    b = _bericht_oder_fehler(request.user, code)
    if request.method == "POST":
        return _variante_speichern(request, b)
    daten = request.GET.copy()
    daten.pop("format", None)
    werte, fehler = bereinigen(b, daten)
    form = formular(b, daten or None)
    ergebnis = b.ausfuehren(werte) if werte is not None else None
    fmt = request.GET.get("format")
    if fmt and ergebnis is not None:
        if fmt not in MIME:
            raise Http404("Unbekanntes Exportformat.")
        inhalt = {"xlsx": export.als_xlsx, "csv": export.als_csv, "pdf": export.als_pdf}[fmt](b, ergebnis, werte)
        antwort = HttpResponse(inhalt, content_type=MIME[fmt])
        antwort["Content-Disposition"] = f'attachment; filename="{export.dateiname(b, fmt)}"'
        return antwort
    zeilen, summe = export.tabelle(b, ergebnis) if ergebnis else ([], None)
    diagramm = None
    if b.diagramm and ergebnis and ergebnis["zeilen"]:
        diagramm = json.dumps({
            "typ": b.diagramm.get("typ", "bar"),
            "labels": [str(z.get(b.diagramm["label"]) or "") for z in ergebnis["zeilen"]][:30],
            "werte": [float(z.get(b.diagramm["wert"]) or 0) for z in ergebnis["zeilen"]][:30],
            "titel": next((s.label for s in b.spalten if s.key == b.diagramm["wert"]), "")})
    query = daten.urlencode() if daten else ""
    return render(request, "reporting/bericht.html", {
        "b": b, "form": form, "fehler": fehler, "zeilen": zeilen, "summe": summe,
        "hinweise": ergebnis["hinweise"] if ergebnis else [], "filter_text": klartext(b, werte) if werte else [],
        "diagramm": diagramm, "query": query,
        "varianten": Berichtsvariante.objects.filter(benutzer=request.user, bericht=b.code)})


def _variante_speichern(request, b):
    name = (request.POST.get("variante_name") or "").strip()[:100]
    werte, fehler = bereinigen(b, request.POST)
    if not name or fehler:
        messages.error(request, "Bitte einen Namen für die Variante angeben und gültige Filter wählen.")
        return redirect("reporting:bericht", b.code)
    v, neu = Berichtsvariante.objects.update_or_create(
        benutzer=request.user, bericht=b.code, name=name,
        defaults={"filter": als_json(werte), "favorit": bool(request.POST.get("favorit"))})
    messages.success(request, f"Variante „{name}“ {'gespeichert' if neu else 'aktualisiert'}.")
    return redirect(reverse("reporting:bericht", args=[b.code]) + "?" + v.query())


@recht_erforderlich("reporting")
def variante_loeschen(request, pk):
    v = get_object_or_404(Berichtsvariante, pk=pk, benutzer=request.user)
    if request.method == "POST":
        v.delete()
        messages.success(request, f"Variante „{v.name}“ gelöscht.")
    return redirect("reporting:index")

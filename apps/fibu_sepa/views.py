"""Web-Oberfläche SEPA-Zahlungsverkehr – bindet ausschließlich die Services aus services.py an.

Rechte (Modul „fibu_sepa“): lesen = ansehen und XML herunterladen, anlegen = Vorschlag erstellen und Posten
entfernen, aendern = SEPA-Mandate pflegen, freigeben = Lauf freigeben, exportieren und stornieren,
loeschen = Entwurf verwerfen.
"""
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.permissions import recht_erforderlich
from apps.fibu.services import FibuFehler

from . import services as S
from .forms import MandatForm, StornoForm, VorschlagForm
from .models import SepaGesperrt, SepaMandat, Zahlungslauf, ZahlungslaufPosten

M = "fibu_sepa"
FEHLER = (FibuFehler, SepaGesperrt)  # SepaWorkflowFehler ist eine Unterklasse von FibuFehler
MANDAT_FILTER = {"aktiv": "Aktiv", "gesperrt": "Gesperrt", "abgelaufen": "Abgelaufen", "alle": "Alle"}


# ------------------------------------------------------------------ SEPA-Mandate

def _mandat_filter(mandate, filt):
    if filt == "aktiv":
        return [m for m in mandate if m.status == SepaMandat.Status.AKTIV and not m.ist_verfallen()]
    if filt == "gesperrt":
        return [m for m in mandate if m.status in (SepaMandat.Status.GESPERRT, SepaMandat.Status.WIDERRUFEN)]
    if filt == "abgelaufen":  # verfallen (36 Monate) oder beendet
        return [m for m in mandate if m.status in (SepaMandat.Status.ABGELAUFEN, SepaMandat.Status.BEENDET)
                or (m.status == SepaMandat.Status.AKTIV and m.ist_verfallen())]
    return mandate


@recht_erforderlich(M)
def mandate(request):
    filt = request.GET.get("filter", "aktiv")
    if filt not in MANDAT_FILTER:
        filt = "aktiv"
    qs = SepaMandat.objects.select_related("partner").annotate(nutzungen=Count("posten"))
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(partner__name__icontains=q) | qs.filter(mandatsreferenz__icontains=q)
    page_obj = Paginator(_mandat_filter(list(qs), filt), 50).get_page(request.GET.get("page"))
    return render(request, "fibu_sepa/mandate.html", {
        "page_obj": page_obj, "filter": filt, "filter_auswahl": MANDAT_FILTER, "q": q, "aktiv": "mandate"})


@recht_erforderlich(M, "aendern")
def mandat_form(request, pk=None):
    mandat = get_object_or_404(SepaMandat, pk=pk) if pk else None
    form = MandatForm(request.POST or None, instance=mandat)
    if request.method == "POST" and form.is_valid():
        mandat = form.save()
        messages.success(request, f"SEPA-Mandat {mandat.mandatsreferenz} gespeichert.")
        return redirect("fibu_sepa:mandat", mandat.pk)
    return render(request, "fibu_sepa/mandat_form.html", {"form": form, "mandat": mandat, "aktiv": "mandate"})


@recht_erforderlich(M)
def mandat_detail(request, pk):
    mandat = get_object_or_404(SepaMandat.objects.select_related("partner"), pk=pk)
    historie = mandat.posten.select_related("lauf", "op").order_by("-lauf__ausfuehrungsdatum", "-pk")
    return render(request, "fibu_sepa/mandat_detail.html", {
        "mandat": mandat, "historie": historie, "naechste_sequenz": mandat.naechste_sequenz(), "aktiv": "mandate"})


@require_POST
@recht_erforderlich(M, "aendern")
def mandat_status(request, pk, aktion):
    mandat = get_object_or_404(SepaMandat, pk=pk)
    ziel = {"sperren": SepaMandat.Status.GESPERRT, "beenden": SepaMandat.Status.BEENDET,
            "entsperren": SepaMandat.Status.AKTIV}.get(aktion)
    erlaubt = {"sperren": [SepaMandat.Status.AKTIV], "entsperren": [SepaMandat.Status.GESPERRT],
               "beenden": [SepaMandat.Status.AKTIV, SepaMandat.Status.GESPERRT]}
    if ziel is None:
        raise Http404
    if mandat.status not in erlaubt[aktion]:
        messages.error(request, f"Mandat {mandat.mandatsreferenz} ist {mandat.get_status_display()} – "
                                f"Aktion nicht möglich.")
    else:
        mandat.status = ziel
        mandat.save(update_fields=["status"])
        messages.success(request, f"Mandat {mandat.mandatsreferenz}: {mandat.get_status_display()}.")
    return redirect("fibu_sepa:mandat", pk)


# ------------------------------------------------------------------ Zahlungsläufe

@recht_erforderlich(M)
def laeufe(request):
    qs = Zahlungslauf.objects.select_related("bankkonto")
    art, status = request.GET.get("art", ""), request.GET.get("status", "")
    if art in Zahlungslauf.Art.values:
        qs = qs.filter(art=art)
    if status in Zahlungslauf.Status.values:
        qs = qs.filter(status=status)
    return render(request, "fibu_sepa/laeufe.html", {
        "page_obj": Paginator(qs, 50).get_page(request.GET.get("page")), "art": art, "status": status,
        "arten": Zahlungslauf.Art.choices, "stati": Zahlungslauf.Status.choices, "aktiv": "laeufe"})


@recht_erforderlich(M, "anlegen")
def vorschlag(request, art):
    lastschrift = art == "lastschrift"
    if art not in ("ueberweisung", "lastschrift"):
        raise Http404
    form = VorschlagForm(request.POST or None, lastschrift=lastschrift)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            if lastschrift:
                lauf = S.lastschriftvorschlag(d["bankkonto"], d["datum"], d["bis"], benutzer=request.user)
            else:
                lauf = S.ueberweisungsvorschlag(d["bankkonto"], d["datum"], d["bis"], d["skonto_nutzen"],
                                                benutzer=request.user)
        except FEHLER as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Vorschlag {lauf.nummer} mit {lauf.anzahl} Posten erstellt "
                                      f"(Ausführung {lauf.ausfuehrungsdatum:%d.%m.%Y}).")
            if lauf.uebersprungen:
                # nicht gespeichert – einmalig anzeigen
                request.session[f"sepa_uebersprungen_{lauf.pk}"] = lauf.uebersprungen
                messages.warning(request, f"{len(lauf.uebersprungen)} Posten übersprungen – siehe Liste im Lauf.")
            return redirect("fibu_sepa:lauf", lauf.pk)
    titel = "Lastschriftvorschlag erstellen" if lastschrift else "Überweisungsvorschlag erstellen"
    return render(request, "fibu_sepa/vorschlag.html", {
        "form": form, "titel": titel, "lastschrift": lastschrift, "aktiv": "laeufe"})


@recht_erforderlich(M)
def lauf_detail(request, pk):
    lauf = get_object_or_404(Zahlungslauf.objects.select_related(
        "bankkonto", "buchung", "storno_buchung", "erstellt_von", "freigegeben_von", "exportiert_von",
        "storniert_von"), pk=pk)
    return render(request, "fibu_sepa/lauf_detail.html", {
        "lauf": lauf, "posten": lauf.posten.select_related("op", "partner", "mandat").order_by("partner__name", "pk"),
        "uebersprungen": request.session.pop(f"sepa_uebersprungen_{lauf.pk}", []), "aktiv": "laeufe"})


@require_POST
@recht_erforderlich(M, "anlegen")
def posten_entfernen(request, pk, posten_pk):
    lauf = get_object_or_404(Zahlungslauf, pk=pk)
    posten = get_object_or_404(ZahlungslaufPosten, pk=posten_pk, lauf=lauf)
    if not lauf.bearbeitbar:
        messages.error(request, f"Lauf {lauf.nummer} ist nicht mehr im Entwurf – Posten können nicht entfernt werden.")
        return redirect("fibu_sepa:lauf", pk)
    try:
        posten.delete()
        S._summen(lauf)
        lauf.save(update_fields=["summe", "anzahl"])
    except FEHLER as exc:
        messages.error(request, str(exc))
    else:
        messages.info(request, f"Posten {posten.op.belegnr} ({posten.name}) aus dem Lauf entfernt.")
    return redirect("fibu_sepa:lauf", pk)


def _workflow(request, pk, funktion, erfolg, **kwargs):
    lauf = get_object_or_404(Zahlungslauf, pk=pk)
    try:
        funktion(lauf, request.user, **kwargs)
    except FEHLER as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, erfolg.format(nummer=lauf.nummer))
    return redirect("fibu_sepa:lauf", pk)


@require_POST
@recht_erforderlich(M, "freigeben")
def lauf_freigeben(request, pk):
    return _workflow(request, pk, S.freigeben, "Lauf {nummer} freigegeben.")


@require_POST
@recht_erforderlich(M, "freigeben")
def lauf_exportieren(request, pk):
    return _workflow(request, pk, S.exportieren,
                     "Lauf {nummer} exportiert, XML erzeugt und auf Geldtransit gebucht – Datei jetzt herunterladen.")


@recht_erforderlich(M, "freigeben")
def lauf_stornieren(request, pk):
    lauf = get_object_or_404(Zahlungslauf, pk=pk)
    form = StornoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        return _workflow(request, pk, S.stornieren, "Lauf {nummer} storniert (Gegenbuchung erzeugt).",
                         grund=form.cleaned_data["grund"])
    return render(request, "fibu_sepa/lauf_storno.html", {"lauf": lauf, "form": form, "aktiv": "laeufe"})


@require_POST
@recht_erforderlich(M, "loeschen")
def lauf_verwerfen(request, pk):
    lauf = get_object_or_404(Zahlungslauf, pk=pk)
    try:
        lauf.delete()  # Modell erlaubt das Löschen nur im Entwurf
    except FEHLER as exc:
        messages.error(request, str(exc))
        return redirect("fibu_sepa:lauf", pk)
    messages.info(request, f"Entwurf {lauf.nummer} verworfen.")
    return redirect("fibu_sepa:laeufe")


@recht_erforderlich(M)
def lauf_xml(request, pk):
    lauf = get_object_or_404(Zahlungslauf, pk=pk)
    if not lauf.xml_inhalt:
        raise Http404("Für diesen Lauf wurde noch keine XML-Datei erzeugt.")
    antwort = HttpResponse(lauf.xml_inhalt.encode("utf-8"), content_type="application/xml; charset=utf-8")
    antwort["Content-Disposition"] = f'attachment; filename="{lauf.xml_dateiname or lauf.nummer + ".xml"}"'
    return antwort

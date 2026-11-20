from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.permissions import hat_recht, recht_erforderlich
from apps.einkauf.models import Bestellung
from apps.einkauf.services import offene_positionen
from apps.lager.models import LagerFehler, Lagerort

from . import services
from .forms import KopfForm, PositionFormSet, QSForm
from .models import Wareneingang, WareneingangFehler, WEPosition
from .pdf import wareneingang_pdf

M = "wareneingang"


@recht_erforderlich(M)
def index(request):
    return render(request, "wareneingang/index.html", {
        "bestellungen": services.offene_bestellungen()[:50],
        "pruefungen": services.offene_pruefungen(),
        "letzte": Wareneingang.objects.select_related("lieferant", "bestellung")[:10],
        "darf_buchen": hat_recht(request.user, M, "anlegen"),
    })


@recht_erforderlich(M)
def liste(request):
    qs = Wareneingang.objects.select_related("lieferant", "bestellung", "lagerort")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(nummer__icontains=q) | Q(lieferschein_nr__icontains=q) | Q(lieferant__name__icontains=q)
                       | Q(bestellung__nummer__icontains=q))
    seite = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "wareneingang/liste.html", {"page_obj": seite, "q": q})


@recht_erforderlich(M, "anlegen")
def buchen(request, bestellung_pk):
    b = get_object_or_404(Bestellung.objects.select_related("lieferant", "lieferort"), pk=bestellung_pk)
    if not b.ist_offen:
        messages.error(request, f"{b} ist {b.get_status_display().lower()} – kein Wareneingang möglich.")
        return redirect("einkauf:detail", b.pk)
    positionen = list(offene_positionen().filter(bestellung=b).order_by("position"))
    lagerort = b.lieferort or Lagerort.objects.filter(ist_wareneingang=True, aktiv=True).first()
    from apps.lager.services import lagerdaten
    initial = [{"bestellposition": p.pk, "menge": p.offen_menge, "qs_pruefen": lagerdaten(p.artikel).qs_pflicht}
               for p in positionen]
    kopf = KopfForm(request.POST or None, initial={"lagerort": lagerort})
    formset = PositionFormSet(request.POST or None, initial=initial, prefix="we")
    if request.method == "POST" and kopf.is_valid() and formset.is_valid():
        d = kopf.cleaned_data
        try:
            we = services.buchen(b, [f.cleaned_data for f in formset if f.cleaned_data],
                                 lieferschein_nr=d["lieferschein_nr"], lagerort=d["lagerort"], datum=d["datum"],
                                 bemerkung=d["bemerkung"], benutzer=request.user)
        except (WareneingangFehler, LagerFehler) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"{we} gebucht.")
            if we.hinweise:
                messages.warning(request, "Differenzen protokolliert: " + we.hinweise.replace("\n", " | "))
            return redirect("wareneingang:detail", we.pk)
    zeilen = list(zip(positionen, formset.forms))
    for p, f in zeilen:
        f.qs_pflicht = lagerdaten(p.artikel).qs_pflicht
    return render(request, "wareneingang/buchen.html", {"b": b, "kopf": kopf, "formset": formset, "zeilen": zeilen})


@recht_erforderlich(M)
def detail(request, pk):
    we = get_object_or_404(Wareneingang.objects.select_related("lieferant", "bestellung", "lagerort", "gebucht_von"),
                           pk=pk)
    return render(request, "wareneingang/detail.html", {
        "we": we, "positionen": we.positionen.select_related("artikel", "charge", "lagerplatz", "bestellposition",
                                                             "lagerbewegung", "qs_von"),
        "qs_form": QSForm(), "darf_qs": hat_recht(request.user, M, "freigeben"),
    })


@require_POST
@recht_erforderlich(M, "freigeben")
def qs(request, pk, was):
    pos = get_object_or_404(WEPosition, pk=pk)
    form = QSForm(request.POST)
    form.is_valid()
    d = form.cleaned_data
    entscheid = {"freigeben": WEPosition.QS.FREIGEGEBEN, "sperren": WEPosition.QS.GESPERRT}.get(was)
    try:
        services.qs_entscheiden(pos, entscheid, bemerkung=d.get("bemerkung", ""),
                                ruecksenden=d.get("ruecksenden", False), benutzer=request.user)
        messages.success(request, f"{pos.artikel.artikelnummer}: "
                         + ("freigegeben – Bestand ist verfügbar." if was == "freigeben" else "gesperrt/reklamiert."))
    except (WareneingangFehler, LagerFehler) as exc:
        messages.error(request, str(exc))
    return redirect("wareneingang:detail", pos.wareneingang_id)


@recht_erforderlich(M)
def pdf(request, pk):
    we = get_object_or_404(Wareneingang, pk=pk)
    antwort = HttpResponse(wareneingang_pdf(we), content_type="application/pdf")
    antwort["Content-Disposition"] = f'inline; filename="{we.nummer}.pdf"'
    return antwort

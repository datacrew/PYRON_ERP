from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.permissions import hat_recht, recht_erforderlich

from . import services
from .forms import BelegForm, PositionFormSet, StornoForm
from .models import Beleg, BelegGesperrt
from .pdf import beleg_pdf

M = "belege"


@recht_erforderlich(M)
def liste(request):
    qs = Beleg.objects.select_related("partner")
    for feld in ("belegart", "status"):
        if request.GET.get(feld):
            qs = qs.filter(**{feld: request.GET[feld]})
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(nummer__icontains=q) | Q(partner__name__icontains=q) | Q(betreff__icontains=q))
    seite = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "belege/liste.html", {
        "seite": seite, "page_obj": seite,
        "belegarten": Beleg.Belegart.choices, "stati": Beleg.Status.choices, "q": q,
    })


def _speichern(request, beleg):
    form = BelegForm(request.POST or None, instance=beleg)
    formset = PositionFormSet(request.POST or None, instance=beleg, prefix="pos")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            beleg = form.save(commit=False)
            if not beleg.pk:
                beleg.erstellt_von = request.user
            if not beleg.zahlungsbedingung_id and beleg.partner.zahlungsbedingung_id:
                beleg.zahlungsbedingung = beleg.partner.zahlungsbedingung
            beleg.save()
            formset.instance = beleg
            formset.save()
            for i, pos in enumerate(beleg.positionen.order_by("position", "pk"), start=1):
                if pos.position != i * 10:
                    pos.position = i * 10
                    pos.save()
            beleg.berechne_summen()
            beleg.save()
        messages.success(request, f"{beleg} wurde gespeichert.")
        return redirect("belege:detail", beleg.pk)
    return render(request, "belege/formular.html", {"form": form, "formset": formset, "beleg": beleg})


@recht_erforderlich(M, "anlegen")
def neu(request):
    beleg = Beleg(belegart=request.GET.get("belegart") or Beleg.Belegart.ANGEBOT)
    return _speichern(request, beleg)


@recht_erforderlich(M, "aendern")
def bearbeiten(request, pk):
    beleg = get_object_or_404(Beleg, pk=pk)
    if not beleg.ist_bearbeitbar:
        messages.error(request, f"{beleg} ist {beleg.get_status_display().lower()} und nicht mehr bearbeitbar.")
        return redirect("belege:detail", pk)
    return _speichern(request, beleg)


@recht_erforderlich(M)
def detail(request, pk):
    beleg = get_object_or_404(Beleg.objects.select_related("partner", "zahlungsbedingung"), pk=pk)
    u = request.user
    return render(request, "belege/detail.html", {
        "beleg": beleg, "positionen": beleg.positionen.select_related("artikel"),
        "steuer": beleg.steuer_aufstellung().items(), "storno_form": StornoForm(),
        "darf_aendern": beleg.ist_bearbeitbar and hat_recht(u, M, "aendern"),
        "darf_loeschen": beleg.ist_bearbeitbar and hat_recht(u, M, "loeschen"),
        "darf_freigeben": hat_recht(u, M, "freigeben"),
    })


@require_POST
@recht_erforderlich(M, "freigeben")
def aktion(request, pk, was):
    beleg = get_object_or_404(Beleg, pk=pk)
    try:
        if was == "freigeben":
            beleg = services.freigeben(beleg, request.user)
            messages.success(request, f"Beleg freigegeben – Nummer {beleg.nummer}.")
        elif was == "festschreiben":
            beleg = services.festschreiben(beleg, request.user)
            messages.success(request, f"{beleg} wurde festgeschrieben und archiviert.")
        elif was == "stornieren":
            form = StornoForm(request.POST)
            services.stornieren(beleg, request.user, form.data.get("grund", ""))
            messages.success(request, f"{beleg} wurde storniert.")
        else:
            raise PermissionDenied
    except (services.WorkflowFehler, BelegGesperrt) as exc:
        messages.error(request, str(exc))
    return redirect("belege:detail", pk)


@require_POST
@recht_erforderlich(M, "loeschen")
def loeschen(request, pk):
    beleg = get_object_or_404(Beleg, pk=pk)
    try:
        beleg.delete()
        messages.success(request, "Entwurf gelöscht.")
        return redirect("belege:liste")
    except BelegGesperrt as exc:
        messages.error(request, str(exc))
        return redirect("belege:detail", pk)


@recht_erforderlich(M)
def pdf(request, pk):
    """Festgeschriebene Belege: archiviertes Original. Sonst: Vorschau mit Entwurfsvermerk."""
    beleg = get_object_or_404(Beleg, pk=pk)
    if beleg.pdf:
        return FileResponse(beleg.pdf.open("rb"), content_type="application/pdf",
                            filename=f"{beleg.nummer}.pdf", as_attachment=False)
    antwort = HttpResponse(beleg_pdf(beleg), content_type="application/pdf")
    antwort["Content-Disposition"] = f'inline; filename="{beleg.nummer or f"entwurf-{beleg.pk}"}.pdf"'
    return antwort


@recht_erforderlich("verwaltung")
def kette(request):
    anzahl, fehler = services.kette_pruefen()
    return render(request, "belege/kette.html", {"anzahl": anzahl, "fehler": fehler})

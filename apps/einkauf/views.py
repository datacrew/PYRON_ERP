from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core.permissions import hat_recht, recht_erforderlich

from . import services
from .forms import BanfForm, BanfUebernahmeForm, BestellungForm, PositionFormSet, StornoForm
from .models import Bestellanforderung, Bestellung, BestellPosition, EinkaufFehler
from .pdf import bestellung_pdf

M = "einkauf"


@recht_erforderlich(M)
def index(request):
    offen = list(services.offene_positionen())
    n_offen = Bestellung.objects.filter(typ=Bestellung.Typ.BESTELLUNG, status__in=Bestellung.OFFENE_STATI).count()
    n_entwurf = Bestellung.objects.filter(status=Bestellung.Status.ENTWURF).count()
    n_banf = Bestellanforderung.objects.filter(status=Bestellanforderung.Status.OFFEN).count()
    kacheln = [
        ("Bestellungen & Anfragen", "einkauf:liste", "bi-cart-check", f"{n_offen} offen · {n_entwurf} Entwürfe"),
        ("Liefertermine", "einkauf:termine", "bi-calendar-event",
         f"{len(offen)} offene Positionen · {sum(1 for p in offen if p.ueberfaellig)} überfällig"),
        ("Bestellvorschlag", "einkauf:vorschlag", "bi-lightbulb",
         f"{len(services.bestellvorschlaege())} Artikel unter Meldebestand"),
        ("Bestellanforderungen", "einkauf:banf_liste", "bi-card-checklist", f"{n_banf} offen"),
    ]
    return render(request, "einkauf/index.html", {
        "kacheln": kacheln, "naechste": sorted(offen, key=lambda p: (p.termin is None, p.termin))[:10],
    })


@recht_erforderlich(M)
def liste(request):
    qs = Bestellung.objects.select_related("lieferant")
    for feld in ("typ", "status"):
        if request.GET.get(feld):
            qs = qs.filter(**{feld: request.GET[feld]})
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(nummer__icontains=q) | Q(lieferant__name__icontains=q) | Q(ihre_referenz__icontains=q))
    seite = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "einkauf/liste.html", {"page_obj": seite, "typen": Bestellung.Typ.choices,
                                                  "stati": Bestellung.Status.choices, "q": q})


def _speichern(request, bestellung):
    form = BestellungForm(request.POST or None, instance=bestellung)
    formset = PositionFormSet(request.POST or None, instance=bestellung, prefix="pos")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            b = form.save(commit=False)
            if not b.pk:
                b.erstellt_von = request.user
            b.save()
            formset.instance = b
            formset.save()
            services.neu_nummerieren(b)
            b.berechne_summen()
            b.save()
        messages.success(request, f"{b} wurde gespeichert.")
        return redirect("einkauf:detail", b.pk)
    return render(request, "einkauf/formular.html", {"form": form, "formset": formset, "bestellung": bestellung})


@recht_erforderlich(M, "anlegen")
def neu(request):
    typ = request.GET.get("typ") if request.GET.get("typ") in Bestellung.Typ.values else Bestellung.Typ.BESTELLUNG
    return _speichern(request, Bestellung(typ=typ))


@recht_erforderlich(M, "aendern")
def bearbeiten(request, pk):
    b = get_object_or_404(Bestellung, pk=pk)
    if not b.ist_bearbeitbar:
        messages.error(request, f"{b} ist {b.get_status_display().lower()} und nicht mehr bearbeitbar.")
        return redirect("einkauf:detail", pk)
    return _speichern(request, b)


@recht_erforderlich(M)
def detail(request, pk):
    b = get_object_or_404(Bestellung.objects.select_related("lieferant", "zahlungsbedingung", "lieferort",
                                                            "anfrage"), pk=pk)
    u = request.user
    wareneingaenge = []
    if hat_recht(u, "wareneingang"):
        wareneingaenge = b.wareneingaenge.all()
    return render(request, "einkauf/detail.html", {
        "b": b, "positionen": b.positionen.select_related("artikel"), "steuer": b.steuer_aufstellung().items(),
        "storno_form": StornoForm(), "wareneingaenge": wareneingaenge,
        "darf_aendern": b.ist_bearbeitbar and hat_recht(u, M, "aendern"),
        "darf_loeschen": b.ist_bearbeitbar and hat_recht(u, M, "loeschen"),
        "darf_freigeben": hat_recht(u, M, "freigeben"),
        "darf_anlegen": hat_recht(u, M, "anlegen"),
        "darf_we": b.ist_offen and hat_recht(u, "wareneingang", "anlegen"),
    })


@require_POST
@recht_erforderlich(M, "freigeben")
def aktion(request, pk, was):
    b = get_object_or_404(Bestellung, pk=pk)
    try:
        if was == "freigeben":
            b = services.freigeben(b, request.user)
            messages.success(request, f"{b.get_typ_display()} freigegeben – Nummer {b.nummer}.")
        elif was == "stornieren":
            services.stornieren(b, request.user, request.POST.get("grund", ""))
            messages.success(request, f"{b} wurde storniert.")
        elif was == "bestellung":
            neu = services.anfrage_in_bestellung(b, request.user)
            messages.success(request, "Bestellentwurf aus der Anfrage erzeugt – bitte Preise prüfen.")
            return redirect("einkauf:detail", neu.pk)
        elif was == "abschliessen":
            pos = get_object_or_404(BestellPosition, pk=request.POST.get("position"), bestellung=b)
            services.restmenge_abschliessen(pos)
            messages.success(request, f"Restmenge von Pos. {pos.position} abgeschlossen.")
        else:
            messages.error(request, "Unbekannte Aktion.")
    except EinkaufFehler as exc:
        messages.error(request, str(exc))
    return redirect("einkauf:detail", pk)


@require_POST
@recht_erforderlich(M, "loeschen")
def loeschen(request, pk):
    b = get_object_or_404(Bestellung, pk=pk)
    if not b.ist_bearbeitbar:
        messages.error(request, "Nur Entwürfe können gelöscht werden.")
        return redirect("einkauf:detail", pk)
    b.delete()
    messages.success(request, "Entwurf gelöscht.")
    return redirect("einkauf:liste")


@recht_erforderlich(M)
def pdf(request, pk):
    b = get_object_or_404(Bestellung, pk=pk)
    antwort = HttpResponse(bestellung_pdf(b), content_type="application/pdf")
    antwort["Content-Disposition"] = f'inline; filename="{(b.nummer or f"entwurf-{b.pk}")}.pdf"'
    return antwort


@recht_erforderlich(M)
def termine(request):
    """Offene Bestellpositionen nach Liefertermin."""
    positionen = sorted(services.offene_positionen(), key=lambda p: (p.termin is None, p.termin, p.pk))
    return render(request, "einkauf/termine.html", {"positionen": positionen})


@recht_erforderlich(M)
def vorschlag(request):
    if request.method == "POST":
        if not hat_recht(request.user, M, "anlegen"):
            messages.error(request, "Dafür fehlt Ihnen das Recht „anlegen“.")
            return redirect("einkauf:vorschlag")
        ids = {int(i) for i in request.POST.getlist("artikel") if i.isdigit()}
        bestellungen, ohne = services.vorschlaege_bestellen(ids, request.user)
        for b in bestellungen:
            messages.success(request, f"{b} für {b.lieferant} angelegt.")
        if ohne:
            messages.warning(request, "Ohne bevorzugten Lieferanten übersprungen: "
                             + ", ".join(a.artikelnummer for a in ohne))
        if not bestellungen and not ohne:
            messages.info(request, "Keine Artikel ausgewählt.")
        return redirect("einkauf:liste" if bestellungen else "einkauf:vorschlag")
    return render(request, "einkauf/vorschlag.html", {"vorschlaege": services.bestellvorschlaege()})


@recht_erforderlich(M)
def banf_liste(request):
    darf = hat_recht(request.user, M, "anlegen")
    form = BanfForm(request.POST or None) if darf else None
    if request.method == "POST" and darf:
        if "uebernehmen" in request.POST:
            ue = BanfUebernahmeForm(request.POST)
            banfs = list(Bestellanforderung.objects.filter(pk__in=request.POST.getlist("banf")))
            if ue.is_valid():
                try:
                    b = services.banf_uebernehmen(banfs, ue.cleaned_data["lieferant"], request.user)
                    messages.success(request, f"{b} aus {len(banfs)} Anforderung(en) angelegt.")
                    return redirect("einkauf:detail", b.pk)
                except EinkaufFehler as exc:
                    messages.error(request, str(exc))
            else:
                messages.error(request, "Bitte einen Lieferanten wählen.")
            form = BanfForm()
        elif form.is_valid():
            d = form.cleaned_data
            banf = services.banf_anlegen(d["artikel"], d["menge"], d["bedarfstermin"], d["begruendung"],
                                         request.user)
            messages.success(request, f"{banf} angelegt.")
            return redirect("einkauf:banf_liste")
    return render(request, "einkauf/banf.html", {
        "banfs": Bestellanforderung.objects.select_related("artikel", "artikel__einheit", "angefordert_von",
                                                           "bestellposition__bestellung")[:200],
        "form": form, "uebernahme_form": BanfUebernahmeForm() if darf else None,
    })

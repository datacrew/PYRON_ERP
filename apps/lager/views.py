from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.core.permissions import hat_recht, recht_erforderlich
from apps.stammdaten.models import Artikel

from . import services
from .forms import BuchungForm, InventurNeuForm, UmbuchungForm, ZaehlFormSet
from .models import ArtikelLager, Bestand, Bestandsart, Inventur, Lagerbewegung, LagerFehler

M = "lager"


def dashboard_hinweise(request):
    """Hinweise für die Startseite (über `erp_modul['dashboard']` eingebunden)."""
    hinweise = []
    for u in services.unterschreitungen():
        a = u["artikel"]
        grenze = u["lagerdaten"].mindestbestand if u["kritisch"] else u["lagerdaten"].meldebestand
        hinweise.append({
            "stufe": "danger" if u["kritisch"] else "warning",
            "text": f"{a.artikelnummer} {a.bezeichnung}: frei {u['frei'].normalize():f} {a.einheit.code} – "
                    f"{'Mindestbestand' if u['kritisch'] else 'Meldebestand'} {grenze.normalize():f}",
            "url": reverse("lager:artikel", args=[a.pk]),
        })
    return hinweise


@recht_erforderlich(M)
def uebersicht(request):
    q = request.GET.get("q", "").strip()
    nur_kritisch = request.GET.get("unterschritten") == "1"
    summen = {}
    for z in Bestand.objects.values("artikel", "bestandsart").annotate(s=Sum("menge")):
        summen.setdefault(z["artikel"], {})[z["bestandsart"]] = z["s"] or Decimal(0)
    lagerdaten = {ld.artikel_id: ld for ld in ArtikelLager.objects.all()}
    artikel = Artikel.objects.filter(Q(pk__in=summen.keys()) | Q(pk__in=lagerdaten.keys())).select_related("einheit")
    if q:
        artikel = artikel.filter(Q(artikelnummer__icontains=q) | Q(bezeichnung__icontains=q))
    zeilen, gesamtwert = [], Decimal(0)
    for a in artikel.order_by("artikelnummer"):
        ld = lagerdaten.get(a.pk)
        frei = summen.get(a.pk, {}).get(Bestandsart.FREI, Decimal(0))
        gesperrt = summen.get(a.pk, {}).get(Bestandsart.GESPERRT, Decimal(0))
        status = ""
        if ld and ld.mindestbestand > 0 and frei < ld.mindestbestand:
            status = "kritisch"
        elif ld and ld.meldebestand > 0 and frei < ld.meldebestand:
            status = "melden"
        if nur_kritisch and not status:
            continue
        wert = ld.bestandswert if ld else Decimal(0)
        gesamtwert += wert
        zeilen.append({"artikel": a, "ld": ld, "frei": frei, "gesperrt": gesperrt, "gesamt": frei + gesperrt,
                       "wert": wert, "status": status})
    return render(request, "lager/uebersicht.html", {
        "zeilen": zeilen, "gesamtwert": gesamtwert, "q": q, "nur_kritisch": nur_kritisch,
    })


@recht_erforderlich(M)
def artikel(request, pk):
    a = get_object_or_404(Artikel.objects.select_related("einheit"), pk=pk)
    return render(request, "lager/artikel.html", {
        "artikel": a, "ld": ArtikelLager.objects.filter(artikel=a).first(),
        "bestaende": Bestand.objects.filter(artikel=a, menge__gt=0).select_related(
            "lagerort", "lagerplatz", "charge").order_by("lagerort__code", "lagerplatz__code"),
        "summen": services.bestand_summen(a),
        "bewegungen": Lagerbewegung.objects.filter(artikel=a).select_related(
            "charge", "von_lagerort", "von_lagerplatz", "nach_lagerort", "nach_lagerplatz", "benutzer")[:30],
    })


@recht_erforderlich(M)
def journal(request):
    qs = Lagerbewegung.objects.select_related(
        "artikel", "charge", "von_lagerort", "von_lagerplatz", "nach_lagerort", "nach_lagerplatz", "benutzer")
    f = request.GET
    if f.get("art"):
        qs = qs.filter(art=f["art"])
    if f.get("q"):
        qs = qs.filter(Q(artikel__artikelnummer__icontains=f["q"]) | Q(artikel__bezeichnung__icontains=f["q"])
                       | Q(charge__chargennummer__icontains=f["q"]) | Q(beleg_nummer__icontains=f["q"]))
    if f.get("von"):
        qs = qs.filter(buchungsdatum__gte=f["von"])
    if f.get("bis"):
        qs = qs.filter(buchungsdatum__lte=f["bis"])
    seite = Paginator(qs, 50).get_page(f.get("page"))
    return render(request, "lager/journal.html", {"page_obj": seite, "arten": Lagerbewegung.Art.choices,
                                                  "f": f})


def _buchung_ausfuehren(request, form, funktion, erfolg):
    if form.is_valid():
        try:
            bewegung = funktion(form.cleaned_data)
        except LagerFehler as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, erfolg.format(b=bewegung))
            return redirect("lager:artikel", bewegung.artikel_id)
    return None


@recht_erforderlich(M, "anlegen")
def buchung(request, art):
    if art not in ("zugang", "abgang"):
        return redirect("lager:uebersicht")
    zugang = art == "zugang"
    form = BuchungForm(request.POST or None, zugang=zugang, initial={"artikel": request.GET.get("artikel")})

    def ausfuehren(d):
        charge = services.charge_holen(d["artikel"], d["chargennummer"], anlegen=zugang)
        ort = (d["lagerort"], d["lagerplatz"], d["bestandsart"])
        return services.buchen(
            Lagerbewegung.Art.ZUGANG if zugang else Lagerbewegung.Art.ABGANG, d["artikel"], d["menge"],
            nach=ort if zugang else None, von=None if zugang else ort, charge=charge,
            preis=d.get("preis"), benutzer=request.user, grund=d["grund"], buchungsdatum=d["buchungsdatum"])

    if request.method == "POST":
        antwort = _buchung_ausfuehren(request, form, ausfuehren, "Bewegung #{b.pk} gebucht.")
        if antwort:
            return antwort
    titel = "Lagerzugang buchen" if zugang else "Lagerabgang buchen"
    return render(request, "lager/buchung.html", {"form": form, "titel": titel})


@recht_erforderlich(M, "anlegen")
def umbuchung(request):
    form = UmbuchungForm(request.POST or None, initial={"artikel": request.GET.get("artikel")})

    def ausfuehren(d):
        charge = services.charge_holen(d["artikel"], d["chargennummer"])
        art = d["bestandsart"]
        return services.buchen(Lagerbewegung.Art.UMBUCHUNG, d["artikel"], d["menge"],
                               von=(d["von_lagerort"], d["von_lagerplatz"], art),
                               nach=(d["nach_lagerort"], d["nach_lagerplatz"], art),
                               charge=charge, benutzer=request.user, grund=d["grund"])

    if request.method == "POST":
        antwort = _buchung_ausfuehren(request, form, ausfuehren, "Umbuchung #{b.pk} durchgeführt.")
        if antwort:
            return antwort
    return render(request, "lager/buchung.html", {"form": form, "titel": "Umbuchung"})


@recht_erforderlich(M)
def inventur_liste(request):
    return render(request, "lager/inventur_liste.html", {
        "inventuren": Inventur.objects.select_related("lagerort")[:100],
        "form": InventurNeuForm() if hat_recht(request.user, M, "anlegen") else None,
    })


@recht_erforderlich(M, "anlegen")
def inventur_neu(request):
    form = InventurNeuForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        inv = services.inventur_anlegen(d["lagerort"], d["stichtag"], d["bezeichnung"], request.user)
        messages.success(request, f"{inv} mit {inv.positionen.count()} Zählpositionen angelegt.")
        return redirect("lager:inventur", inv.pk)
    return redirect("lager:inventur_liste")


@recht_erforderlich(M)
def inventur(request, pk):
    inv = get_object_or_404(Inventur.objects.select_related("lagerort"), pk=pk)
    qs = inv.positionen.select_related("artikel", "artikel__einheit", "lagerplatz", "charge")
    offen = inv.status == Inventur.Status.OFFEN
    formset = ZaehlFormSet(request.POST or None, queryset=qs, prefix="z")
    if request.method == "POST" and offen:
        buchen = "buchen" in request.POST
        if not hat_recht(request.user, M, "freigeben" if buchen else "aendern"):
            messages.error(request, "Dafür fehlt Ihnen das Recht.")
        elif formset.is_valid():
            try:
                with transaction.atomic():
                    formset.save()
                    if buchen:
                        anzahl = services.inventur_buchen(inv, request.user)
                        messages.success(request, f"{inv} gebucht – {anzahl} Korrekturbuchung(en).")
                    else:
                        messages.success(request, "Zählergebnisse gespeichert.")
                return redirect("lager:inventur", inv.pk)
            except LagerFehler as exc:
                messages.error(request, str(exc))
    return render(request, "lager/inventur.html", {
        "inventur": inv, "formset": formset, "positionen": qs, "offen": offen,
        "darf_zaehlen": hat_recht(request.user, M, "aendern"), "darf_buchen": hat_recht(request.user, M, "freigeben"),
    })

"""Web-Oberfläche Produktion: Dashboard, Fertigungsaufträge, BDE-Terminal, Stempelungen, Akkord, Nachkalkulation.

Rechte: Modul „produktion“ (Aufträge, Material, Rückmeldung, Fertigmeldung), „bde“ (Terminal und Stempelungen;
Korrektur = ändern) und „akkord“ (Lohnauswertung und Lohndaten der Mitarbeiter).
"""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.permissions import hat_recht, recht_erforderlich
from apps.lager.models import Bestand

from . import services
from .forms import (
    AkkordFilterForm, EntnahmeForm, FAForm, FertigmeldungForm, KorrekturForm, NachtragForm, RueckmeldungForm,
    StornoForm,
)
from .models import FAMaterial, Fertigungsauftrag, ProduktionFehler, Rueckmeldung, Stempelung
from .pdf import akkord_pdf, laufkarte_pdf

M, BDE, AKKORD = "produktion", "bde", "akkord"
S = Fertigungsauftrag.Status


def dashboard_hinweise(request):
    """Hinweise für das Start-Dashboard (überfällige Aufträge, Fehlteile freigegebener Aufträge)."""
    hinweise = []
    heute = timezone.localdate()
    offen = Fertigungsauftrag.objects.filter(status__in=Fertigungsauftrag.OFFENE_STATI).select_related("artikel")
    for fa in offen.filter(termin__lt=heute).order_by("termin")[:4]:
        hinweise.append({"stufe": "danger", "text": f"{fa.nummer} ({fa.artikel.artikelnummer}) ist seit "
                                                    f"{fa.termin:%d.%m.%Y} überfällig.",
                         "url": reverse("produktion:detail", args=[fa.pk])})
    for fa in offen.filter(status=S.FREIGEGEBEN)[:10]:
        fehl = services.fehlteile(fa, nur_fehlend=True)
        if fehl:
            hinweise.append({"stufe": "warning", "text": f"{fa.nummer}: {len(fehl)} Fehlteil(e), z. B. "
                                                         f"{fehl[0]['artikel'].artikelnummer}.",
                             "url": reverse("produktion:detail", args=[fa.pk])})
    return hinweise


@recht_erforderlich(M)
def index(request):
    offen = Fertigungsauftrag.objects.filter(status__in=Fertigungsauftrag.OFFENE_STATI).select_related("artikel")
    heute = timezone.localdate()
    laufend = list(services.laufende_stempelungen())
    anwesend = list(services.anwesende())
    n_ueberfaellig = offen.filter(termin__lt=heute).count()
    kacheln = [
        ("Fertigungsaufträge", "produktion:liste", "bi-list-task",
         f"{offen.count()} offen · {n_ueberfaellig} überfällig"),
        ("Rückmeldungen", "produktion:rueckmeldungen", "bi-clipboard-check",
         f"{Rueckmeldung.objects.filter(zeitpunkt__date=heute).count()} heute"),
    ]
    u = request.user
    if hat_recht(u, BDE):
        kacheln += [("Stempel-Terminal", "produktion:terminal", "bi-upc-scan", f"{len(anwesend)} anwesend"),
                    ("Stempelungen", "produktion:stempelungen", "bi-clock-history", f"{len(laufend)} laufend")]
    if hat_recht(u, AKKORD):
        kacheln += [("Akkordauswertung", "produktion:akkord", "bi-cash-coin", "Leistungsgrad, Verdienst, CSV/PDF"),
                    ("Mitarbeiter", "produktion:mitarbeiter_liste", "bi-people", "Personal- und Lohndaten")]
    return render(request, "produktion/index.html", {
        "kacheln": kacheln, "auftraege": offen.order_by("termin", "nummer")[:15], "laufend": laufend,
        "anwesend": anwesend, "leistungsgrade": services.leistungsgrade_arbeitsgaenge(), "heute": heute,
        "darf_bde": hat_recht(u, BDE),
    })


@recht_erforderlich(M)
def liste(request):
    qs = Fertigungsauftrag.objects.select_related("artikel", "uebergeordnet")
    status = request.GET.get("status", "offen")
    if status == "offen":
        qs = qs.filter(status__in=Fertigungsauftrag.OFFENE_STATI)
    elif status:
        qs = qs.filter(status=status)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(nummer__icontains=q) | Q(artikel__artikelnummer__icontains=q)
                       | Q(artikel__bezeichnung__icontains=q))
    seite = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "produktion/liste.html", {"page_obj": seite, "stati": S.choices, "status": status,
                                                     "q": q, "heute": timezone.localdate()})


@recht_erforderlich(M, "anlegen")
def neu(request):
    form = FAForm(request.POST or None, initial={"artikel": request.GET.get("artikel"),
                                                 "menge": request.GET.get("menge")})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            fa = services.fa_anlegen(d["artikel"], d["menge"], termin=d["termin"], starttermin=d["starttermin"],
                                     ziel_lagerort=d["ziel_lagerort"], bemerkung=d["bemerkung"], benutzer=request.user)
            messages.success(request, f"Fertigungsauftrag {fa.nummer} angelegt (Entwurf).")
            return redirect("produktion:detail", fa.pk)
        except ProduktionFehler as exc:
            form.add_error(None, str(exc))
    return render(request, "produktion/formular.html", {"form": form, "titel": "Neuer Fertigungsauftrag"})


@recht_erforderlich(M)
def detail(request, pk):
    fa = get_object_or_404(Fertigungsauftrag.objects.select_related(
        "artikel", "artikel__einheit", "stueckliste", "arbeitsplan", "ziel_lagerort", "uebergeordnet"), pk=pk)
    u = request.user
    offen = fa.status in Fertigungsauftrag.OFFENE_STATI
    stempelungen = Stempelung.objects.filter(arbeitsgang__auftrag=fa).select_related(
        "mitarbeiter", "arbeitsgang").order_by("-beginn")
    return render(request, "produktion/detail.html", {
        "fa": fa, "fehlteile": services.fehlteile(fa), "arbeitsgaenge": fa.arbeitsgaenge.select_related("arbeitsplatz"),
        "stempelungen": stempelungen[:50],
        "rueckmeldungen": Rueckmeldung.objects.filter(arbeitsgang__auftrag=fa).select_related(
            "arbeitsgang", "mitarbeiter")[:50],
        "entnahmen": fa.entnahmen.select_related("artikel", "charge", "lagerort", "lagerplatz"),
        "fertigmeldungen": fa.fertigmeldungen.select_related("charge", "lagerort", "lagerplatz"),
        "unterauftraege": fa.unterauftraege.select_related("artikel"),
        "storno_form": StornoForm(), "offen": offen,
        "darf_anlegen": offen and hat_recht(u, M, "anlegen"),
        "darf_freigeben": hat_recht(u, M, "freigeben"),
        "darf_rueckmelden": offen and fa.status != S.ENTWURF and hat_recht(u, M, "anlegen"),
        "darf_loeschen": fa.status == S.ENTWURF and hat_recht(u, M, "loeschen"),
        "darf_bde": hat_recht(u, BDE),
    })


@require_POST
@recht_erforderlich(M, "freigeben")
def aktion(request, pk, was):
    fa = get_object_or_404(Fertigungsauftrag, pk=pk)
    try:
        if was == "freigeben":
            fa, unter = services.freigeben(fa, request.user, unterauftraege=bool(request.POST.get("unterauftraege")))
            messages.success(request, f"{fa.nummer} freigegeben – Material und Arbeitsgänge übernommen.")
            for ua in unter:
                messages.info(request, f"Unterauftrag {ua.nummer} für {ua.artikel.artikelnummer} "
                                       f"({ua.menge.normalize():f}) als Entwurf angelegt.")
            if fehl := services.fehlteile(fa, nur_fehlend=True):
                messages.warning(request, f"Achtung: {len(fehl)} Fehlteil(e) – siehe Materialliste.")
        elif was == "stornieren":
            services.stornieren(fa, request.user, request.POST.get("grund", ""))
            messages.success(request, f"{fa.nummer} wurde storniert.")
        else:
            messages.error(request, "Unbekannte Aktion.")
    except ProduktionFehler as exc:
        messages.error(request, str(exc))
    return redirect("produktion:detail", pk)


@require_POST
@recht_erforderlich(M, "loeschen")
def loeschen(request, pk):
    fa = get_object_or_404(Fertigungsauftrag, pk=pk)
    if fa.status != S.ENTWURF or fa.unterauftraege.exists():
        messages.error(request, "Nur Entwürfe ohne Unteraufträge können gelöscht werden (sonst stornieren).")
        return redirect("produktion:detail", pk)
    fa.delete()
    messages.success(request, f"Entwurf {fa.nummer} gelöscht.")
    return redirect("produktion:liste")


@recht_erforderlich(M)
def laufkarte(request, pk):
    fa = get_object_or_404(Fertigungsauftrag, pk=pk)
    antwort = HttpResponse(laufkarte_pdf(fa), content_type="application/pdf")
    antwort["Content-Disposition"] = f'inline; filename="Laufkarte-{fa.nummer}.pdf"'
    return antwort


@recht_erforderlich(M, "anlegen")
def entnahme(request, pk):
    fa = get_object_or_404(Fertigungsauftrag, pk=pk)
    if fa.status not in Fertigungsauftrag.OFFENE_STATI or fa.status == S.ENTWURF:
        messages.error(request, "Material kann nur bei freigegebenen, offenen Aufträgen entnommen werden.")
        return redirect("produktion:detail", pk)
    if request.method == "POST":
        try:
            if "alles" in request.POST:
                erledigt, ueber = services.restmaterial_entnehmen(fa, request.user)
                for m in erledigt:
                    messages.success(request, f"{m.artikel.artikelnummer} vollständig entnommen.")
                for m, grund in ueber:
                    messages.warning(request, f"{m.artikel.artikelnummer}: {grund}")
            else:
                material = get_object_or_404(FAMaterial, pk=request.POST.get("material"), auftrag=fa)
                form = EntnahmeForm(request.POST)
                if not form.is_valid():
                    raise ProduktionFehler("Bitte eine gültige Menge angeben.")
                bestand = None
                if request.POST.get("bestand"):
                    bestand = get_object_or_404(Bestand, pk=request.POST["bestand"])
                services.material_entnehmen(fa, material, form.cleaned_data["menge"], bestand=bestand,
                                            benutzer=request.user)
                messages.success(request, f"{form.cleaned_data['menge'].normalize():f} "
                                          f"{material.artikel.einheit.code} {material.artikel.artikelnummer} entnommen.")
        except ProduktionFehler as exc:
            messages.error(request, str(exc))
        return redirect("produktion:entnahme", pk)
    zeilen = []
    for m in fa.materialien.select_related("artikel", "artikel__einheit", "unterauftrag"):
        zeilen.append({"m": m, "bestaende": list(services.entnahme_bestaende(m.artikel))})
    return render(request, "produktion/entnahme.html", {"fa": fa, "zeilen": zeilen,
                                                        "entnahmen": fa.entnahmen.select_related("artikel", "charge")})


@recht_erforderlich(M, "anlegen")
def fertigmelden(request, pk):
    fa = get_object_or_404(Fertigungsauftrag.objects.select_related("artikel", "ziel_lagerort"), pk=pk)
    if fa.status not in Fertigungsauftrag.OFFENE_STATI or fa.status == S.ENTWURF:
        messages.error(request, "Fertigmeldung nur für freigegebene, offene Aufträge.")
        return redirect("produktion:detail", pk)
    letzter = fa.arbeitsgaenge.order_by("-position").first()
    vorschlag = max((letzter.gut_menge if letzter else fa.menge) - fa.gut_menge, 0)
    form = FertigmeldungForm(request.POST or None, initial={"menge": vorschlag or None, "lagerort": fa.ziel_lagerort})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            fm = services.fertigmelden(fa, d["menge"], lagerort=d["lagerort"], lagerplatz=d["lagerplatz"],
                                       chargennummer=d["chargennummer"], abschliessen=d["abschliessen"],
                                       benutzer=request.user)
            messages.success(request, f"{fm.menge.normalize():f} {fa.artikel.artikelnummer} zu "
                                      f"{fm.herstellkosten_stueck:.4f} €/Einheit eingelagert (Charge {fm.charge}).")
            return redirect("produktion:detail", pk)
        except ProduktionFehler as exc:
            form.add_error(None, str(exc))
    return render(request, "produktion/fertigmelden.html", {
        "fa": fa, "form": form, "ist": services.ist_kosten(fa), "letzter": letzter})


@recht_erforderlich(M)
def rueckmeldungen(request):
    darf = hat_recht(request.user, M, "anlegen")
    auftrag = None
    if request.GET.get("auftrag"):
        auftrag = get_object_or_404(Fertigungsauftrag, pk=request.GET["auftrag"])
    form = RueckmeldungForm(request.POST or None, auftrag=auftrag) if darf else None
    if request.method == "POST" and darf and form.is_valid():
        d = form.cleaned_data
        try:
            services.rueckmelden(d["arbeitsgang"], d["gut"], d["ausschuss"], d["grund"], d["nacharbeit"],
                                 mitarbeiter=d["mitarbeiter"], benutzer=request.user, bemerkung=d["bemerkung"])
            messages.success(request, f"Rückmeldung zu {d['arbeitsgang'].code} gespeichert.")
            return redirect(request.get_full_path())
        except ProduktionFehler as exc:
            form.add_error(None, str(exc))
    qs = Rueckmeldung.objects.select_related("arbeitsgang__auftrag", "mitarbeiter", "erfasst_von")
    if auftrag:
        qs = qs.filter(arbeitsgang__auftrag=auftrag)
    return render(request, "produktion/rueckmeldungen.html", {
        "form": form, "auftrag": auftrag, "page_obj": Paginator(qs, 50).get_page(request.GET.get("page"))})


@recht_erforderlich(M)
def nachkalkulation(request, pk):
    fa = get_object_or_404(Fertigungsauftrag.objects.select_related("artikel", "artikel__einheit"), pk=pk)
    return render(request, "produktion/nachkalkulation.html", {"fa": fa, "k": services.nachkalkulation(fa)})


# ---------------------------------------------------------------- BDE

@recht_erforderlich(BDE)
def terminal(request):
    """Stempel-Terminal: Personalnummer (oder Ausweis) + Arbeitsgang-Code; Enter springt weiter, ein leerer
    Code mit Enter bucht automatisch (Kommen bzw. Start). Buchen braucht bde/anlegen."""
    meldung = fehler = None
    letzte_kennung = ""
    if request.method == "POST":
        if not hat_recht(request.user, BDE, "anlegen"):
            raise PermissionDenied
        letzte_kennung = request.POST.get("kennung", "")
        try:
            ma, meldung = services.terminal_buchen(
                letzte_kennung, request.POST.get("code", ""), request.POST.get("aktion") or "auto",
                gut=_zahl(request.POST.get("gut")), ausschuss=_zahl(request.POST.get("ausschuss")),
                grund=request.POST.get("grund", ""), nacharbeit=_zahl(request.POST.get("nacharbeit")),
                benutzer=request.user)
            letzte_kennung = ""
        except ProduktionFehler as exc:
            fehler = str(exc)
    return render(request, "produktion/terminal.html", {
        "meldung": meldung, "fehler": fehler, "kennung": letzte_kennung,
        "laufend": services.laufende_stempelungen(), "anwesend": services.anwesende(),
        "gruende": Rueckmeldung.Grund.choices, "jetzt": timezone.localtime(),
    })


def _zahl(text):
    text = (text or "").strip().replace(".", "").replace(",", ".") if "," in (text or "") else (text or "").strip()
    try:
        return services._d(text or 0)
    except Exception as exc:
        raise ProduktionFehler(f"„{text}“ ist keine gültige Zahl.") from exc


@recht_erforderlich(BDE)
def stempelungen(request):
    qs = Stempelung.objects.select_related("mitarbeiter", "arbeitsgang__auftrag")
    if request.GET.get("mitarbeiter"):
        qs = qs.filter(mitarbeiter_id=request.GET["mitarbeiter"])
    if request.GET.get("datum"):
        qs = qs.filter(beginn__date=request.GET["datum"])
    if request.GET.get("korrigiert"):
        qs = qs.filter(korrigiert=True)
    from apps.stammdaten.models import Mitarbeiter

    return render(request, "produktion/stempelungen.html", {
        "page_obj": Paginator(qs.order_by("-beginn"), 50).get_page(request.GET.get("page")),
        "mitarbeiter": Mitarbeiter.objects.filter(aktiv=True), "darf_aendern": hat_recht(request.user, BDE, "aendern"),
        "nachtrag_form": NachtragForm() if hat_recht(request.user, BDE, "aendern") else None,
    })


@recht_erforderlich(BDE, "aendern")
def stempel_korrektur(request, pk):
    st = get_object_or_404(Stempelung.objects.select_related("mitarbeiter", "arbeitsgang__auftrag"), pk=pk)
    initial = {"beginn": timezone.localtime(st.beginn), "taetigkeit": st.taetigkeit,
               "ende": timezone.localtime(st.ende) if st.ende else None}
    form = KorrekturForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            services.stempelung_korrigieren(st, beginn=d["beginn"], ende=d["ende"], grund=d["grund"],
                                            taetigkeit=d["taetigkeit"], benutzer=request.user)
            messages.success(request, "Stempelung korrigiert – Vorher/Nachher steht im Audit-Log.")
            return redirect("produktion:stempelungen")
        except ProduktionFehler as exc:
            form.add_error(None, str(exc))
    return render(request, "produktion/korrektur.html", {"st": st, "form": form})


@require_POST
@recht_erforderlich(BDE, "aendern")
def stempel_nachtrag(request):
    form = NachtragForm(request.POST)
    if form.is_valid():
        d = form.cleaned_data
        try:
            ag = services.arbeitsgang_finden(d["code"])
            services.stempelung_nachtragen(d["mitarbeiter"], ag, beginn=d["beginn"], ende=d["ende"], grund=d["grund"],
                                           taetigkeit=d["taetigkeit"], benutzer=request.user)
            messages.success(request, f"Stempelung für {d['mitarbeiter'].name} an {ag.code} nachgetragen.")
        except ProduktionFehler as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, "Nachtrag unvollständig: " + "; ".join(
            f"{form.fields[f].label if f in form.fields else ''} {' '.join(e)}" for f, e in form.errors.items()))
    return redirect("produktion:stempelungen")


# ---------------------------------------------------------------- Akkord

def _akkord_daten(request):
    daten = request.GET if request.GET.get("von") else AkkordFilterForm.standard()
    form = AkkordFilterForm(daten)
    if not form.is_valid():
        return form, None
    d = form.cleaned_data
    return form, services.akkord_auswertung(d["von"], d["bis"], mitarbeiter=d["mitarbeiter"], auftrag=d["auftrag"])


@recht_erforderlich(AKKORD)
def akkord(request):
    form, auswertung = _akkord_daten(request)
    return render(request, "produktion/akkord.html", {"form": form, "a": auswertung,
                                                      "query": request.GET.urlencode()})


@recht_erforderlich(AKKORD)
def akkord_pdf_view(request):
    form, auswertung = _akkord_daten(request)
    if auswertung is None:
        messages.error(request, "Bitte einen gültigen Zeitraum wählen.")
        return redirect("produktion:akkord")
    antwort = HttpResponse(akkord_pdf(auswertung), content_type="application/pdf")
    antwort["Content-Disposition"] = f'inline; filename="Akkord-{auswertung["von"]:%Y%m%d}-{auswertung["bis"]:%Y%m%d}.pdf"'
    return antwort


@recht_erforderlich(AKKORD)
def akkord_csv_view(request):
    form, auswertung = _akkord_daten(request)
    if auswertung is None:
        messages.error(request, "Bitte einen gültigen Zeitraum wählen.")
        return redirect("produktion:akkord")
    antwort = HttpResponse(services.akkord_csv(auswertung), content_type="text/csv; charset=utf-8")
    antwort["Content-Disposition"] = (f'attachment; filename="Akkord-{auswertung["von"]:%Y%m%d}-'
                                      f'{auswertung["bis"]:%Y%m%d}.csv"')
    return antwort


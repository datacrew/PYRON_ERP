from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse

from .forms import BenutzerForm, ErpModelForm, RolleForm
from .models import AKTIONEN, AuditLog, Mandant, Modul, Recht, Rolle
from .permissions import hat_recht, recht_erforderlich, rechte_cache_leeren

User = get_user_model()


def _modul_url(modul):
    try:
        return reverse(modul.url_name) if modul.url_name else reverse("core:platzhalter", args=[modul.code])
    except NoReverseMatch:
        return reverse("core:platzhalter", args=[modul.code])


@login_required
def dashboard(request):
    from apps.belege.models import Beleg
    from apps.stammdaten.models import Artikel, Geschaeftspartner

    from .context_processors import erp_kontext

    kacheln = [{"modul": m, "url": _modul_url(m)} for m in erp_kontext(request).get("erp_module", [])]
    kennzahlen = {}
    if hat_recht(request.user, "stammdaten"):
        kennzahlen["Artikel"] = Artikel.objects.filter(aktiv=True).count()
        kennzahlen["Geschäftspartner"] = Geschaeftspartner.objects.filter(aktiv=True).count()
    letzte_belege = []
    if hat_recht(request.user, "belege"):
        kennzahlen["Belege im Entwurf"] = Beleg.objects.filter(status=Beleg.Status.ENTWURF).count()
        kennzahlen["Festgeschriebene Belege"] = Beleg.objects.filter(status=Beleg.Status.FESTGESCHRIEBEN).count()
        letzte_belege = Beleg.objects.select_related("partner").order_by("-pk")[:8]
    kennzahlen.update(_warenfluss_kennzahlen(request.user))
    return render(request, "core/dashboard.html", {"kacheln": kacheln, "kennzahlen": kennzahlen,
                                                   "letzte_belege": letzte_belege,
                                                   "hinweise": _modul_hinweise(request, kacheln)})


def _modul_hinweise(request, kacheln):
    """Sammelt Hinweise der Module (AppConfig.erp_modul["dashboard"] = Pfad zu einer Funktion(request))."""
    from django.apps import apps
    from django.utils.module_loading import import_string

    erlaubt = {k["modul"].code for k in kacheln}
    hinweise = []
    for cfg in apps.get_app_configs():
        info = getattr(cfg, "erp_modul", None) or {}
        if info.get("dashboard") and info["code"] in erlaubt:
            for h in import_string(info["dashboard"])(request)[:8]:
                hinweise.append({**h, "modul": info["name"]})
    return hinweise


def _warenfluss_kennzahlen(user):
    werte = {}
    if hat_recht(user, "einkauf"):
        from apps.einkauf.models import Bestellung

        werte["Offene Bestellungen"] = Bestellung.objects.filter(
            typ=Bestellung.Typ.BESTELLUNG, status__in=Bestellung.OFFENE_STATI).count()
    if hat_recht(user, "wareneingang"):
        from apps.wareneingang.models import WEPosition

        werte["Offene QS-Prüfungen"] = WEPosition.objects.filter(qs_status=WEPosition.QS.OFFEN).count()
    if hat_recht(user, "lager"):
        from django.db.models import F, Sum

        from apps.lager.models import ArtikelLager

        wert = ArtikelLager.objects.aggregate(s=Sum(F("bestand_gesamt") * F("durchschnittspreis")))["s"] or 0
        werte["Lagerwert"] = f"{wert:,.2f} €".replace(",", "§").replace(".", ",").replace("§", ".")
    if hat_recht(user, "produktion"):
        from apps.produktion.models import Fertigungsauftrag, Stempelung

        werte["Offene Fertigungsaufträge"] = Fertigungsauftrag.objects.filter(
            status__in=Fertigungsauftrag.OFFENE_STATI).count()
        werte["Angestempelt"] = Stempelung.objects.filter(ende__isnull=True).count()
    if hat_recht(user, "fibu"):
        from apps.fibu.services import euro
        from apps.fibu.views import kennzahlen

        k = kennzahlen()
        werte["Offene Forderungen"] = euro(k["debitoren"])
        werte["Offene Verbindlichkeiten"] = euro(k["kreditoren"])
        werte["Bankbestand"] = euro(k["liquiditaet"])
    return werte


@login_required
def platzhalter(request, code):
    modul = get_object_or_404(Modul, code=code)
    if not modul.aktiv:
        raise Http404("Modul ist deaktiviert.")
    if not hat_recht(request.user, code, "lesen"):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied
    if modul.url_name:  # Modul inzwischen umgesetzt → auf die echte Startseite weiterleiten
        return redirect(modul.url_name)
    return render(request, "core/platzhalter.html", {"modul": modul})


# ---------------------------------------------------------------- Verwaltung

@recht_erforderlich("verwaltung")
def verwaltung(request):
    return render(request, "core/verwaltung.html", {
        "anzahl_benutzer": User.objects.count(),
        "anzahl_rollen": Rolle.objects.count(),
        "anzahl_module": Modul.objects.count(),
    })


@recht_erforderlich("verwaltung")
def benutzer_liste(request):
    benutzer = User.objects.prefetch_related("erp_rollen").order_by("username")
    return render(request, "core/benutzer_liste.html", {"benutzer": benutzer})


@recht_erforderlich("verwaltung", "aendern")
def benutzer_bearbeiten(request, pk=None):
    user = get_object_or_404(User, pk=pk) if pk else User()
    form = BenutzerForm(request.POST or None, instance=user)
    if request.method == "POST" and form.is_valid():
        if user.pk == request.user.pk and not form.cleaned_data["is_active"]:
            form.add_error("is_active", "Sie können sich nicht selbst deaktivieren.")
        else:
            form.save()
            messages.success(request, f"Benutzer „{form.instance.username}“ gespeichert.")
            return redirect("core:benutzer_liste")
    return render(request, "core/formular_einfach.html",
                  {"form": form, "titel": "Benutzer", "zurueck": reverse("core:benutzer_liste")})


@recht_erforderlich("verwaltung")
def rollen_liste(request):
    return render(request, "core/rollen_liste.html",
                  {"rollen": Rolle.objects.prefetch_related("benutzer", "rechte__modul")})


@recht_erforderlich("verwaltung", "aendern")
def rolle_bearbeiten(request, pk=None):
    """Rolle mit Rechte-Matrix (Modul × Aktion) und Mitgliedern bearbeiten."""
    rolle = get_object_or_404(Rolle, pk=pk) if pk else Rolle()
    form = RolleForm(request.POST or None, instance=rolle)
    module = list(Modul.objects.all())
    alle_benutzer = User.objects.order_by("username")
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            rolle = form.save()
            for m in module:
                werte = {a: f"r_{m.code}_{a}" in request.POST for a, _ in AKTIONEN}
                if any(werte.values()):
                    Recht.objects.update_or_create(rolle=rolle, modul=m, defaults=werte)
                else:
                    Recht.objects.filter(rolle=rolle, modul=m).delete()
            rolle.benutzer.set(User.objects.filter(pk__in=request.POST.getlist("benutzer")))
        rechte_cache_leeren(request.user)
        messages.success(request, f"Rolle „{rolle}“ und Rechte gespeichert.")
        return redirect("core:rollen_liste")
    vorhanden = {r.modul_id: r for r in rolle.rechte.all()} if rolle.pk else {}
    matrix = [
        {"modul": m, "zellen": [(a, label, bool(vorhanden.get(m.pk) and getattr(vorhanden[m.pk], a)))
                                for a, label in AKTIONEN]}
        for m in module
    ]
    mitglieder = set(rolle.benutzer.values_list("pk", flat=True)) if rolle.pk else set()
    return render(request, "core/rolle_formular.html", {
        "form": form, "rolle": rolle, "matrix": matrix, "aktionen": AKTIONEN,
        "alle_benutzer": alle_benutzer, "mitglieder": mitglieder,
    })


@recht_erforderlich("verwaltung", "loeschen")
def rolle_loeschen(request, pk):
    rolle = get_object_or_404(Rolle, pk=pk)
    if request.method == "POST":
        rolle.delete()
        messages.success(request, f"Rolle „{rolle}“ gelöscht.")
    return redirect("core:rollen_liste")


@recht_erforderlich("verwaltung")
def module_verwalten(request):
    if request.method == "POST":
        if not hat_recht(request.user, "verwaltung", "aendern"):
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied
        modul = get_object_or_404(Modul, pk=request.POST.get("modul"))
        if modul.pflicht:
            messages.error(request, f"Das Pflichtmodul „{modul}“ kann nicht deaktiviert werden.")
        else:
            modul.aktiv = not modul.aktiv
            modul.save(update_fields=["aktiv"])
            messages.success(request, f"Modul „{modul}“ ist jetzt {'aktiv' if modul.aktiv else 'deaktiviert'}.")
        return redirect("core:module")
    return render(request, "core/module.html", {"module": Modul.objects.all()})


class MandantForm(ErpModelForm):
    class Meta:
        model = Mandant
        fields = "__all__"


@recht_erforderlich("verwaltung")
def mandant_bearbeiten(request):
    mandant = Mandant.aktuell() or Mandant(name="Meine Firma GmbH")
    form = MandantForm(request.POST or None, instance=mandant)
    if request.method == "POST":
        if not hat_recht(request.user, "verwaltung", "aendern"):
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied
        if form.is_valid():
            form.save()
            messages.success(request, "Firmendaten gespeichert.")
            return redirect("core:verwaltung")
    return render(request, "core/formular_einfach.html",
                  {"form": form, "titel": "Firmendaten (Mandant)", "zurueck": reverse("core:verwaltung")})


@recht_erforderlich("verwaltung")
def audit_log(request):
    qs = AuditLog.objects.select_related("benutzer")
    for feld in ("app_label", "aktion", "benutzername"):
        wert = request.GET.get(feld)
        if wert:
            qs = qs.filter(**{feld: wert})
    if request.GET.get("objekt"):
        qs = qs.filter(objekt_text__icontains=request.GET["objekt"])
    seite = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "core/audit_log.html", {"seite": seite, "page_obj": seite, "aktionen": AuditLog.AKTIONEN})

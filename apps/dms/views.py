"""Web-Oberfläche DMS (Rechte siehe apps.dms.apps.DmsConfig)."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from apps.core.permissions import hat_recht, recht_erforderlich

from . import services as S
from .forms import MetadatenForm, TagForm, UploadForm, VersionForm
from .models import Dokument, DokumentVersion, DmsGesperrt, Dokumentart, Stapel, Tag

M = "dms"
FEHLER = (S.DmsFehler, DmsGesperrt)


def _fehler(request, exc):
    messages.error(request, str(exc))


# ------------------------------------------------------------------ Übersicht / Suche

@recht_erforderlich(M)
def index(request):
    zaehlung = dict(Dokument.objects.values_list("status").annotate(n=Count("pk")))
    return render(request, "dms/index.html", {
        "zaehlung": [(code, text, zaehlung.get(code, 0)) for code, text in Dokument.Status.choices],
        "zu_pruefen": Dokument.objects.filter(status__in=[Dokument.Status.PRUEFEN, Dokument.Status.ERKANNT],
                                              art=Dokumentart.EINGANGSRECHNUNG)
        .select_related("partner").order_by("erstellt_am")[:15],
        "fehler": Dokument.objects.filter(status=Dokument.Status.FEHLER).order_by("-erstellt_am")[:10],
        "stapel": Stapel.objects.annotate(n=Count("dokumente")).order_by("-erstellt_am")[:8],
        "archiviert": Dokument.objects.filter(archiviert=True).count(),
        "aktiv": "index"})


@recht_erforderlich(M)
def liste(request):
    qs = Dokument.objects.select_related("partner").prefetch_related("tags")
    f = {k: (request.GET.get(k) or "").strip() for k in ("q", "art", "status", "tag", "partner", "archiv")}
    if f["art"]:
        qs = qs.filter(art=f["art"])
    if f["status"]:
        qs = qs.filter(status=f["status"])
    if f["tag"]:
        qs = qs.filter(tags__pk=f["tag"])
    if f["partner"]:
        qs = qs.filter(Q(partner__name__icontains=f["partner"]) | Q(partner__nummer=f["partner"]))
    if f["archiv"] in ("ja", "nein"):
        qs = qs.filter(archiviert=f["archiv"] == "ja")
    qs = S.suchen(qs, f["q"]) if f["q"] else qs.order_by("-erstellt_am", "-pk")
    seite = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "dms/liste.html", {
        "page_obj": seite, "f": f, "arten": Dokumentart.choices, "status": Dokument.Status.choices,
        "tags": Tag.objects.all(), "aktiv": "liste"})


@recht_erforderlich(M)
def fristen(request):
    """Aufbewahrungsfristen: archivierte Dokumente nach Fristende (abgelaufene zuerst)."""
    heute = timezone.localdate()
    qs = Dokument.objects.filter(archiviert=True).select_related("partner").order_by("aufbewahren_bis", "pk")
    seite = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "dms/fristen.html", {
        "page_obj": seite, "heute": heute, "aktiv": "fristen",
        "abgelaufen": qs.filter(aufbewahren_bis__lt=heute).count()})


# ------------------------------------------------------------------ Eingang

@recht_erforderlich(M, "anlegen")
def hochladen(request):
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        stapel = S.stapel_anlegen(bezeichnung=cd["bezeichnung"] or f"Upload {timezone.localtime():%d.%m.%Y %H:%M}",
                                  art=cd["art"], automatisch_trennen=cd["automatisch_trennen"],
                                  benutzer=request.user)
        angelegt, fehler = [], []
        for datei in cd["dateien"]:
            try:
                angelegt.append(S.dokument_anlegen(datei.read(), datei.name, art=cd["art"], stapel=stapel,
                                                   partner=cd["partner"], benutzer=request.user))
            except FEHLER as exc:
                fehler.append(str(exc))
        for f in fehler:
            messages.error(request, f)
        if not angelegt:
            stapel.delete()
            return render(request, "dms/hochladen.html", {"form": form, "aktiv": "hochladen"})
        messages.success(request, f"{len(angelegt)} Dokument(e) im Stapel {stapel.nummer} angelegt.")
        if cd.get("sofort_verarbeiten"):
            return redirect(f"{stapel_url(stapel)}?start=1")
        return redirect(stapel_url(stapel))
    return render(request, "dms/hochladen.html", {"form": form, "aktiv": "hochladen"})


def stapel_url(stapel):
    from django.urls import reverse

    return reverse("dms:stapel", args=[stapel.pk])


@recht_erforderlich(M)
def stapel_detail(request, pk):
    stapel = get_object_or_404(Stapel, pk=pk)
    return render(request, "dms/stapel.html", {
        "stapel": stapel, "fortschritt": stapel.fortschritt(), "aktiv": "stapel",
        "dokumente": stapel.dokumente.select_related("partner").order_by("pk"),
        "autostart": request.GET.get("start") == "1" and hat_recht(request.user, M, "anlegen")})


@recht_erforderlich(M)
def stapel_liste(request):
    seite = Paginator(Stapel.objects.annotate(n=Count("dokumente")).order_by("-erstellt_am", "-pk"), 50) \
        .get_page(request.GET.get("page"))
    return render(request, "dms/stapel_liste.html", {"page_obj": seite, "aktiv": "stapel"})


# ------------------------------------------------------------------ Dokument

@recht_erforderlich(M)
def detail(request, pk):
    d = get_object_or_404(Dokument.objects.select_related("partner", "beleg", "buchung", "eingangsrechnung",
                                                          "stapel", "eltern"), pk=pk)
    darf_aendern = hat_recht(request.user, M, "aendern")
    form = MetadatenForm(request.POST or None, instance=d) if darf_aendern else None
    if request.method == "POST":
        if not form:
            raise PermissionDenied
        if form.is_valid():
            try:
                obj = form.save(commit=False)
                obj.beleg = form.cleaned_data["beleg_nummer"]
                obj.buchung = form.cleaned_data["buchung_nummer"] or obj.buchung
                obj.save()
                form.save_m2m()
                S.suchvektor_aktualisieren(obj)
                S.protokollieren(obj, "Metadaten", "Metadaten geändert: " + ", ".join(form.changed_data),
                                 request.user)
            except FEHLER as exc:
                _fehler(request, exc)
            else:
                messages.success(request, "Metadaten gespeichert.")
                return redirect("dms:detail", pk)
    return render(request, "dms/detail.html", {
        "d": d, "form": form, "versionen": d.versionen.select_related("erstellt_von").order_by("-version", "pk"),
        "protokoll": d.protokoll.all(), "teile": d.teile.order_by("seiten_von", "pk"),
        "versionform": VersionForm() if hat_recht(request.user, M, "anlegen") and not d.archiviert else None,
        "aktiv": "liste"})


@xframe_options_sameorigin
@recht_erforderlich(M)
def datei(request, pk, version_pk=None):
    """Liefert die Datei rechtegeprüft aus (inline für die Split-Ansicht, ?download=1 als Anhang)."""
    d = get_object_or_404(Dokument, pk=pk)
    v = get_object_or_404(DokumentVersion, pk=version_pk, dokument=d) if version_pk else d.anzeigeversion
    if not v:
        raise Http404
    return FileResponse(v.datei.open("rb"), content_type=v.mime or "application/octet-stream",
                        as_attachment=request.GET.get("download") == "1", filename=v.dateiname)


@require_POST
@recht_erforderlich(M, "anlegen")
def version_neu(request, pk):
    d = get_object_or_404(Dokument, pk=pk)
    form = VersionForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            f = form.cleaned_data["datei"]
            v = S.neue_version(d, f.read(), f.name, form.cleaned_data["kommentar"], request.user)
        except FEHLER as exc:
            _fehler(request, exc)
        else:
            messages.success(request, f"Version {v.version} angelegt – das Dokument wird neu verarbeitet.")
    else:
        messages.error(request, "Bitte Datei und Kommentar angeben.")
    return redirect("dms:detail", pk)


@require_POST
@recht_erforderlich(M, "aendern")
def archivieren(request, pk):
    d = get_object_or_404(Dokument, pk=pk)
    try:
        S.archivieren(d, request.user)
    except FEHLER as exc:
        _fehler(request, exc)
    else:
        messages.success(request, f"{d.nummer} revisionssicher archiviert (bis {d.aufbewahren_bis:%d.%m.%Y}).")
    return redirect("dms:detail", pk)


@require_POST
@recht_erforderlich(M, "loeschen")
def loeschen(request, pk):
    d = get_object_or_404(Dokument, pk=pk)
    nummer = d.nummer
    try:
        S.loeschen(d, request.user)
    except FEHLER as exc:
        _fehler(request, exc)
        return redirect("dms:detail", pk)
    messages.success(request, f"Dokument {nummer} gelöscht.")
    return redirect("dms:liste")


# ------------------------------------------------------------------ Schlagwörter

@recht_erforderlich(M)
def tags(request):
    form = TagForm(request.POST or None)
    if request.method == "POST":
        if not hat_recht(request.user, M, "aendern"):
            raise PermissionDenied
        if form.is_valid():
            form.save()
            messages.success(request, "Schlagwort angelegt.")
            return redirect("dms:tags")
    return render(request, "dms/tags.html", {"form": form, "aktiv": "tags",
                                             "tags": Tag.objects.annotate(n=Count("dokumente"))})

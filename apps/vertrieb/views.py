"""Web-Oberfläche Vertrieb.

Rechte:
* vertrieb: lesen; anlegen = Leads/Aktivitäten/Angebote/Aufträge/Lieferscheine erfassen;
  freigeben = Auftrag bestätigen, Rechnung/Gutschrift erstellen und festschreiben, Stornos
* versand (anlegen) oder vertrieb (freigeben): Warenausgang eines Lieferscheins buchen
* edi: lesen = Nachrichten, anlegen = ORDERS importieren/Nachrichten erzeugen, aendern = Partner pflegen
* shopapi: lesen = Shops/Bestellungen, aendern = Shop anlegen und API-Token erzeugen
"""
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.belege.models import Beleg
from apps.belege.services import WorkflowFehler
from apps.core.permissions import hat_recht, recht_erforderlich

from . import edifact as ED
from . import erechnung as ER
from . import services as S
from .api import token_erzeugen
from .forms import AngebotKopfForm, AuftragKopfForm, EdiUploadForm, PositionFormSet, ShopForm, StornoForm
from .models import (Auftrag, AuftragPosition, EdiNachricht, EdiPartner, Lead, Lieferung, Shop, ShopBestellung,
                     Verkaufschance, VertriebFehler)

M = "vertrieb"
FEHLER = (VertriebFehler, WorkflowFehler, ED.EdiFehler, ER.ERechnungFehler)


def _seite(request, qs, n=50):
    return Paginator(qs, n).get_page(request.GET.get("seite"))


def _datei(inhalt, name, typ):
    r = HttpResponse(inhalt, content_type=typ)
    r["Content-Disposition"] = f'attachment; filename="{name}"'
    return r


# ------------------------------------------------------------------ Dashboard / CRM

@recht_erforderlich(M)
def index(request):
    k = S.kennzahlen()
    kacheln = [("Leads / Interessenten", "vertrieb:leads_liste", "bi-person-plus", "Status, Umwandlung in Kunden"),
               ("Aktivitäten", "vertrieb:aktivitaeten_liste", "bi-telephone", "Kontakthistorie, Wiedervorlagen"),
               ("Verkaufschancen", "vertrieb:pipeline", "bi-funnel", "Pipeline nach Phase"),
               ("Angebote", "vertrieb:angebote", "bi-file-earmark-text", "Angebote erstellen, in Auftrag übernehmen"),
               ("Kundenaufträge", "vertrieb:auftraege", "bi-cart-check", "Bestätigung, Verfügbarkeit, Lieferung"),
               ("Lieferscheine", "vertrieb:lieferscheine", "bi-box-seam", "Teillieferungen, Warenausgang"),
               ("Rechnungen", "vertrieb:rechnungen", "bi-receipt", "Sammelrechnung, ZUGFeRD, XRechnung"),
               ("Kundenpreise", "vertrieb:kundenpreise_liste", "bi-tags", "Kundenpreise, Rabatte, Staffeln")]
    if hat_recht(request.user, "edi"):
        kacheln.append(("EDI/EDIFACT", "vertrieb:edi", "bi-arrow-left-right", "ORDERS, ORDRSP, DESADV, INVOIC"))
    if hat_recht(request.user, "shopapi"):
        kacheln.append(("Onlineshops", "vertrieb:shops", "bi-cart", "Shop-API, Token, Bestellungen"))
    return render(request, "vertrieb/index.html", {
        "k": k, "pipeline": S.pipeline(), "kacheln": kacheln, "heute": timezone.localdate(),
        "wiedervorlagen": S.faellige_wiedervorlagen(request.user)[:10],
        "auftraege": Auftrag.objects.filter(status__in=Auftrag.OFFENE_STATI).select_related("partner")[:10]})


@recht_erforderlich(M)
def pipeline(request):
    chancen = Verkaufschance.objects.select_related("partner", "lead", "zustaendig")
    spalten = [(p, [c for c in chancen if c.phase == p["code"]]) for p in S.pipeline()]
    return render(request, "vertrieb/pipeline.html", {"spalten": spalten})


@require_POST
@recht_erforderlich(M, "anlegen")
def lead_umwandeln(request, pk):
    lead = get_object_or_404(Lead, pk=pk)
    partner = S.lead_umwandeln(lead, request.user)
    messages.success(request, f"Lead „{lead}“ wurde in Kunde {partner.nummer} umgewandelt.")
    return redirect("stammdaten:partner_bearbeiten", partner.pk) if _hat_url("stammdaten:partner_bearbeiten") \
        else redirect("vertrieb:leads_liste")


def _hat_url(name):
    from django.urls import NoReverseMatch, reverse

    try:
        reverse(name, args=[1])
        return True
    except NoReverseMatch:
        return False


@require_POST
@recht_erforderlich(M, "anlegen")
def wiedervorlage_erledigt(request, pk):
    from .models import Aktivitaet

    Aktivitaet.objects.filter(pk=pk).update(erledigt=True)
    return redirect(request.POST.get("weiter") or "vertrieb:index")


# ------------------------------------------------------------------ Angebote

@recht_erforderlich(M)
def angebote(request):
    qs = Beleg.objects.filter(belegart=Beleg.Belegart.ANGEBOT).select_related("partner").order_by("-belegdatum", "-pk")
    if request.GET.get("q"):
        qs = qs.filter(Q(nummer__icontains=request.GET["q"]) | Q(partner__name__icontains=request.GET["q"]))
    uebernommen = set(Auftrag.objects.exclude(angebot=None).exclude(status="STORNIERT").values_list("angebot_id",
                                                                                                     flat=True))
    return render(request, "vertrieb/angebote.html", {"seite": _seite(request, qs), "uebernommen": uebernommen})


@recht_erforderlich(M, "anlegen")
def angebot_neu(request):
    form = AngebotKopfForm(request.POST or None, initial={"partner": request.GET.get("partner")})
    fs = PositionFormSet(request.POST or None, prefix="pos")
    if request.method == "POST" and form.is_valid() and fs.is_valid():
        pos = [(f.cleaned_data["artikel"], f.cleaned_data["menge"], f.cleaned_data.get("einzelpreis"))
               for f in fs if f.cleaned_data.get("artikel")]
        if not pos:
            messages.error(request, "Bitte mindestens eine Position erfassen.")
        else:
            try:
                b = S.angebot_anlegen(form.cleaned_data["partner"], pos, request.user, form.cleaned_data["betreff"],
                                      freigeben=form.cleaned_data["freigeben"])
            except FEHLER as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"Angebot {b.nummer or '(Entwurf)'} angelegt.")
                return redirect("belege:detail", b.pk)
    return render(request, "vertrieb/formular_positionen.html", {"titel": "Neues Angebot", "form": form, "formset": fs})


@require_POST
@recht_erforderlich(M, "anlegen")
def angebot_uebernehmen(request, pk):
    try:
        a = S.auftrag_aus_angebot(get_object_or_404(Beleg, pk=pk), request.user, request.POST.get("referenz", ""))
    except FEHLER as exc:
        messages.error(request, str(exc))
        return redirect("vertrieb:angebote")
    messages.success(request, f"Auftrag {a.nummer} aus Angebot angelegt.")
    return redirect("vertrieb:auftrag", a.pk)


# ------------------------------------------------------------------ Aufträge

@recht_erforderlich(M)
def auftraege(request):
    qs = Auftrag.objects.select_related("partner").prefetch_related("positionen")
    status = request.GET.get("status", "offen")
    if status == "offen":
        qs = qs.filter(status__in=Auftrag.OFFENE_STATI)
    elif status:
        qs = qs.filter(status=status)
    if request.GET.get("q"):
        q = request.GET["q"]
        qs = qs.filter(Q(nummer__icontains=q) | Q(partner__name__icontains=q) | Q(ihre_referenz__icontains=q))
    if request.GET.get("quelle"):
        qs = qs.filter(quelle=request.GET["quelle"])
    return render(request, "vertrieb/auftraege.html", {"seite": _seite(request, qs), "status": status,
                                                       "stati": Auftrag.Status.choices,
                                                       "quellen": Auftrag.Quelle.choices})


@recht_erforderlich(M, "anlegen")
def auftrag_neu(request):
    form = AuftragKopfForm(request.POST or None, initial={"partner": request.GET.get("partner")})
    fs = PositionFormSet(request.POST or None, prefix="pos")
    if request.method == "POST" and form.is_valid() and fs.is_valid():
        pos = []
        for f in fs:
            d = f.cleaned_data
            if d.get("artikel"):
                zeile = {"artikel": d["artikel"], "menge": d["menge"]}
                if d.get("einzelpreis") is not None:
                    zeile["einzelpreis"] = d["einzelpreis"]
                if d.get("liefertermin"):
                    zeile["liefertermin"] = d["liefertermin"]
                pos.append(zeile)
        felder = {k: v for k, v in form.cleaned_data.items() if k != "partner" and v not in (None, "")}
        try:
            a = S.auftrag_anlegen(form.cleaned_data["partner"], pos, request.user, **felder)
        except FEHLER as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Auftrag {a.nummer} angelegt.")
            return redirect("vertrieb:auftrag", a.pk)
    return render(request, "vertrieb/formular_positionen.html", {"titel": "Neuer Kundenauftrag", "form": form,
                                                                 "formset": fs})


@recht_erforderlich(M)
def auftrag_detail(request, pk):
    a = get_object_or_404(Auftrag.objects.select_related("partner", "angebot", "ab_beleg", "zustaendig"), pk=pk)
    return render(request, "vertrieb/auftrag.html", {
        "a": a, "verfuegbarkeit": S.verfuegbarkeit(a) if a.status in Auftrag.OFFENE_STATI else [],
        "lieferungen": a.lieferungen.select_related("beleg", "rechnung").order_by("pk"),
        "edi": a.edi.all(), "ist_edi": EdiPartner.objects.filter(partner=a.partner, aktiv=True).exists(),
        "storno_form": StornoForm()})


@require_POST
@recht_erforderlich(M)
def auftrag_aktion(request, pk, aktion):
    a = get_object_or_404(Auftrag, pk=pk)
    benoetigt = {"bestaetigen": (M, "freigeben"), "stornieren": (M, "freigeben"), "liefern": (M, "anlegen"),
                 "fa": ("produktion", "anlegen"), "ordrsp": ("edi", "anlegen")}.get(aktion)
    if not benoetigt:
        raise PermissionDenied("Unbekannte Aktion.")
    if not hat_recht(request.user, *benoetigt):
        raise PermissionDenied(f"Kein Recht „{benoetigt[1]}“ im Modul „{benoetigt[0]}“.")
    try:
        if aktion == "bestaetigen":
            a = S.auftrag_bestaetigen(a, request.user)
            messages.success(request, f"Auftragsbestätigung {a.ab_beleg.nummer} erstellt und festgeschrieben.")
        elif aktion == "stornieren":
            S.auftrag_stornieren(a, request.user, request.POST.get("grund", ""))
            messages.success(request, f"Auftrag {a.nummer} storniert.")
        elif aktion == "liefern":
            mengen = {}
            for p in a.positionen.all():
                wert = (request.POST.get(f"menge_{p.pk}") or "").replace(",", ".").strip()
                if wert:
                    mengen[p.pk] = wert
            lf = S.lieferung_anlegen(a, mengen, request.user, request.POST.get("lieferdatum") or None)
            messages.success(request, f"Lieferschein (Entwurf) für {lf.positionen.count()} Position(en) angelegt.")
            return redirect("vertrieb:lieferschein", lf.pk)
        elif aktion == "fa":
            fa = S.fa_aus_position(get_object_or_404(AuftragPosition, pk=request.POST.get("position"), auftrag=a),
                                   request.user)
            messages.success(request, f"Fertigungsauftrag {fa} angelegt.")
        elif aktion == "ordrsp":
            n = ED.ordrsp_erzeugen(a)
            messages.success(request, f"ORDRSP {n.kontrollnummer} erzeugt.")
    except FEHLER as exc:
        messages.error(request, str(exc))
    except Exception as exc:  # z. B. Fehler der Produktion (Stückliste fehlt)
        if exc.__class__.__name__.endswith("Fehler"):
            messages.error(request, str(exc))
        else:
            raise
    return redirect("vertrieb:auftrag", a.pk)


# ------------------------------------------------------------------ Lieferscheine

@recht_erforderlich(M)
def lieferscheine(request):
    qs = Lieferung.objects.select_related("auftrag__partner", "beleg", "rechnung")
    status = request.GET.get("status", "")
    if status == "unberechnet":
        qs = qs.filter(status__in=S.BERECHENBAR, rechnung=None)
    elif status:
        qs = qs.filter(status=status)
    if request.GET.get("q"):
        q = request.GET["q"]
        qs = qs.filter(Q(beleg__nummer__icontains=q) | Q(auftrag__nummer__icontains=q) |
                       Q(auftrag__partner__name__icontains=q))
    return render(request, "vertrieb/lieferscheine.html", {"seite": _seite(request, qs), "status": status,
                                                           "stati": Lieferung.Status.choices})


@recht_erforderlich(M)
def lieferschein_detail(request, pk):
    lf = get_object_or_404(Lieferung.objects.select_related("auftrag__partner", "beleg", "rechnung", "lagerort"), pk=pk)
    return render(request, "vertrieb/lieferschein.html", {
        "lf": lf, "positionen": lf.positionen.select_related("auftrag_position__artikel", "charge"),
        "darf_buchen": hat_recht(request.user, "versand", "anlegen") or hat_recht(request.user, M, "freigeben"),
        "ist_edi": EdiPartner.objects.filter(partner=lf.auftrag.partner, aktiv=True).exists(),
        "tourstopp": getattr(lf, "tourstopp", None)})


@require_POST
@recht_erforderlich(M)
def lieferschein_aktion(request, pk, aktion):
    lf = get_object_or_404(Lieferung, pk=pk)
    try:
        if aktion == "buchen":
            if not (hat_recht(request.user, "versand", "anlegen") or hat_recht(request.user, M, "freigeben")):
                raise PermissionDenied("Kein Recht zum Buchen des Warenausgangs.")
            lf = S.warenausgang_buchen(lf, request.user)
            messages.success(request, f"Warenausgang gebucht, Lieferschein {lf.beleg.nummer} festgeschrieben.")
        elif aktion == "stornieren":
            if not hat_recht(request.user, M, "freigeben"):
                raise PermissionDenied("Kein Recht zum Stornieren.")
            S.lieferung_stornieren(lf, request.user, request.POST.get("grund", ""))
            messages.success(request, "Lieferschein storniert, Bestand zurückgebucht.")
        elif aktion == "rechnung":
            if not hat_recht(request.user, M, "freigeben"):
                raise PermissionDenied("Kein Recht zur Rechnungserstellung.")
            re = S.rechnung_erstellen([lf], request.user)
            messages.success(request, f"Rechnung {re.nummer} erstellt und festgeschrieben.")
            return redirect("vertrieb:rechnung", re.pk)
        elif aktion == "desadv":
            if not hat_recht(request.user, "edi", "anlegen"):
                raise PermissionDenied("Kein Recht im Modul EDI.")
            n = ED.desadv_erzeugen(lf)
            messages.success(request, f"DESADV {n.kontrollnummer} erzeugt.")
    except FEHLER as exc:
        messages.error(request, str(exc))
    return redirect("vertrieb:lieferschein", lf.pk)


@require_POST
@recht_erforderlich(M, "freigeben")
def sammelrechnung(request):
    ids = request.POST.getlist("lieferung")
    try:
        re = S.rechnung_erstellen(Lieferung.objects.filter(pk__in=ids), request.user)
    except FEHLER as exc:
        messages.error(request, str(exc))
        return redirect("/vertrieb/lieferscheine/?status=unberechnet")
    messages.success(request, f"{'Sammelrechnung' if len(ids) > 1 else 'Rechnung'} {re.nummer} erstellt.")
    return redirect("vertrieb:rechnung", re.pk)


# ------------------------------------------------------------------ Rechnungen / E-Rechnung

@recht_erforderlich(M)
def rechnungen(request):
    qs = Beleg.objects.filter(belegart__in=["RECHNUNG", "GUTSCHRIFT"]).select_related("partner").order_by(
        "-belegdatum", "-pk")
    if request.GET.get("q"):
        qs = qs.filter(Q(nummer__icontains=request.GET["q"]) | Q(partner__name__icontains=request.GET["q"]))
    return render(request, "vertrieb/rechnungen.html", {"seite": _seite(request, qs)})


@recht_erforderlich(M)
def rechnung_detail(request, pk):
    from apps.fibu.models import OffenerPosten

    b = get_object_or_404(Beleg.objects.select_related("partner"), pk=pk, belegart__in=["RECHNUNG", "GUTSCHRIFT"])
    pruefung = None
    if b.nummer:
        try:
            xml = ER.cii_xml(b)
            ER.pruefen_cii(xml)
            ER.pruefen_ubl(ER.xrechnung_xml(b), gutschrift=b.belegart == "GUTSCHRIFT")
            pruefung = {"ok": not ER.summen_pruefen(xml), "fehler": ER.summen_pruefen(xml)}
        except Exception as exc:  # Validierungsfehler anzeigen statt 500
            pruefung = {"ok": False, "fehler": [str(exc)[:500]]}
    return render(request, "vertrieb/rechnung.html", {
        "b": b, "positionen": b.positionen.all(), "lieferungen": Lieferung.objects.filter(rechnung=b),
        "ops": OffenerPosten.objects.filter(belegnr=b.nummer) if b.nummer else [], "pruefung": pruefung,
        "pflicht": _pflichtangaben(b), "ist_edi": EdiPartner.objects.filter(partner=b.partner, aktiv=True).exists(),
        "gutschriften": Beleg.objects.filter(belegart="GUTSCHRIFT", ihre_referenz=b.nummer).exclude(nummer="")})


def _pflichtangaben(b):
    hinweise = []
    p = b.partner
    if not p.ust_id and not p.leitweg_id:
        hinweise.append("Kunde ohne USt-IdNr. (für Inlandsrechnungen nicht zwingend).")
    if not (p.email_rechnung or p.email):
        hinweise.append("Keine E-Mail-Adresse des Käufers (XRechnung BT-49 elektronische Adresse).")
    if p.leitweg_id:
        hinweise.append(f"Öffentlicher Auftraggeber: Leitweg-ID {p.leitweg_id} wird als Käuferreferenz (BT-10) "
                        "übermittelt.")
    return hinweise


@recht_erforderlich(M)
def rechnung_datei(request, pk, art):
    b = get_object_or_404(Beleg, pk=pk, belegart__in=["RECHNUNG", "GUTSCHRIFT"])
    try:
        if art == "zugferd":
            return _datei(ER.zugferd_rechnung(b), f"{b.nummer}_zugferd.pdf", "application/pdf")
        if art == "xrechnung":
            return _datei(ER.xrechnung_xml(b), f"{b.nummer}_xrechnung.xml", "application/xml")
        if art == "cii":
            return _datei(ER.cii_xml(b), f"{b.nummer}_factur-x.xml", "application/xml")
    except FEHLER as exc:
        messages.error(request, str(exc))
        return redirect("vertrieb:rechnung", b.pk)
    raise PermissionDenied("Unbekanntes Format.")


@require_POST
@recht_erforderlich(M, "freigeben")
def rechnung_aktion(request, pk, aktion):
    b = get_object_or_404(Beleg, pk=pk, belegart__in=["RECHNUNG", "GUTSCHRIFT"])
    try:
        if aktion == "gutschrift":
            gs = S.gutschrift_erstellen(b, request.user, grund=request.POST.get("grund", ""))
            messages.success(request, f"Gutschrift {gs.nummer} erstellt und festgeschrieben.")
            return redirect("vertrieb:rechnung", gs.pk)
        if aktion == "invoic":
            if not hat_recht(request.user, "edi", "anlegen"):
                raise PermissionDenied("Kein Recht im Modul EDI.")
            n = ED.invoic_erzeugen(b)
            messages.success(request, f"INVOIC {n.kontrollnummer} erzeugt.")
    except FEHLER as exc:
        messages.error(request, str(exc))
    return redirect("vertrieb:rechnung", b.pk)


# ------------------------------------------------------------------ EDI

@recht_erforderlich("edi")
def edi(request):
    form = EdiUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        if not hat_recht(request.user, "edi", "anlegen"):
            raise PermissionDenied("Kein Recht „anlegen“ im Modul „edi“.")
        if form.is_valid():
            datei = form.cleaned_data["datei"]
            n = ED.orders_importieren(datei.read().decode("utf-8", errors="replace"), datei.name, request.user,
                                      preise_uebernehmen=form.cleaned_data["preise"])
            if n.status == EdiNachricht.Status.VERARBEITET:
                messages.success(request, f"ORDERS {n.referenz} importiert → Auftrag {n.auftrag.nummer}.")
            else:
                messages.error(request, f"ORDERS nicht verarbeitet: {n.fehler}")
            return redirect("vertrieb:edi")
    qs = EdiNachricht.objects.select_related("edi_partner__partner", "auftrag")
    for feld in ("richtung", "typ", "status"):
        if request.GET.get(feld):
            qs = qs.filter(**{feld: request.GET[feld]})
    return render(request, "vertrieb/edi.html", {
        "form": form, "seite": _seite(request, qs), "partner": EdiPartner.objects.select_related("partner"),
        "typen": EdiNachricht.Typ.choices, "stati": EdiNachricht.Status.choices,
        "adapter": ED.Uebertragung.name})


@recht_erforderlich("edi")
def edi_nachricht(request, pk):
    n = get_object_or_404(EdiNachricht, pk=pk)
    if request.GET.get("download"):
        return _datei(n.inhalt.encode("utf-8"), n.dateiname or f"edi_{n.pk}.edi", "application/edifact")
    return render(request, "vertrieb/edi_nachricht.html", {"n": n})


@require_POST
@recht_erforderlich("edi", "anlegen")
def edi_uebertragen(request, pk):
    try:
        ED.als_uebertragen_markieren(get_object_or_404(EdiNachricht, pk=pk))
        messages.success(request, "Als übertragen markiert.")
    except ED.EdiFehler as exc:
        messages.error(request, str(exc))
    return redirect("vertrieb:edi_nachricht", pk)


# ------------------------------------------------------------------ Shops

@recht_erforderlich("shopapi")
def shops(request):
    form = ShopForm(request.POST or None)
    neues_token = None
    if request.method == "POST":
        if not hat_recht(request.user, "shopapi", "aendern"):
            raise PermissionDenied("Kein Recht „aendern“ im Modul „shopapi“.")
        if request.POST.get("token_fuer"):
            shop = get_object_or_404(Shop, pk=request.POST["token_fuer"])
            neues_token = (shop, token_erzeugen(shop))
        elif form.is_valid():
            shop = form.save()
            neues_token = (shop, token_erzeugen(shop))
            form = ShopForm()
    return render(request, "vertrieb/shops.html", {
        "shops": Shop.objects.all(), "form": form, "neues_token": neues_token,
        "bestellungen": ShopBestellung.objects.select_related("shop", "auftrag__partner")[:50],
        "basis": request.build_absolute_uri("/api/v1/shop/")})


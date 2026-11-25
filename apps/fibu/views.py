"""Web-Oberfläche Finanzbuchhaltung.

Rechte:
* fibu: lesen = ansehen/Auswertungen, anlegen = buchen (Dialog, Eingangsrechnungen, OP-Ausgleich),
  freigeben = festschreiben, aendern = Einstellungen (Steuerschlüssel, Kontierungsregeln)
* fibu_bank: lesen, anlegen = Kontoauszug importieren und Umsätze zuordnen/buchen
* fibu_mahnwesen: lesen, anlegen = Mahnvorschlag, freigeben = Mahnlauf ausführen
* fibu_abschluss: lesen, anlegen = Periode sperren/entsperren, Saldenvortrag, Geschäftsjahr anlegen
"""
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.permissions import hat_recht, recht_erforderlich
from apps.stammdaten.models import Konto

from . import auswertungen as A
from . import bank as BK
from . import mahnwesen as MW
from . import services as S
from .ereignisse import nachbuchen
from .forms import (AusgleichForm, BankUploadForm, BuchungKopfForm, BuchungZeilenFormSet, ERKopfForm,
                    ERPositionFormSet, MahnvorschlagForm, SachkontoForm, StornoForm, ZeitraumForm)
from .models import (NULL, AutomatikProtokoll, Bankimport, Bankkonto, Bankumsatz, Buchung, Eingangsrechnung,
                     FibuGesperrt, Geschaeftsjahr, Mahnlauf, Mahnung, OffenerPosten, Periode)

M, BANK, MAHN, ABSCHLUSS = "fibu", "fibu_bank", "fibu_mahnwesen", "fibu_abschluss"
FEHLER = (S.FibuFehler, FibuGesperrt)


def _fehler(request, exc):
    messages.error(request, str(exc))


# ------------------------------------------------------------------ Dashboard

def kennzahlen():
    heute = timezone.localdate()
    ops = OffenerPosten.objects.exclude(offen=0)
    d = ops.filter(art="D").aggregate(s=Sum("offen"), n=Count("pk"))
    k = ops.filter(art="K").aggregate(s=Sum("offen"), n=Count("pk"))
    d_ue = ops.filter(art="D", faellig_am__lt=heute, offen__gt=0).aggregate(s=Sum("offen"), n=Count("pk"))
    k_ue = ops.filter(art="K", faellig_am__lt=heute, offen__gt=0).aggregate(s=Sum("offen"), n=Count("pk"))
    banken = [(b, BK.bankstand(b)) for b in Bankkonto.objects.filter(aktiv=True).select_related("konto")]
    return {
        "debitoren": d["s"] or NULL, "debitoren_n": d["n"], "kreditoren": k["s"] or NULL, "kreditoren_n": k["n"],
        "deb_ueberfaellig": d_ue["s"] or NULL, "deb_ueberfaellig_n": d_ue["n"],
        "kred_ueberfaellig": k_ue["s"] or NULL, "kred_ueberfaellig_n": k_ue["n"],
        "banken": banken, "liquiditaet": sum((s for _, s in banken), NULL),
        "unzugeordnet": Bankumsatz.objects.filter(status=Bankumsatz.Status.OFFEN).count(),
        "ungeklaert": Bankumsatz.objects.filter(status=Bankumsatz.Status.VERRECHNUNG).count(),
        "nicht_festgeschrieben": Buchung.objects.filter(status=Buchung.Status.ERFASST).count(),
        "automatik_fehler": AutomatikProtokoll.objects.filter(erfolgreich=False).count(),
        "er_offen": Eingangsrechnung.objects.filter(status=Eingangsrechnung.Status.ERFASST).count(),
    }


def dashboard_hinweise(request):
    """Hinweise für die Startseite (über erp_modul['dashboard'])."""
    k = kennzahlen()
    h = []
    if k["automatik_fehler"]:
        h.append({"stufe": "danger", "text": f"{k['automatik_fehler']} automatische Buchung(en) fehlgeschlagen – "
                                             f"bitte prüfen und nachbuchen.", "url": reverse("fibu:index")})
    if k["deb_ueberfaellig_n"]:
        h.append({"stufe": "warning", "text": f"{k['deb_ueberfaellig_n']} überfällige Forderung(en): "
                                              f"{S.euro(k['deb_ueberfaellig'])}",
                  "url": reverse("fibu:op_liste") + "?art=D&ueberfaellig=1"})
    if k["unzugeordnet"]:
        h.append({"stufe": "info", "text": f"{k['unzugeordnet']} Bankumsätze nicht zugeordnet.",
                  "url": reverse("fibu:bank")})
    return h


@recht_erforderlich(M)
def index(request):
    k = kennzahlen()
    kacheln = [
        ("Buchungsjournal", "fibu:buchungen", "bi-journal-text", f"{k['nicht_festgeschrieben']} nicht festgeschrieben"),
        ("Kontenübersicht", "fibu:konten", "bi-list-columns", f"Kontenrahmen {S.rahmen()}"),
        ("Offene Posten", "fibu:op_liste", "bi-receipt", f"Debitoren {S.euro(k['debitoren'])} · "
                                                         f"Kreditoren {S.euro(k['kreditoren'])}"),
        ("Eingangsrechnungen", "fibu:er_liste", "bi-inbox", f"{k['er_offen']} ungebucht"),
        ("Bank", "fibu:bank", "bi-bank2", f"{k['unzugeordnet']} offen · {k['ungeklaert']} ungeklärt"),
        ("Mahnwesen", "fibu:mahnwesen", "bi-envelope-exclamation", f"{k['deb_ueberfaellig_n']} überfällige OPs"),
        ("Auswertungen", "fibu:auswertungen", "bi-graph-up", "SuSa, Bilanz, GuV, UStVA, DATEV"),
        ("Perioden & Abschluss", "fibu:perioden", "bi-calendar-check", "Periodensperre, Saldenvortrag"),
    ]
    heute = timezone.localdate()
    return render(request, "fibu/index.html", {
        "k": k, "kacheln": kacheln,
        "ueberfaellig": OffenerPosten.objects.filter(art="D", offen__gt=0, faellig_am__lt=heute)
        .select_related("partner").order_by("faellig_am")[:8],
        "faellig_kred": OffenerPosten.objects.filter(art="K", offen__gt=0).select_related("partner")
        .order_by("faellig_am")[:8],
        "fehler": AutomatikProtokoll.objects.filter(erfolgreich=False)[:10],
        "letzte": Buchung.objects.order_by("-pk")[:8]})


@require_POST
@recht_erforderlich(M, "anlegen")
def nachbuchen_view(request):
    ok, fehler = nachbuchen(request.user)
    messages.info(request, f"Nachbuchen: {ok} erfolgreich, {fehler} weiterhin fehlerhaft.")
    return redirect("fibu:index")


# ------------------------------------------------------------------ Konten

@recht_erforderlich(M)
def konten(request):
    r = S.rahmen()
    bis = _datum(request.GET.get("bis")) or timezone.localdate()
    beginn = A.gj_beginn(bis)
    qs = Konto.objects.filter(kontenrahmen=r)
    such = request.GET.get("q", "").strip()
    if such:
        qs = qs.filter(Q(nummer__startswith=such) | Q(bezeichnung__icontains=such))
    salden = A._salden(beginn, bis)
    nur_bebucht = request.GET.get("alle") != "1"
    zeilen = []
    for k in qs.order_by("nummer"):
        s, h = salden.get(k.pk, (NULL, NULL))
        if nur_bebucht and not (s or h) and not such:
            continue
        zeilen.append({"konto": k, "soll": s, "haben": h, "saldo": s - h})
    return render(request, "fibu/konten.html", {"zeilen": zeilen, "bis": bis, "beginn": beginn, "q": such,
                                                "alle": not nur_bebucht, "rahmen": r})


@recht_erforderlich(M)
def kontoblatt(request, pk):
    k = get_object_or_404(Konto, pk=pk)
    von, bis = _zeitraum(request)
    a = A.kontoblatt(k, von, bis)
    return _auswertung_antwort(request, a, f"kontoblatt_{k.nummer}", extra={"konto": k})


# ------------------------------------------------------------------ Buchungen

@recht_erforderlich(M)
def buchungen(request):
    qs = Buchung.objects.select_related("periode").annotate(betrag=Sum("zeilen__soll"))
    such = request.GET.get("q", "").strip()
    if such:
        qs = qs.filter(Q(nummer__icontains=such) | Q(belegnr__icontains=such) | Q(text__icontains=such))
    status = request.GET.get("status")
    if status in Buchung.Status.values:
        qs = qs.filter(status=status)
    quelle = request.GET.get("quelle")
    if quelle is not None and quelle != "alle":
        qs = qs.filter(quelle=quelle)
    seite = Paginator(qs.order_by("-belegdatum", "-pk"), 50).get_page(request.GET.get("seite"))
    return render(request, "fibu/buchungen.html", {"seite": seite, "q": such, "status": status,
                                                   "quellen": Buchung.objects.values_list("quelle", flat=True)
                                                   .distinct().order_by("quelle")})


@recht_erforderlich(M, "anlegen")
def buchung_neu(request):
    kopf = BuchungKopfForm(request.POST or None)
    zeilen = BuchungZeilenFormSet(request.POST or None, prefix="z")
    if request.method == "POST" and kopf.is_valid() and zeilen.is_valid():
        daten = [{"konto": f.cleaned_data["konto"], "soll": f.cleaned_data.get("soll"),
                  "haben": f.cleaned_data.get("haben"), "steuerschluessel": f.cleaned_data.get("steuerschluessel"),
                  "text": f.cleaned_data.get("text") or "", "partner": f.cleaned_data["konto"].partner,
                  "brutto": kopf.cleaned_data["brutto"]}
                 for f in zeilen if f.cleaned_data.get("konto")]
        try:
            b = S.buchen(kopf.cleaned_data["belegdatum"], kopf.cleaned_data["belegnr"], kopf.cleaned_data["text"],
                         daten, benutzer=request.user)
        except FEHLER as exc:
            _fehler(request, exc)
        else:
            messages.success(request, f"Buchung {b.nummer} erfasst.")
            return redirect("fibu:buchung", b.pk)
    return render(request, "fibu/buchung_form.html", {"kopf": kopf, "zeilen": zeilen})


@recht_erforderlich(M)
def buchung_detail(request, pk):
    b = get_object_or_404(Buchung.objects.select_related("periode", "storno_von"), pk=pk)
    storno = Buchung.objects.filter(storno_von=b).first()
    return render(request, "fibu/buchung_detail.html", {
        "b": b, "zeilen": b.zeilen.select_related("konto", "steuerschluessel", "partner"), "storno": storno,
        "ops": b.offene_posten.select_related("partner"), "storno_form": StornoForm(),
        "summe_soll": sum((z.soll for z in b.zeilen.all()), NULL)})


@require_POST
@recht_erforderlich(M, "freigeben")
def buchung_festschreiben(request, pk):
    b = get_object_or_404(Buchung, pk=pk)
    S.festschreiben(b, request.user)
    messages.success(request, f"Buchung {b.nummer} festgeschrieben (GoBD).")
    return redirect("fibu:buchung", pk)


@require_POST
@recht_erforderlich(M, "freigeben")
def buchungen_festschreiben(request):
    bis = _datum(request.POST.get("bis")) or timezone.localdate()
    n = S.festschreiben(Buchung.objects.filter(status=Buchung.Status.ERFASST, belegdatum__lte=bis), request.user)
    messages.success(request, f"{n} Buchung(en) bis {bis:%d.%m.%Y} festgeschrieben.")
    return redirect("fibu:buchungen")


@require_POST
@recht_erforderlich(M, "anlegen")
def buchung_storno(request, pk):
    b = get_object_or_404(Buchung, pk=pk)
    form = StornoForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Bitte einen Stornogrund angeben.")
        return redirect("fibu:buchung", pk)
    try:
        st = S.stornieren(b, request.user, form.cleaned_data.get("datum"), form.cleaned_data["grund"])
    except FEHLER as exc:
        _fehler(request, exc)
        return redirect("fibu:buchung", pk)
    messages.success(request, f"Stornobuchung {st.nummer} erzeugt.")
    return redirect("fibu:buchung", st.pk)


@recht_erforderlich(M)
def kette(request):
    anzahl, fehler = S.kette_pruefen()
    return render(request, "fibu/kette.html", {"anzahl": anzahl, "fehler": fehler})


# ------------------------------------------------------------------ Offene Posten

@recht_erforderlich(M)
def op_liste(request):
    art = request.GET.get("art", "D")
    qs = OffenerPosten.objects.filter(art=art).select_related("partner", "konto")
    if request.GET.get("erledigt") != "1":
        qs = qs.exclude(offen=0)
    if request.GET.get("ueberfaellig") == "1":
        qs = qs.filter(faellig_am__lt=timezone.localdate(), offen__gt=0)
    such = request.GET.get("q", "").strip()
    if such:
        qs = qs.filter(Q(belegnr__icontains=such) | Q(fremdbelegnr__icontains=such) | Q(partner__name__icontains=such))
    summe = qs.aggregate(s=Sum("offen"))["s"] or NULL
    return render(request, "fibu/op_liste.html", {"ops": qs.order_by("faellig_am")[:500], "art": art, "q": such,
                                                  "summe": summe, "heute": timezone.localdate()})


@recht_erforderlich(M)
def op_detail(request, pk):
    op = get_object_or_404(OffenerPosten.objects.select_related("partner", "konto", "buchung"), pk=pk)
    form = AusgleichForm(request.POST or None, initial={"betrag": op.offen - op.skonto_betrag(),
                                                        "gegenkonto": _bank_sachkonto()})
    if request.method == "POST":
        if not hat_recht(request.user, M, "anlegen"):
            raise Http404
        if form.is_valid():
            try:
                b = S.op_ausgleichen(op, form.cleaned_data["betrag"], form.cleaned_data["datum"],
                                     form.cleaned_data["gegenkonto"], request.user,
                                     skonto=form.cleaned_data["skonto"])
            except FEHLER as exc:
                _fehler(request, exc)
            else:
                messages.success(request, f"Ausgleich gebucht ({b.nummer}).")
                return redirect("fibu:op", pk)
    return render(request, "fibu/op_detail.html", {
        "op": op, "form": form, "ausgleiche": op.ausgleiche.select_related("buchung"),
        "mahnungen": op.mahnpositionen.select_related("mahnung__mahnlauf", "mahnung__stufe"),
        "skonto": op.skonto_betrag()})


@require_POST
@recht_erforderlich(MAHN, "anlegen")
def op_mahnsperre(request, pk):
    op = get_object_or_404(OffenerPosten, pk=pk)
    op.mahnsperre = not op.mahnsperre
    op.save(update_fields=["mahnsperre"])
    messages.success(request, f"Mahnsperre für {op.belegnr} {'gesetzt' if op.mahnsperre else 'aufgehoben'}.")
    return redirect("fibu:op", pk)


def _bank_sachkonto():
    b = Bankkonto.objects.filter(aktiv=True).select_related("konto").first()
    return b.konto if b else None


# ------------------------------------------------------------------ Eingangsrechnungen

@recht_erforderlich(M)
def er_liste(request):
    qs = Eingangsrechnung.objects.select_related("lieferant", "bestellung")
    status = request.GET.get("status")
    if status:
        qs = qs.filter(status=status)
    return render(request, "fibu/er_liste.html", {"rechnungen": qs[:300], "status": status})


@recht_erforderlich(M, "anlegen")
def er_neu(request):
    form = ERKopfForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        we = form.cleaned_data.get("wareneingang")
        try:
            if we:
                if we.lieferant_id != form.cleaned_data["lieferant"].pk:
                    raise S.FibuFehler("Der Wareneingang gehört zu einem anderen Lieferanten.")
                er = S.er_aus_wareneingang(we, form.cleaned_data["rechnungsnummer"],
                                           form.cleaned_data["rechnungsdatum"], request.user)
                for feld in ("eingangsdatum", "faellig_am", "zahlungsbedingung"):
                    if form.cleaned_data.get(feld):
                        setattr(er, feld, form.cleaned_data[feld])
                er.save()
            else:
                er = form.save(commit=False)
                er.nummer = S.naechste_nummer("FIBU_ER", timezone.localdate())
                er.erfasst_von = request.user
                er.zahlungsbedingung = er.zahlungsbedingung or er.lieferant.zahlungsbedingung
                er.save()
        except FEHLER as exc:
            _fehler(request, exc)
        else:
            messages.success(request, f"Eingangsrechnung {er.nummer} angelegt – Positionen prüfen.")
            return redirect("fibu:er", er.pk)
    return render(request, "fibu/er_form.html", {"form": form})


@recht_erforderlich(M)
def er_detail(request, pk):
    er = get_object_or_404(Eingangsrechnung.objects.select_related("lieferant", "bestellung", "buchung"), pk=pk)
    bearbeitbar = er.status == Eingangsrechnung.Status.ERFASST and hat_recht(request.user, M, "anlegen")
    formset = ERPositionFormSet(request.POST or None, instance=er, prefix="p") if bearbeitbar else None
    if request.method == "POST":
        if not bearbeitbar:
            raise Http404
        aktion = request.POST.get("aktion", "speichern")
        try:
            if aktion == "speichern":
                if formset.is_valid():
                    formset.save()
                    S.er_summen(er)
                    S.er_pruefen(er)
                    messages.success(request, "Positionen gespeichert und geprüft.")
                    return redirect("fibu:er", pk)
            elif aktion == "pruefen":
                hinweise = S.er_pruefen(er)
                messages.info(request, "Rechnungsprüfung ohne Abweichung." if not er.abweichung else
                              f"Rechnungsprüfung: {len(hinweise)} Hinweis(e) – bitte prüfen.")
                return redirect("fibu:er", pk)
            elif aktion == "freigeben":
                er.abweichung_akzeptiert = True
                er.save(update_fields=["abweichung_akzeptiert"])
                messages.warning(request, "Abweichung freigegeben – die Rechnung kann gebucht werden.")
                return redirect("fibu:er", pk)
            elif aktion == "buchen":
                b = S.er_buchen(er, request.user)
                messages.success(request, f"Eingangsrechnung gebucht ({b.nummer}).")
                return redirect("fibu:er", pk)
        except FEHLER as exc:
            _fehler(request, exc)
            return redirect("fibu:er", pk)
    return render(request, "fibu/er_detail.html", {
        "er": er, "formset": formset, "positionen": er.positionen.select_related("artikel", "steuerschluessel",
                                                                                 "bestellposition"),
        "ops": er.offene_posten.all()})


# ------------------------------------------------------------------ Bank

@recht_erforderlich(BANK)
def bank(request):
    form = BankUploadForm()
    status = request.GET.get("status", "OFFEN")
    umsaetze = Bankumsatz.objects.select_related("bankkonto").order_by("-buchungsdatum", "-pk")
    if status != "alle":
        umsaetze = umsaetze.filter(status=status)
    return render(request, "fibu/bank.html", {
        "form": form, "banken": [(b, BK.bankstand(b)) for b in Bankkonto.objects.select_related("konto")],
        "importe": Bankimport.objects.select_related("bankkonto")[:10], "umsaetze": umsaetze[:300],
        "status": status, "stati": Bankumsatz.Status.choices})


@require_POST
@recht_erforderlich(BANK, "anlegen")
def bank_import(request):
    form = BankUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Bitte eine Datei auswählen.")
        return redirect("fibu:bank")
    datei = form.cleaned_data["datei"]
    try:
        imp = BK.importieren(datei.read(), datei.name, request.user)
    except FEHLER as exc:
        _fehler(request, exc)
        return redirect("fibu:bank")
    text = f"{imp.nummer}: {imp.anzahl_neu} Umsätze importiert, {imp.anzahl_dubletten} Dubletten übersprungen."
    if form.cleaned_data["automatisch"]:
        try:
            z, g, o = BK.automatisch_zuordnen(imp.umsaetze.all(), request.user)
            text += f" Automatisch zugeordnet: {z}, Gebühren gebucht: {g}, offen: {o}."
        except FEHLER as exc:
            text += f" Automatische Zuordnung abgebrochen: {exc}"
    messages.success(request, text)
    return redirect("fibu:bank")


@require_POST
@recht_erforderlich(BANK, "anlegen")
def bank_auto(request):
    try:
        z, g, o = BK.automatisch_zuordnen(Bankumsatz.objects.filter(status=Bankumsatz.Status.OFFEN), request.user)
        messages.success(request, f"Automatisch zugeordnet: {z}, Gebühren gebucht: {g}, weiterhin offen: {o}.")
    except FEHLER as exc:
        _fehler(request, exc)
    return redirect("fibu:bank")


@recht_erforderlich(BANK)
def bank_umsatz(request, pk):
    u = get_object_or_404(Bankumsatz.objects.select_related("bankkonto__konto", "buchung", "zuordnung_buchung"),
                          pk=pk)
    sach_form = SachkontoForm(request.POST if request.POST.get("aktion") == "sachkonto" else None)
    if request.method == "POST":
        if not hat_recht(request.user, BANK, "anlegen"):
            raise Http404
        aktion = request.POST.get("aktion")
        try:
            if aktion == "zuordnen":
                ops = list(OffenerPosten.objects.filter(pk__in=request.POST.getlist("op")).order_by("faellig_am"))
                b = BK.zuordnen(u, ops, request.user, skonto=request.POST.get("skonto") == "1")
                messages.success(request, f"Zugeordnet und gebucht ({b.nummer}).")
                return redirect("fibu:bank")
            if aktion == "sepa_abgleich":
                from apps.fibu_sepa import services as SP

                lauf = SP.lauf_abgleichen(u, request.user)
                messages.success(request, f"SEPA-Lauf {lauf.nummer} abgeglichen ({lauf.get_status_display()}).")
                return redirect("fibu:bank")
            if aktion == "verrechnung":
                b = BK.auf_verrechnung(u, request.user)
                messages.warning(request, f"Auf Verrechnungskonto „ungeklärt“ gebucht ({b.nummer}).")
                return redirect("fibu:bank")
            if aktion == "gebuehr":
                b = BK.gebuehr_buchen(u, request.user)
                messages.success(request, f"Als Bankgebühr gebucht ({b.nummer}).")
                return redirect("fibu:bank")
            if aktion == "sachkonto" and sach_form.is_valid():
                b = BK.auf_konto_buchen(u, sach_form.cleaned_data["konto"], request.user,
                                        sach_form.cleaned_data.get("steuerschluessel"),
                                        sach_form.cleaned_data.get("text") or "")
                messages.success(request, f"Auf Sachkonto gebucht ({b.nummer}).")
                return redirect("fibu:bank")
        except FEHLER as exc:
            _fehler(request, exc)
    art = "D" if u.betrag > 0 else "K"
    vorschlaege = BK.vorschlaege(u, limit=8) if u.status != Bankumsatz.Status.GEBUCHT else []
    vorschlag_ids = {v["op"].pk for v in vorschlaege}
    weitere = (OffenerPosten.objects.filter(art=art, offen__gt=0).exclude(pk__in=vorschlag_ids)
               .select_related("partner").order_by("partner__name", "faellig_am")[:100])
    return render(request, "fibu/bank_umsatz.html", {"u": u, "vorschlaege": vorschlaege, "weitere": weitere,
                                                     "sach_form": sach_form, "gebuehr": BK.ist_gebuehr(u)})


# ------------------------------------------------------------------ Mahnwesen

@recht_erforderlich(MAHN)
def mahnwesen(request):
    form = MahnvorschlagForm(request.POST or None)
    if request.method == "POST":
        if not hat_recht(request.user, MAHN, "anlegen"):
            raise Http404
        if form.is_valid():
            try:
                lauf = MW.vorschlag(form.cleaned_data["stichtag"], request.user)
            except FEHLER as exc:
                _fehler(request, exc)
            else:
                messages.success(request, f"Mahnvorschlag {lauf.nummer} erstellt.")
                return redirect("fibu:mahnlauf", lauf.pk)
    heute = timezone.localdate()
    try:
        faellig = MW.faellige_posten(heute)
    except FEHLER:
        faellig = []
    return render(request, "fibu/mahnwesen.html", {
        "form": form, "laeufe": Mahnlauf.objects.annotate(n=Count("mahnungen"))[:30], "faellig": faellig,
        "gesperrt": OffenerPosten.objects.filter(art="D", offen__gt=0).filter(
            Q(mahnsperre=True) | Q(partner__mahnsperre=True)).select_related("partner")})


@recht_erforderlich(MAHN)
def mahnlauf(request, pk):
    lauf = get_object_or_404(Mahnlauf, pk=pk)
    if request.method == "POST":
        if not hat_recht(request.user, MAHN, "freigeben"):
            raise Http404
        aktion = request.POST.get("aktion")
        try:
            if aktion == "ausfuehren":
                MW.ausfuehren(lauf, request.user)
                messages.success(request, f"Mahnlauf {lauf.nummer} ausgeführt – Mahnstufen fortgeschrieben.")
            elif aktion == "loeschen" and lauf.status == Mahnlauf.Status.VORSCHLAG:
                lauf.delete()
                messages.info(request, "Mahnvorschlag verworfen.")
                return redirect("fibu:mahnwesen")
            elif aktion == "entfernen" and lauf.status == Mahnlauf.Status.VORSCHLAG:
                Mahnung.objects.filter(mahnlauf=lauf, pk=request.POST.get("mahnung")).delete()
        except FEHLER as exc:
            _fehler(request, exc)
        return redirect("fibu:mahnlauf", pk)
    mahnungen = lauf.mahnungen.select_related("partner", "stufe").prefetch_related("positionen__op")
    return render(request, "fibu/mahnlauf.html", {"lauf": lauf, "mahnungen": mahnungen})


@recht_erforderlich(MAHN)
def mahnung_pdf(request, pk):
    m = get_object_or_404(Mahnung.objects.select_related("mahnlauf", "partner", "stufe"), pk=pk)
    antwort = HttpResponse(MW.mahnung_pdf(m), content_type="application/pdf")
    antwort["Content-Disposition"] = f'inline; filename="Mahnung_{m.mahnlauf.nummer}_{m.partner.nummer}.pdf"'
    return antwort


# ------------------------------------------------------------------ Perioden / Abschluss

@recht_erforderlich(ABSCHLUSS)
def perioden(request):
    if request.method == "POST":
        if not hat_recht(request.user, ABSCHLUSS, "anlegen"):
            raise Http404
        aktion = request.POST.get("aktion")
        try:
            if aktion == "gj_anlegen":
                gj = S.geschaeftsjahr_anlegen(int(request.POST.get("jahr")))
                messages.success(request, f"{gj} mit 12 Perioden angelegt.")
            elif aktion == "sperren":
                p = get_object_or_404(Periode, pk=request.POST.get("periode"))
                S.periode_sperren(p, request.user)
                messages.success(request, f"Periode {p} gesperrt (offene Buchungen festgeschrieben).")
            elif aktion == "entsperren":
                p = get_object_or_404(Periode, pk=request.POST.get("periode"))
                S.periode_entsperren(p)
                messages.warning(request, f"Periode {p} wieder geöffnet.")
            elif aktion == "saldenvortrag":
                b = S.saldenvortrag(int(request.POST.get("jahr")), request.user)
                messages.success(request, f"Saldenvortrag gebucht ({b.nummer}).")
            elif aktion == "abschliessen":
                S.gj_abschliessen(int(request.POST.get("jahr")), request.user)
                messages.success(request, "Geschäftsjahr abgeschlossen.")
        except (ValueError, TypeError):
            messages.error(request, "Ungültige Eingabe.")
        except FEHLER as exc:
            _fehler(request, exc)
        return redirect("fibu:perioden")
    jahre = Geschaeftsjahr.objects.prefetch_related("perioden")
    zaehler = {e["periode"]: e for e in Buchung.objects.values("periode").annotate(
        n=Count("pk"), offen=Count("pk", filter=Q(status=Buchung.Status.ERFASST)))}
    return render(request, "fibu/perioden.html", {"jahre": jahre, "zaehler": zaehler,
                                                  "naechstes": (jahre.first().jahr + 1) if jahre else
                                                  timezone.localdate().year})


# ------------------------------------------------------------------ Auswertungen

def _datum(text):
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def _zeitraum(request):
    heute = timezone.localdate()
    von = _datum(request.GET.get("von")) or A.gj_beginn(heute)
    bis = _datum(request.GET.get("bis")) or heute
    return von, bis


def _anzeige(a):
    """Werte für HTML/PDF formatieren (Dezimal → deutsches Format)."""
    from apps.core.templatetags.erp import _deutsch

    zeilen = []
    for z in a["zeilen"]:
        werte = []
        for w in z["werte"]:
            if isinstance(w, Decimal):
                werte.append(_deutsch(w, 2))
            elif isinstance(w, int) and not isinstance(w, bool):
                werte.append(_deutsch(w, 0))
            else:
                werte.append("" if w is None else w)
        zeilen.append({"werte": list(zip(werte, [s[1] for s in a["spalten"]])), "stil": z["stil"]})
    return {**a, "anzeige": zeilen}


def _auswertung_antwort(request, a, dateiname, extra=None):
    fmt = request.GET.get("format")
    if fmt == "csv":
        antwort = HttpResponse(A.als_csv(a), content_type="text/csv; charset=utf-8")
        antwort["Content-Disposition"] = f'attachment; filename="{dateiname}.csv"'
        return antwort
    a = _anzeige(a)
    if fmt == "pdf":
        antwort = HttpResponse(A.als_pdf(a), content_type="application/pdf")
        antwort["Content-Disposition"] = f'inline; filename="{dateiname}.pdf"'
        return antwort
    von, bis = _zeitraum(request)
    return render(request, "fibu/auswertung.html", {"a": a, "von": von, "bis": bis, "params": request.GET.urlencode(),
                                                    **(extra or {})})


AUSWERTUNGEN = {
    "susa": ("Summen- und Saldenliste", lambda r, von, bis: A.susa(von, bis)),
    "journal": ("Buchungsjournal", lambda r, von, bis: A.journal(von, bis)),
    "op_debitoren": ("OP-Liste Debitoren", lambda r, von, bis: A.op_liste("D", bis)),
    "op_kreditoren": ("OP-Liste Kreditoren", lambda r, von, bis: A.op_liste("K", bis)),
    "bilanz": ("Bilanz", lambda r, von, bis: A.bilanz(bis)),
    "guv": ("GuV", lambda r, von, bis: A.guv(von, bis)),
    "ustva": ("UStVA", lambda r, von, bis: A.ustva(von, bis)),
}


@recht_erforderlich(M)
def auswertungen_index(request):
    heute = timezone.localdate()
    monat_beginn = heute.replace(day=1)
    vormonat_ende = monat_beginn.fromordinal(monat_beginn.toordinal() - 1)
    return render(request, "fibu/auswertungen.html", {
        "liste": [(k, v[0]) for k, v in AUSWERTUNGEN.items()], "von": A.gj_beginn(heute), "bis": heute,
        "vm_von": vormonat_ende.replace(day=1), "vm_bis": vormonat_ende, "form": ZeitraumForm(
            initial={"von": A.gj_beginn(heute), "bis": heute})})


@recht_erforderlich(M)
def auswertung(request, art):
    if art not in AUSWERTUNGEN:
        raise Http404
    von, bis = _zeitraum(request)
    if von > bis:
        messages.error(request, "Das Von-Datum liegt nach dem Bis-Datum.")
        return redirect("fibu:auswertungen")
    a = AUSWERTUNGEN[art][1](request, von, bis)
    return _auswertung_antwort(request, a, f"{art}_{von:%Y%m%d}_{bis:%Y%m%d}", extra={"art": art})


@recht_erforderlich(M)
def datev(request):
    von, bis = _zeitraum(request)
    daten, anzahl = A.datev_csv(von, bis)
    antwort = HttpResponse(daten, content_type="text/csv; charset=windows-1252")
    antwort["Content-Disposition"] = f'attachment; filename="EXTF_Buchungsstapel_{von:%Y%m%d}_{bis:%Y%m%d}.csv"'
    antwort["X-Anzahl-Buchungszeilen"] = str(anzahl)
    return antwort

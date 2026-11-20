from decimal import Decimal, InvalidOperation

from django.shortcuts import get_object_or_404, render

from apps.core.permissions import recht_erforderlich

from .models import Artikel, Arbeitsplan, Arbeitsplatz, Geschaeftspartner, Konto, Stueckliste, StuecklistenFehler


@recht_erforderlich("stammdaten")
def index(request):
    bereiche = [
        ("Artikel", "stammdaten:artikel_liste", Artikel.objects.count(), "bi-box-seam"),
        ("Geschäftspartner", "stammdaten:partner_liste", Geschaeftspartner.objects.count(), "bi-people"),
        ("Stücklisten", "stammdaten:stueckliste_liste", Stueckliste.objects.count(), "bi-diagram-3"),
        ("Arbeitspläne", "stammdaten:arbeitsplan_liste", Arbeitsplan.objects.count(), "bi-tools"),
        ("Arbeitsplätze", "stammdaten:arbeitsplatz_liste", Arbeitsplatz.objects.count(), "bi-hammer"),
        ("Kontenrahmen", "stammdaten:konto_liste", Konto.objects.count(), "bi-journal-text"),
        ("Steuersätze", "stammdaten:steuersatz_liste", None, "bi-percent"),
        ("Einheiten", "stammdaten:einheit_liste", None, "bi-rulers"),
        ("Zahlungsbedingungen", "stammdaten:zahlungsbedingung_liste", None, "bi-calendar-check"),
    ]
    return render(request, "stammdaten/index.html", {"bereiche": bereiche})


@recht_erforderlich("stammdaten")
def stueckliste_aufloesung(request, pk):
    stueckliste = get_object_or_404(Stueckliste.objects.select_related("artikel"), pk=pk)
    try:
        menge = Decimal(request.GET.get("menge", "1").replace(",", "."))
    except InvalidOperation:
        menge = Decimal(1)
    fehler, zeilen, bedarf = None, [], {}
    try:
        zeilen = stueckliste.aufloesen(menge)
        bedarf = stueckliste.rohstoffbedarf(menge)
    except StuecklistenFehler as exc:
        fehler = str(exc)
    return render(request, "stammdaten/stueckliste_aufloesung.html",
                  {"stueckliste": stueckliste, "menge": menge, "zeilen": zeilen, "bedarf": bedarf, "fehler": fehler})

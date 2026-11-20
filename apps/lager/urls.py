from django.urls import path

from apps.core.generic import crud_urls

from . import views
from .models import ArtikelLager, Charge, Lagerort, Lagerplatz

app_name = "lager"
M = "lager"

urlpatterns = [
    path("", views.uebersicht, name="uebersicht"),
    path("artikel/<int:pk>/", views.artikel, name="artikel"),
    path("journal/", views.journal, name="journal"),
    path("buchen/<str:art>/", views.buchung, name="buchung"),
    path("umbuchen/", views.umbuchung, name="umbuchung"),
    path("inventur/", views.inventur_liste, name="inventur_liste"),
    path("inventur/neu/", views.inventur_neu, name="inventur_neu"),
    path("inventur/<int:pk>/", views.inventur, name="inventur"),
]

urlpatterns += crud_urls(
    "lagerort", Lagerort, M, felder=["code", "bezeichnung", "ist_wareneingang", "aktiv", "beschreibung"],
    spalten=[("Kürzel", "code"), ("Bezeichnung", "bezeichnung"), ("WE-Standard", "ist_wareneingang"),
             ("Aktiv", "aktiv")],
    inlines=[("Lagerplätze", Lagerplatz, ["code", "bezeichnung", "aktiv"])],
)
urlpatterns += crud_urls(
    "dispo", ArtikelLager, M,
    felder=["artikel", "mindestbestand", "meldebestand", "bestellmenge", "wiederbeschaffungszeit_tage",
            "bevorzugter_lieferant", "standard_lagerort", "qs_pflicht"],
    spalten=[("Artikel", "artikel"), ("Mindestbestand", "mindestbestand"), ("Meldebestand", "meldebestand"),
             ("Bestellmenge", "bestellmenge"), ("Lieferant", "bevorzugter_lieferant"), ("QS", "qs_pflicht"),
             ("Ø-Preis", "durchschnittspreis")],
    suchfelder=["artikel__artikelnummer", "artikel__bezeichnung"],
    queryset=ArtikelLager.objects.select_related("artikel", "bevorzugter_lieferant"),
    titel="Dispositionsdaten", titel_plural="Dispositionsdaten",
)
urlpatterns += crud_urls(
    "charge", Charge, M,
    felder=["artikel", "chargennummer", "lieferant", "lieferantencharge", "herstelldatum", "mhd", "bemerkung"],
    spalten=[("Charge", "chargennummer"), ("Artikel", "artikel.artikelnummer"), ("Lieferant", "lieferant"),
             ("Lieferantencharge", "lieferantencharge"), ("MHD", "mhd")],
    suchfelder=["chargennummer", "lieferantencharge", "artikel__artikelnummer"],
    queryset=Charge.objects.select_related("artikel", "lieferant"),
)

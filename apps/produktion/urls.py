from django.urls import path

from apps.core.generic import crud_urls
from apps.stammdaten.models import Mitarbeiter

from . import views

app_name = "produktion"

urlpatterns = [
    path("", views.index, name="index"),
    path("auftraege/", views.liste, name="liste"),
    path("auftraege/neu/", views.neu, name="neu"),
    path("auftraege/<int:pk>/", views.detail, name="detail"),
    path("auftraege/<int:pk>/loeschen/", views.loeschen, name="loeschen"),
    path("auftraege/<int:pk>/laufkarte/", views.laufkarte, name="laufkarte"),
    path("auftraege/<int:pk>/entnahme/", views.entnahme, name="entnahme"),
    path("auftraege/<int:pk>/fertigmelden/", views.fertigmelden, name="fertigmelden"),
    path("auftraege/<int:pk>/nachkalkulation/", views.nachkalkulation, name="nachkalkulation"),
    path("auftraege/<int:pk>/<str:was>/", views.aktion, name="aktion"),
    path("rueckmeldungen/", views.rueckmeldungen, name="rueckmeldungen"),
    path("terminal/", views.terminal, name="terminal"),
    path("stempelungen/", views.stempelungen, name="stempelungen"),
    path("stempelungen/nachtrag/", views.stempel_nachtrag, name="stempel_nachtrag"),
    path("stempelungen/<int:pk>/korrektur/", views.stempel_korrektur, name="stempel_korrektur"),
    path("akkord/", views.akkord, name="akkord"),
    path("akkord/pdf/", views.akkord_pdf_view, name="akkord_pdf"),
    path("akkord/csv/", views.akkord_csv_view, name="akkord_csv"),
]

# Personal- und Lohndaten: nur mit Recht „akkord“ (Lohnauswertung)
urlpatterns += crud_urls(
    "mitarbeiter", Mitarbeiter, "akkord",
    felder=["personalnummer", "ausweisnummer", "vorname", "nachname", "lohngruppe", "stundenlohn",
            "akkordrichtsatz", "benutzer", "aktiv"],
    spalten=[("Personalnr.", "personalnummer"), ("Name", "name"), ("Ausweis", "ausweisnummer"),
             ("Lohngruppe", "lohngruppe"), ("Grundlohn €/h", "stundenlohn"), ("Richtsatz €/h", "akkordrichtsatz"),
             ("Aktiv", "aktiv")],
    suchfelder=["personalnummer", "vorname", "nachname", "ausweisnummer"],
    titel="Mitarbeiter", titel_plural="Mitarbeiter",
)

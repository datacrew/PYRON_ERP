from django.urls import path

from apps.core.generic import crud_urls

from . import views
from .models import Fahrzeug, PlzKoordinate, Region

app_name = "versand"

urlpatterns = [
    path("", views.index, name="index"),
    path("touren/", views.touren, name="touren"),
    path("touren/bilden/", views.planen, name="planen"),
    path("touren/<int:pk>/", views.tour_detail, name="tour"),
    path("touren/<int:pk>/pdf/", views.tour_pdf, name="tour_pdf"),
    path("touren/<int:pk>/<slug:aktion>/", views.tour_aktion, name="tour_aktion"),
    path("lvs/", views.lvs, name="lvs"),
    path("lvs/protokoll/<int:pk>/", views.lvs_protokoll, name="lvs_protokoll"),
]

urlpatterns += crud_urls(
    "regionen", Region, "versand",
    ["code", "name", "plz_bereiche", "liefertage", "land", "sortierung", "standard_fahrzeug", "aktiv"],
    [("Code", "code"), ("Name", "name"), ("PLZ-Bereiche", "plz_bereiche"), ("Liefertage", "liefertage_text"),
     ("Sortierung", "sortierung"), ("Fahrzeug", "standard_fahrzeug"), ("Aktiv", "aktiv")],
    suchfelder=["code", "name", "plz_bereiche"], titel="Region", titel_plural="Regionen / PLZ-Gebiete")
urlpatterns += crud_urls(
    "fahrzeuge", Fahrzeug, "versand", ["kennzeichen", "bezeichnung", "max_kg", "max_paletten", "fahrer", "aktiv"],
    [("Kennzeichen", "kennzeichen"), ("Bezeichnung", "bezeichnung"), ("Max. kg", "max_kg"),
     ("Paletten", "max_paletten"), ("Fahrer", "fahrer"), ("Aktiv", "aktiv")],
    suchfelder=["kennzeichen", "bezeichnung"], titel="Fahrzeug", titel_plural="Fahrzeuge")
urlpatterns += crud_urls(
    "plzkoordinaten", PlzKoordinate, "versand", ["land", "plz", "ort", "breite", "laenge"],
    [("PLZ", "plz"), ("Ort", "ort"), ("Land", "land"), ("Breite", "breite"), ("Länge", "laenge")],
    suchfelder=["plz", "ort"], titel="PLZ-Koordinate", titel_plural="PLZ-Koordinaten")

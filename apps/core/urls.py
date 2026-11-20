from django.urls import path

from . import views
from .generic import crud_urls
from .models import Nummernkreis

app_name = "core"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("suche/", views.suche, name="suche"),
    path("modul/<slug:code>/", views.platzhalter, name="platzhalter"),
    path("verwaltung/", views.verwaltung, name="verwaltung"),
    path("verwaltung/benutzer/", views.benutzer_liste, name="benutzer_liste"),
    path("verwaltung/benutzer/neu/", views.benutzer_bearbeiten, name="benutzer_neu"),
    path("verwaltung/benutzer/<int:pk>/", views.benutzer_bearbeiten, name="benutzer_bearbeiten"),
    path("verwaltung/rollen/", views.rollen_liste, name="rollen_liste"),
    path("verwaltung/rollen/neu/", views.rolle_bearbeiten, name="rolle_neu"),
    path("verwaltung/rollen/<int:pk>/", views.rolle_bearbeiten, name="rolle_bearbeiten"),
    path("verwaltung/rollen/<int:pk>/loeschen/", views.rolle_loeschen, name="rolle_loeschen"),
    path("verwaltung/module/", views.module_verwalten, name="module"),
    path("verwaltung/firma/", views.mandant_bearbeiten, name="mandant"),
    path("verwaltung/audit/", views.audit_log, name="audit"),
]

# Nummernkreise: der Zählerstand ist bewusst nicht editierbar (Lückenlosigkeit)
urlpatterns += [
    path(f"verwaltung/{p.pattern._route}", p.callback, name=p.name)
    for p in crud_urls(
        "nummernkreis", Nummernkreis, "verwaltung",
        felder=["code", "bezeichnung", "praefix", "jahresbezogen", "stellen"],
        spalten=[("Code", "code"), ("Bezeichnung", "bezeichnung"), ("Präfix", "praefix"),
                 ("Jahresbezogen", "jahresbezogen"), ("Stellen", "stellen"), ("Nächste Nummer", "naechste_nummer"),
                 ("Jahr", "aktuelles_jahr")],
        suchfelder=["code", "bezeichnung"], titel="Nummernkreis", titel_plural="Nummernkreise",
    )
]

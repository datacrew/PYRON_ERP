from django import forms
from django.urls import path

from apps.core.generic import crud_urls

from . import views
from .models import Beleg, BelegVorlage

app_name = "belege"

urlpatterns = [
    path("", views.liste, name="liste"),
    path("neu/", views.neu, name="neu"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/bearbeiten/", views.bearbeiten, name="bearbeiten"),
    path("<int:pk>/loeschen/", views.loeschen, name="loeschen"),
    path("<int:pk>/pdf/", views.pdf, name="pdf"),
    path("<int:pk>/<str:was>/", views.aktion, name="aktion"),
    path("verwaltung/kette/", views.kette, name="kette"),
]

# Belegvorlagen werden in der Verwaltung gepflegt (Recht „verwaltung“)
urlpatterns += crud_urls(
    "vorlage", BelegVorlage, "verwaltung",
    felder=["name", "belegart", "aktiv", "html"],
    widgets={
        "html": forms.Textarea(attrs={"rows": 28, "style": "font-family:monospace;font-size:12px"}),
        "belegart": forms.Select(choices=[("", "– alle Belegarten –"), *Beleg.Belegart.choices]),
    },
    spalten=[("Name", "name"), ("Belegart", "get_belegart_anzeige"), ("Aktiv", "aktiv"),
             ("Geändert", "geaendert_am")],
    titel="Belegvorlage", titel_plural="Belegvorlagen",
)

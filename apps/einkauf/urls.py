from django.urls import path

from . import views

app_name = "einkauf"

urlpatterns = [
    path("", views.index, name="index"),
    path("bestellungen/", views.liste, name="liste"),
    path("bestellungen/neu/", views.neu, name="neu"),
    path("bestellungen/<int:pk>/", views.detail, name="detail"),
    path("bestellungen/<int:pk>/bearbeiten/", views.bearbeiten, name="bearbeiten"),
    path("bestellungen/<int:pk>/loeschen/", views.loeschen, name="loeschen"),
    path("bestellungen/<int:pk>/pdf/", views.pdf, name="pdf"),
    path("bestellungen/<int:pk>/<str:was>/", views.aktion, name="aktion"),
    path("termine/", views.termine, name="termine"),
    path("bestellvorschlag/", views.vorschlag, name="vorschlag"),
    path("anforderungen/", views.banf_liste, name="banf_liste"),
]

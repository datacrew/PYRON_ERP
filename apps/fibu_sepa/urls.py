from django.urls import path

from . import views

app_name = "fibu_sepa"

urlpatterns = [
    path("", views.laeufe, name="laeufe"),
    path("vorschlag/<str:art>/", views.vorschlag, name="vorschlag"),
    path("lauf/<int:pk>/", views.lauf_detail, name="lauf"),
    path("lauf/<int:pk>/posten/<int:posten_pk>/entfernen/", views.posten_entfernen, name="posten_entfernen"),
    path("lauf/<int:pk>/freigeben/", views.lauf_freigeben, name="lauf_freigeben"),
    path("lauf/<int:pk>/exportieren/", views.lauf_exportieren, name="lauf_exportieren"),
    path("lauf/<int:pk>/stornieren/", views.lauf_stornieren, name="lauf_stornieren"),
    path("lauf/<int:pk>/verwerfen/", views.lauf_verwerfen, name="lauf_verwerfen"),
    path("lauf/<int:pk>/xml/", views.lauf_xml, name="lauf_xml"),
    path("mandate/", views.mandate, name="mandate"),
    path("mandate/neu/", views.mandat_form, name="mandat_neu"),
    path("mandate/<int:pk>/", views.mandat_detail, name="mandat"),
    path("mandate/<int:pk>/bearbeiten/", views.mandat_form, name="mandat_bearbeiten"),
    path("mandate/<int:pk>/<str:aktion>/", views.mandat_status, name="mandat_status"),
]

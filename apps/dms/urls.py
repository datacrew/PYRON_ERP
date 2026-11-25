from django.urls import path

from . import views

app_name = "dms"

urlpatterns = [
    path("", views.index, name="index"),
    path("dokumente/", views.liste, name="liste"),
    path("dokumente/hochladen/", views.hochladen, name="hochladen"),
    path("dokumente/<int:pk>/", views.detail, name="detail"),
    path("dokumente/<int:pk>/datei/", views.datei, name="datei"),
    path("dokumente/<int:pk>/datei/<int:version_pk>/", views.datei, name="datei_version"),
    path("dokumente/<int:pk>/version/", views.version_neu, name="version_neu"),
    path("dokumente/<int:pk>/verarbeiten/", views.verarbeiten, name="verarbeiten"),
    path("dokumente/<int:pk>/archivieren/", views.archivieren, name="archivieren"),
    path("dokumente/<int:pk>/loeschen/", views.loeschen, name="loeschen"),
    path("stapel/", views.stapel_liste, name="stapel_liste"),
    path("stapel/<int:pk>/", views.stapel_detail, name="stapel"),
    path("stapel/<int:pk>/status/", views.stapel_status, name="stapel_status"),
    path("stapel/<int:pk>/verarbeiten/", views.stapel_verarbeiten, name="stapel_verarbeiten"),
    path("dokumente/<int:pk>/trennen/", views.trennen, name="trennen"),
    path("trennblatt.pdf", views.trennblatt, name="trennblatt"),
    path("dokumente/<int:pk>/pruefen/", views.pruefen, name="pruefen"),
    path("lernregeln/", views.regeln, name="regeln"),
    path("fristen/", views.fristen, name="fristen"),
    path("schlagwoerter/", views.tags, name="tags"),
]

from django.urls import path

from . import views

app_name = "reporting"

urlpatterns = [
    path("", views.index, name="index"),
    path("cockpit/", views.cockpit, name="cockpit"),
    path("bericht/<str:code>/", views.bericht, name="bericht"),
    path("variante/<int:pk>/loeschen/", views.variante_loeschen, name="variante_loeschen"),
]

from django.urls import path

from . import views

app_name = "wareneingang"

urlpatterns = [
    path("", views.index, name="index"),
    path("liste/", views.liste, name="liste"),
    path("buchen/<int:bestellung_pk>/", views.buchen, name="buchen"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/pdf/", views.pdf, name="pdf"),
    path("position/<int:pk>/qs/<str:was>/", views.qs, name="qs"),
]

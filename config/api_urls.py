"""REST-API v1 – alle Endpunkte mit Rechteprüfung pro Modul."""
from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter

from apps.belege.api import BelegViewSet
from apps.stammdaten.api import (
    ArtikelViewSet,
    GeschaeftspartnerViewSet,
    KontoViewSet,
    SteuersatzViewSet,
    StuecklisteViewSet,
)

router = DefaultRouter()
router.register("artikel", ArtikelViewSet, basename="artikel")
router.register("partner", GeschaeftspartnerViewSet, basename="partner")
router.register("stuecklisten", StuecklisteViewSet, basename="stueckliste")
router.register("konten", KontoViewSet, basename="konto")
router.register("steuersaetze", SteuersatzViewSet, basename="steuersatz")
router.register("belege", BelegViewSet, basename="beleg")

urlpatterns = [
    path("token/", obtain_auth_token, name="api-token"),
    path("", include(router.urls)),
]

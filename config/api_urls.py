"""REST-API v1 – alle Endpunkte mit Rechteprüfung pro Modul."""
from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter

from apps.belege.api import BelegViewSet
from apps.einkauf.api import BestellungViewSet
from apps.lager.api import BestandViewSet, ChargeViewSet, LagerbewegungViewSet, LagerortViewSet
from apps.stammdaten.api import (
    ArtikelViewSet,
    GeschaeftspartnerViewSet,
    KontoViewSet,
    SteuersatzViewSet,
    StuecklisteViewSet,
)
from apps.wareneingang.api import WareneingangViewSet

router = DefaultRouter()
router.register("artikel", ArtikelViewSet, basename="artikel")
router.register("partner", GeschaeftspartnerViewSet, basename="partner")
router.register("stuecklisten", StuecklisteViewSet, basename="stueckliste")
router.register("konten", KontoViewSet, basename="konto")
router.register("steuersaetze", SteuersatzViewSet, basename="steuersatz")
router.register("belege", BelegViewSet, basename="beleg")
router.register("bestellungen", BestellungViewSet, basename="bestellung")
router.register("wareneingaenge", WareneingangViewSet, basename="wareneingang")
router.register("bestaende", BestandViewSet, basename="bestand")
router.register("lagerbewegungen", LagerbewegungViewSet, basename="lagerbewegung")
router.register("lagerorte", LagerortViewSet, basename="lagerort")
router.register("chargen", ChargeViewSet, basename="charge")

urlpatterns = [
    path("token/", obtain_auth_token, name="api-token"),
    path("", include(router.urls)),
]

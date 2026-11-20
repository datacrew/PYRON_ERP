"""REST-API v1 – alle Endpunkte mit Rechteprüfung pro Modul."""
from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter

from apps.belege.api import BelegViewSet
from apps.einkauf.api import BestellungViewSet
from apps.fibu.api import BankumsatzViewSet, BuchungViewSet, OPViewSet, SachkontoViewSet
from apps.produktion.api import FertigungsauftragViewSet, RueckmeldungViewSet, StempelungViewSet
from apps.lager.api import BestandViewSet, ChargeViewSet, LagerbewegungViewSet, LagerortViewSet
from apps.stammdaten.api import (
    ArtikelViewSet,
    GeschaeftspartnerViewSet,
    KontoViewSet,
    SteuersatzViewSet,
    StuecklisteViewSet,
)
from apps.versand.api import LvsViewSet, TourViewSet
from apps.vertrieb.api import (AuftragViewSet, EdiNachrichtViewSet, LeadViewSet, ShopArtikelViewSet,
                               ShopBestaendeView, ShopBestellungViewSet)
from apps.wareneingang.api import WareneingangViewSet
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

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
router.register("fertigungsauftraege", FertigungsauftragViewSet, basename="fertigungsauftrag")
router.register("stempelungen", StempelungViewSet, basename="stempelung")
router.register("rueckmeldungen", RueckmeldungViewSet, basename="rueckmeldung")
router.register("fibu/konten", SachkontoViewSet, basename="fibu-konto")
router.register("buchungen", BuchungViewSet, basename="buchung")
router.register("offene-posten", OPViewSet, basename="offener-posten")
router.register("bankumsaetze", BankumsatzViewSet, basename="bankumsatz")
router.register("vertrieb/auftraege", AuftragViewSet, basename="vk-auftrag")
router.register("vertrieb/leads", LeadViewSet, basename="lead")
router.register("edi/nachrichten", EdiNachrichtViewSet, basename="edi-nachricht")
router.register("versand/touren", TourViewSet, basename="tour")
router.register("versand/lvs", LvsViewSet, basename="lvs")
# Shop-API: Authentifizierung per Shop-Token (Header „Authorization: Shop <token>“)
router.register("shop/artikel", ShopArtikelViewSet, basename="shop-artikel")
router.register("shop/bestellungen", ShopBestellungViewSet, basename="shop-bestellung")

urlpatterns = [
    path("token/", obtain_auth_token, name="api-token"),
    path("shop/bestaende/", ShopBestaendeView.as_view(), name="shop-bestaende"),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="api-docs"),
    path("redoc/", SpectacularRedocView.as_view(url_name="schema"), name="api-redoc"),
    path("", include(router.urls)),
]

"""REST-API Vertrieb.

1. Onlineshop-API (/api/v1/shop/…): Authentifizierung je Shop über API-Token
   (Header „Authorization: Shop <token>“ oder „X-Shop-Token: <token>“). Artikel, Preise, Bestände,
   Bestellungen anlegen (idempotent über die externe Bestellnummer), Status- und Versandabfrage.
2. Interne API (/api/v1/vertrieb/…): Benutzer-Token/Session, Rechte über Modul „vertrieb“ bzw. „edi“.
"""
from decimal import Decimal
import hashlib
import secrets

from django.db import IntegrityError, transaction
from django.db.models import Sum
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_serializer, inline_serializer
from rest_framework import authentication, exceptions, mixins, permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import ModulRecht
from apps.lager.models import Bestand, Bestandsart
from apps.stammdaten.models import Adresse, Artikel, Geschaeftspartner

from . import services as S
from .edifact import EdiFehler, orders_importieren
from .models import Auftrag, AuftragPosition, EdiNachricht, Lead, Shop, ShopBestellung, VertriebFehler


# ================================================================== Token / Authentifizierung

def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def token_erzeugen(shop):
    """Erzeugt ein neues Token (nur einmal im Klartext sichtbar) und speichert dessen Hash."""
    token = "shop_" + secrets.token_urlsafe(32)
    shop.token_hash, shop.token_anfang = token_hash(token), token[:8]
    shop.save(update_fields=["token_hash", "token_anfang"])
    return token


class ShopBenutzer:
    """Pseudo-Benutzer für authentifizierte Shops (kein Django-Benutzer, keine Modulrechte)."""

    is_authenticated = True
    is_active = True
    is_staff = is_superuser = False
    pk = id = None

    def __init__(self, shop):
        self.shop = shop
        self.username = f"shop:{shop.name}"

    def __str__(self):
        return self.username


class ShopTokenAuthentication(authentication.BaseAuthentication):
    keyword = "Shop"

    def authenticate(self, request):
        token = request.META.get("HTTP_X_SHOP_TOKEN", "")
        kopf = request.META.get("HTTP_AUTHORIZATION", "").split()
        if not token and len(kopf) == 2 and kopf[0] == self.keyword:
            token = kopf[1]
        if not token:
            return None
        shop = Shop.objects.filter(token_hash=token_hash(token), aktiv=True).first()
        if not shop:
            raise exceptions.AuthenticationFailed("Ungültiges oder deaktiviertes Shop-Token.")
        return ShopBenutzer(shop), shop

    def authenticate_header(self, request):
        return self.keyword


class ShopTokenSchema(OpenApiAuthenticationExtension):
    """OpenAPI-Beschreibung der Shop-Token-Authentifizierung."""

    target_class = "apps.vertrieb.api.ShopTokenAuthentication"
    name = "ShopToken"

    def get_security_definition(self, auto_schema):
        return {"type": "apiKey", "in": "header", "name": "Authorization",
                "description": "Wert: „Shop <token>“ (alternativ Header „X-Shop-Token“)."}


class IstShop(permissions.BasePermission):
    def has_permission(self, request, view):
        return isinstance(request.auth, Shop)


class _ShopBasis:
    authentication_classes = [ShopTokenAuthentication]
    permission_classes = [IstShop]


# ================================================================== Shop: Artikel / Preise / Bestände

def _bestand(artikel, shop):
    qs = Bestand.objects.filter(artikel=artikel, bestandsart=Bestandsart.FREI)
    if shop.lagerort_id:
        qs = qs.filter(lagerort=shop.lagerort)
    return qs.aggregate(s=Sum("menge"))["s"] or 0


class ShopArtikelSerializer(serializers.ModelSerializer):
    einheit = serializers.CharField(source="einheit.code")
    steuersatz = serializers.DecimalField(source="steuersatz.satz", max_digits=5, decimal_places=2)
    preis_netto = serializers.SerializerMethodField()
    bestand = serializers.SerializerMethodField()

    class Meta:
        model = Artikel
        fields = ["artikelnummer", "bezeichnung", "beschreibung", "ean", "einheit", "steuersatz", "gewicht_kg",
                  "preis_netto", "bestand"]

    def get_preis_netto(self, a) -> str:
        return f"{a.verkaufspreis:.2f}"

    def get_bestand(self, a) -> str:
        return f"{_bestand(a, self.context['shop']):f}"


VERKAUFSARTEN = [Artikel.Art.FERTIGARTIKEL, Artikel.Art.HANDELSWARE]


class ShopArtikelViewSet(_ShopBasis, viewsets.ReadOnlyModelViewSet):
    """Verkaufsfähige Artikel (Fertigartikel, Handelsware) mit Listenpreis und freiem Bestand."""

    serializer_class = ShopArtikelSerializer
    lookup_field = "artikelnummer"
    lookup_value_regex = "[^/]+"

    def get_queryset(self):
        return Artikel.objects.filter(aktiv=True, art__in=VERKAUFSARTEN).select_related(
            "einheit", "steuersatz").order_by("artikelnummer")

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "shop": self.request.auth}


class ShopBestaendeView(_ShopBasis, APIView):
    @extend_schema(responses=inline_serializer("ShopBestand", {"artikelnummer": serializers.CharField(),
                                                               "bestand": serializers.CharField()}, many=True),
                   summary="Freie Bestände aller verkaufsfähigen Artikel")
    def get(self, request):
        daten = [{"artikelnummer": a.artikelnummer, "bestand": f"{_bestand(a, request.auth):f}"}
                 for a in Artikel.objects.filter(aktiv=True, art__in=VERKAUFSARTEN).order_by("artikelnummer")]
        return Response(daten)


# ================================================================== Shop: Bestellungen

@extend_schema_serializer(component_name="ShopAdresse")
class AdresseSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    strasse = serializers.CharField(max_length=200)
    plz = serializers.CharField(max_length=10)
    ort = serializers.CharField(max_length=100)
    land = serializers.CharField(max_length=2, default="DE")


@extend_schema_serializer(component_name="ShopKunde")
class KundeSerializer(AdresseSerializer):
    kundennummer = serializers.CharField(required=False, allow_blank=True,
                                         help_text="Bestehende Kundennummer im ERP (optional)")
    email = serializers.EmailField()
    telefon = serializers.CharField(required=False, allow_blank=True, max_length=50)
    ust_id = serializers.CharField(required=False, allow_blank=True, max_length=20)


class BestellPositionSerializer(serializers.Serializer):
    artikelnummer = serializers.CharField()
    menge = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal("0.001"))
    einzelpreis_netto = serializers.DecimalField(max_digits=12, decimal_places=4, required=False,
                                                 help_text="Leer = Preisfindung im ERP")


class BestellungEingangSerializer(serializers.Serializer):
    externe_nummer = serializers.CharField(max_length=100)
    kunde = KundeSerializer()
    lieferadresse = AdresseSerializer(required=False)
    liefertermin = serializers.DateField(required=False)
    bemerkung = serializers.CharField(required=False, allow_blank=True)
    positionen = BestellPositionSerializer(many=True)

    def validate_positionen(self, werte):
        if not werte:
            raise serializers.ValidationError("Mindestens eine Position erforderlich.")
        fehlend = [p["artikelnummer"] for p in werte if not Artikel.objects.filter(
            artikelnummer=p["artikelnummer"], aktiv=True, art__in=VERKAUFSARTEN).exists()]
        if fehlend:
            raise serializers.ValidationError(f"Unbekannte Artikel: {', '.join(fehlend)}")
        return werte


def bestellung_status(sb):
    a = sb.auftrag
    lieferungen = []
    for lf in a.lieferungen.select_related("beleg").order_by("pk"):
        tour = getattr(lf, "tourstopp", None)
        lieferungen.append({
            "lieferschein": lf.beleg.nummer, "status": lf.status, "status_text": lf.get_status_display(),
            "lieferdatum": lf.lieferdatum, "packstuecke": lf.packstuecke,
            "tour": tour.tour.nummer if tour else None, "zugestellt_am": lf.zugestellt_am,
            "rechnung": lf.rechnung.nummer if lf.rechnung_id else None})
    return {"externe_nummer": sb.externe_nummer, "auftrag": a.nummer, "status": a.status,
            "status_text": a.get_status_display(), "summe_netto": f"{a.summe_netto:.2f}",
            "positionen": [{"artikelnummer": p.artikel.artikelnummer, "menge": f"{p.menge:f}",
                            "geliefert": f"{p.menge_geliefert:f}", "einzelpreis_netto": f"{p.einzelpreis:.2f}"}
                           for p in a.positionen.select_related("artikel")],
            "lieferungen": lieferungen}


def _kunde_finden_oder_anlegen(shop, k):
    if k.get("kundennummer"):
        p = Geschaeftspartner.objects.filter(nummer=k["kundennummer"], ist_kunde=True).first()
        if p:
            return p
    p = Geschaeftspartner.objects.filter(ist_kunde=True, email__iexact=k["email"]).order_by("pk").first()
    if p:
        return p
    p = Geschaeftspartner.objects.create(name=k["name"], ist_kunde=True, email=k["email"],
                                         telefon=k.get("telefon", ""), ust_id=k.get("ust_id", ""),
                                         zahlungsbedingung=shop.zahlungsbedingung)
    Adresse.objects.create(partner=p, typ="HAUPT", strasse=k["strasse"], plz=k["plz"], ort=k["ort"],
                           land=k.get("land") or "DE")
    return p


def bestellung_anlegen(shop, daten, rohdaten=None):
    """(ShopBestellung, angelegt: bool). Idempotent: dieselbe externe Nummer liefert die bestehende Bestellung."""
    vorhanden = ShopBestellung.objects.filter(shop=shop, externe_nummer=daten["externe_nummer"]).first()
    if vorhanden:
        return vorhanden, False
    try:
        with transaction.atomic():
            partner = _kunde_finden_oder_anlegen(shop, daten["kunde"])
            positionen = []
            for p in daten["positionen"]:
                zeile = {"artikel": Artikel.objects.get(artikelnummer=p["artikelnummer"]), "menge": p["menge"]}
                if p.get("einzelpreis_netto") is not None:
                    zeile["einzelpreis"] = p["einzelpreis_netto"]
                positionen.append(zeile)
            felder = {"ihre_referenz": daten["externe_nummer"],
                      "bemerkung": (f"Onlineshop {shop.name}. " + daten.get("bemerkung", "")).strip(),
                      "liefertermin": daten.get("liefertermin")}
            la = daten.get("lieferadresse")
            if la:
                felder.update({"liefer_name": la["name"], "liefer_strasse": la["strasse"], "liefer_plz": la["plz"],
                               "liefer_ort": la["ort"], "liefer_land": la.get("land") or "DE"})
            a = S.auftrag_anlegen(partner, positionen, None, quelle=Auftrag.Quelle.SHOP, **felder)
            sb = ShopBestellung.objects.create(shop=shop, externe_nummer=daten["externe_nummer"], auftrag=a,
                                               rohdaten=rohdaten or {})
            if shop.auto_bestaetigen:
                S.auftrag_bestaetigen(a)
    except IntegrityError:
        # paralleler Doppel-Request: der andere hat gewonnen
        return ShopBestellung.objects.get(shop=shop, externe_nummer=daten["externe_nummer"]), False
    return sb, True


class ShopBestellungViewSet(_ShopBasis, viewsets.ViewSet):
    lookup_field = "externe_nummer"
    lookup_value_regex = "[^/]+"

    @extend_schema(request=BestellungEingangSerializer,
                   responses={201: dict, 200: dict}, summary="Bestellung übermitteln (idempotent)",
                   description="Legt Kunde (falls nötig) und Kundenauftrag an. Wiederholte Übermittlung derselben "
                               "externen Bestellnummer liefert HTTP 200 mit dem bestehenden Auftrag.")
    def create(self, request):
        ser = BestellungEingangSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            sb, neu = bestellung_anlegen(request.auth, ser.validated_data, dict(request.data))
        except VertriebFehler as exc:
            return Response({"fehler": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(bestellung_status(sb), status=status.HTTP_201_CREATED if neu else status.HTTP_200_OK)

    @extend_schema(responses=dict, summary="Status und Versandinformation einer Bestellung")
    def retrieve(self, request, externe_nummer=None):
        sb = ShopBestellung.objects.filter(shop=request.auth, externe_nummer=externe_nummer).first()
        if not sb:
            return Response({"fehler": "Bestellung nicht gefunden."}, status=status.HTTP_404_NOT_FOUND)
        return Response(bestellung_status(sb))

    @extend_schema(responses=dict, summary="Bestellungen dieses Shops", operation_id="shop_bestellungen_list")
    def list(self, request):
        return Response([bestellung_status(sb) for sb in ShopBestellung.objects.filter(shop=request.auth)
                         .select_related("auftrag")[:200]])


# ================================================================== Interne API

class AuftragPositionSerializer(serializers.ModelSerializer):
    artikelnummer = serializers.CharField(source="artikel.artikelnummer", read_only=True)

    class Meta:
        model = AuftragPosition
        fields = ["id", "position", "artikel", "artikelnummer", "bezeichnung", "kundenartikelnummer", "menge",
                  "einheit", "einzelpreis", "rabatt_prozent", "steuersatz", "liefertermin", "menge_geliefert",
                  "menge_berechnet"]
        read_only_fields = ["position", "bezeichnung", "einheit", "menge_geliefert", "menge_berechnet"]


class AuftragSerializer(serializers.ModelSerializer):
    positionen = AuftragPositionSerializer(many=True)
    summe_netto = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = Auftrag
        fields = ["id", "nummer", "partner", "status", "quelle", "bestelldatum", "liefertermin", "ihre_referenz",
                  "liefer_name", "liefer_strasse", "liefer_plz", "liefer_ort", "liefer_land", "bemerkung",
                  "ab_beleg", "summe_netto", "positionen"]
        read_only_fields = ["nummer", "status", "quelle", "ab_beleg"]

    def create(self, daten):
        positionen = [{k: v for k, v in p.items() if v is not None} for p in daten.pop("positionen")]
        partner = daten.pop("partner")
        return S.auftrag_anlegen(partner, positionen, self.context["request"].user, **daten)


class _VertriebFehlerMixin:
    def handle_exception(self, exc):
        if isinstance(exc, (VertriebFehler, EdiFehler)):
            return Response({"fehler": str(exc)}, status=status.HTTP_409_CONFLICT)
        return super().handle_exception(exc)


class AuftragViewSet(_VertriebFehlerMixin, mixins.CreateModelMixin, viewsets.ReadOnlyModelViewSet):
    """Kundenaufträge. POST legt einen Auftrag an; /bestaetigen/ erzeugt die Auftragsbestätigung."""

    serializer_class = AuftragSerializer
    permission_classes = [ModulRecht]
    modul_code = "vertrieb"
    recht_aktionen = {"bestaetigen": "freigeben", "verfuegbarkeit": "lesen"}

    def get_queryset(self):
        qs = Auftrag.objects.select_related("partner").prefetch_related("positionen__artikel")
        if self.request.query_params.get("status"):
            qs = qs.filter(status=self.request.query_params["status"])
        return qs

    @extend_schema(request=None, responses=AuftragSerializer)
    @action(detail=True, methods=["post"])
    def bestaetigen(self, request, pk=None):
        return Response(AuftragSerializer(S.auftrag_bestaetigen(self.get_object(), request.user)).data)

    @extend_schema(responses=dict)
    @action(detail=True)
    def verfuegbarkeit(self, request, pk=None):
        return Response([{"position": z["position"].position, "artikel": z["position"].artikel.artikelnummer,
                          "offen": str(z["offen"]), "frei": str(z["frei"]), "reserviert": str(z["reserviert"]),
                          "fehlmenge": str(z["fehlmenge"]), "eigenfertigung": z["eigenfertigung"]}
                         for z in S.verfuegbarkeit(self.get_object())])


class LeadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lead
        fields = ["id", "firma", "ansprechpartner", "email", "telefon", "strasse", "plz", "ort", "land", "quelle",
                  "status", "zustaendig", "partner", "bemerkung", "erstellt_am"]
        read_only_fields = ["partner", "erstellt_am"]


class LeadViewSet(viewsets.ModelViewSet):
    serializer_class = LeadSerializer
    permission_classes = [ModulRecht]
    modul_code = "vertrieb"
    queryset = Lead.objects.all()


class EdiNachrichtSerializer(serializers.ModelSerializer):
    class Meta:
        model = EdiNachricht
        fields = ["id", "richtung", "typ", "status", "edi_partner", "referenz", "kontrollnummer", "dateiname",
                  "fehler", "auftrag", "beleg", "erstellt_am", "inhalt"]
        read_only_fields = fields


class EdiNachrichtViewSet(viewsets.ReadOnlyModelViewSet):
    """EDI-Nachrichten. POST /orders/ importiert eine ORDERS-Datei (Text im Feld „inhalt“ oder Datei „datei“)."""

    serializer_class = EdiNachrichtSerializer
    permission_classes = [ModulRecht]
    modul_code = "edi"
    recht_aktionen = {"orders": "anlegen"}
    queryset = EdiNachricht.objects.all()

    @extend_schema(request=inline_serializer("OrdersImport", {"inhalt": serializers.CharField(required=False),
                                                              "datei": serializers.FileField(required=False)}),
                   responses=EdiNachrichtSerializer,
                   parameters=[OpenApiParameter("preise", bool, description="Preise aus PRI übernehmen")])
    @action(detail=False, methods=["post"])
    def orders(self, request):
        datei = request.FILES.get("datei")
        text = datei.read().decode("utf-8", errors="replace") if datei else request.data.get("inhalt", "")
        n = orders_importieren(text, datei.name if datei else "api.edi", request.user,
                               preise_uebernehmen=request.query_params.get("preise") == "1")
        return Response(EdiNachrichtSerializer(n).data,
                        status=status.HTTP_201_CREATED if n.status == "VERARBEITET" else 422)

"""REST-API Wareneingang: Buchung gegen Bestellung, QS-Entscheid, PDF."""
from django.http import HttpResponse
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.permissions import ModulRecht
from apps.einkauf.models import Bestellung, BestellPosition
from apps.lager.models import LagerFehler, Lagerort, Lagerplatz

from . import services
from .models import Wareneingang, WareneingangFehler, WEPosition
from .pdf import wareneingang_pdf


class WEPositionSerializer(serializers.ModelSerializer):
    artikelnummer = serializers.CharField(source="artikel.artikelnummer", read_only=True)
    chargennummer = serializers.CharField(source="charge.chargennummer", read_only=True, default=None)

    class Meta:
        model = WEPosition
        fields = ["id", "bestellposition", "artikel", "artikelnummer", "menge", "einheit", "lagerplatz", "charge",
                  "chargennummer", "offen_vorher", "mengendifferenz", "bestellpreis", "lieferpreis",
                  "preisdifferenz", "hinweis", "qs_erforderlich", "qs_status", "qs_bemerkung", "qs_am",
                  "zuruckgesendet", "lagerbewegung"]
        read_only_fields = fields


class WareneingangSerializer(serializers.ModelSerializer):
    positionen = WEPositionSerializer(many=True, read_only=True)
    bestellnummer = serializers.CharField(source="bestellung.nummer", read_only=True)

    class Meta:
        model = Wareneingang
        fields = ["id", "nummer", "bestellung", "bestellnummer", "lieferant", "lieferschein_nr", "datum", "lagerort",
                  "bemerkung", "hinweise", "gebucht_am", "positionen"]
        read_only_fields = fields


class BuchPositionSerializer(serializers.Serializer):
    bestellposition = serializers.PrimaryKeyRelatedField(queryset=BestellPosition.objects)
    menge = serializers.DecimalField(max_digits=14, decimal_places=3)
    chargennummer = serializers.CharField(required=False, allow_blank=True)
    lieferantencharge = serializers.CharField(required=False, allow_blank=True)
    mhd = serializers.DateField(required=False, allow_null=True)
    herstelldatum = serializers.DateField(required=False, allow_null=True)
    lagerplatz = serializers.PrimaryKeyRelatedField(queryset=Lagerplatz.objects, required=False, allow_null=True)
    lieferpreis = serializers.DecimalField(max_digits=14, decimal_places=4, required=False, allow_null=True)
    qs_pruefen = serializers.BooleanField(required=False, default=False)
    restmenge_abschliessen = serializers.BooleanField(required=False, default=False)


class BuchungSerializer(serializers.Serializer):
    bestellung = serializers.PrimaryKeyRelatedField(queryset=Bestellung.objects)
    lieferschein_nr = serializers.CharField()
    datum = serializers.DateField(required=False)
    lagerort = serializers.PrimaryKeyRelatedField(queryset=Lagerort.objects)
    bemerkung = serializers.CharField(required=False, allow_blank=True, default="")
    positionen = BuchPositionSerializer(many=True)


class WareneingangViewSet(mixins.CreateModelMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [ModulRecht]
    modul_code = "wareneingang"
    recht_aktionen = {"qs": "freigeben", "pdf": "lesen"}

    def get_serializer_class(self):
        return BuchungSerializer if self.action == "create" else WareneingangSerializer

    def get_queryset(self):
        qs = Wareneingang.objects.select_related("bestellung").prefetch_related("positionen__artikel",
                                                                                 "positionen__charge")
        if self.request.query_params.get("bestellung"):
            qs = qs.filter(bestellung_id=self.request.query_params["bestellung"])
        return qs

    def handle_exception(self, exc):
        if isinstance(exc, (WareneingangFehler, LagerFehler)):
            return Response({"fehler": str(exc)}, status=status.HTTP_409_CONFLICT)
        return super().handle_exception(exc)

    def create(self, request, *args, **kwargs):
        s = BuchungSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        we = services.buchen(d["bestellung"], d["positionen"], lieferschein_nr=d["lieferschein_nr"],
                             lagerort=d["lagerort"], datum=d.get("datum"), bemerkung=d["bemerkung"],
                             benutzer=request.user)
        return Response(WareneingangSerializer(we).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def qs(self, request, pk=None):
        """Body: {"position": id, "entscheid": "FREIGEGEBEN"|"GESPERRT", "bemerkung": "", "ruecksenden": false}"""
        we = self.get_object()
        try:
            pos = we.positionen.get(pk=request.data.get("position"))
        except (WEPosition.DoesNotExist, ValueError, TypeError):
            return Response({"fehler": "Position nicht gefunden."}, status=status.HTTP_404_NOT_FOUND)
        services.qs_entscheiden(pos, request.data.get("entscheid"), bemerkung=request.data.get("bemerkung", ""),
                                ruecksenden=bool(request.data.get("ruecksenden")), benutzer=request.user)
        return Response(WareneingangSerializer(we).data)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        return HttpResponse(wareneingang_pdf(self.get_object()), content_type="application/pdf")

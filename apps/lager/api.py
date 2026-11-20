"""REST-API des Lagers: Bestände und Bewegungen (lesend), manuelle Buchung per POST, Lagerorte, Chargen."""
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.response import Response

from apps.core.permissions import ModulRecht
from apps.stammdaten.models import Artikel

from . import services
from .models import Bestand, Bestandsart, Charge, Lagerbewegung, Lagerort, LagerFehler, Lagerplatz


class LagerplatzSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lagerplatz
        fields = ["id", "code", "bezeichnung", "aktiv"]


class LagerortSerializer(serializers.ModelSerializer):
    plaetze = LagerplatzSerializer(many=True, read_only=True)

    class Meta:
        model = Lagerort
        fields = ["id", "code", "bezeichnung", "beschreibung", "ist_wareneingang", "aktiv", "plaetze"]


class ChargeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Charge
        fields = ["id", "artikel", "chargennummer", "lieferant", "lieferantencharge", "herstelldatum", "mhd",
                  "bemerkung"]


class BestandSerializer(serializers.ModelSerializer):
    artikelnummer = serializers.CharField(source="artikel.artikelnummer", read_only=True)
    lagerort_code = serializers.CharField(source="lagerort.code", read_only=True)
    lagerplatz_code = serializers.CharField(source="lagerplatz.code", read_only=True, default=None)
    chargennummer = serializers.CharField(source="charge.chargennummer", read_only=True, default=None)

    class Meta:
        model = Bestand
        fields = ["id", "artikel", "artikelnummer", "lagerort", "lagerort_code", "lagerplatz", "lagerplatz_code",
                  "charge", "chargennummer", "bestandsart", "menge", "aktualisiert"]


class LagerbewegungSerializer(serializers.ModelSerializer):
    artikelnummer = serializers.CharField(source="artikel.artikelnummer", read_only=True)

    class Meta:
        model = Lagerbewegung
        fields = ["id", "zeitpunkt", "buchungsdatum", "art", "artikel", "artikelnummer", "charge", "menge",
                  "von_lagerort", "von_lagerplatz", "von_bestandsart", "nach_lagerort", "nach_lagerplatz",
                  "nach_bestandsart", "bewertungspreis", "wert", "durchschnittspreis_neu", "bestand_neu", "quelle",
                  "quelle_id", "beleg_nummer", "grund", "benutzer"]
        read_only_fields = fields


class BuchungSerializer(serializers.Serializer):
    """Eingabe für manuelle Buchungen (Zugang/Abgang/Umbuchung)."""

    art = serializers.ChoiceField(choices=["ZUGANG", "ABGANG", "UMBUCHUNG"])
    artikel = serializers.PrimaryKeyRelatedField(queryset=Artikel.objects)
    menge = serializers.DecimalField(max_digits=14, decimal_places=3)
    chargennummer = serializers.CharField(required=False, allow_blank=True)
    von_lagerort = serializers.PrimaryKeyRelatedField(queryset=Lagerort.objects, required=False)
    von_lagerplatz = serializers.PrimaryKeyRelatedField(queryset=Lagerplatz.objects, required=False)
    nach_lagerort = serializers.PrimaryKeyRelatedField(queryset=Lagerort.objects, required=False)
    nach_lagerplatz = serializers.PrimaryKeyRelatedField(queryset=Lagerplatz.objects, required=False)
    bestandsart = serializers.ChoiceField(choices=Bestandsart.values, default=Bestandsart.FREI)
    preis = serializers.DecimalField(max_digits=14, decimal_places=4, required=False)
    grund = serializers.CharField(required=False, allow_blank=True)


class LagerortViewSet(viewsets.ModelViewSet):
    permission_classes = [ModulRecht]
    modul_code = "lager"
    serializer_class = LagerortSerializer
    queryset = Lagerort.objects.prefetch_related("plaetze")


class ChargeViewSet(viewsets.ModelViewSet):
    permission_classes = [ModulRecht]
    modul_code = "lager"
    serializer_class = ChargeSerializer

    def get_queryset(self):
        qs = Charge.objects.all()
        if self.request.query_params.get("artikel"):
            qs = qs.filter(artikel_id=self.request.query_params["artikel"])
        return qs


class BestandViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [ModulRecht]
    modul_code = "lager"
    serializer_class = BestandSerializer

    def get_queryset(self):
        qs = Bestand.objects.filter(menge__gt=0).select_related("artikel", "lagerort", "lagerplatz", "charge")
        p = self.request.query_params
        for feld in ("artikel", "lagerort", "charge"):
            if p.get(feld):
                qs = qs.filter(**{f"{feld}_id": p[feld]})
        if p.get("bestandsart"):
            qs = qs.filter(bestandsart=p["bestandsart"])
        return qs


class LagerbewegungViewSet(mixins.CreateModelMixin, viewsets.ReadOnlyModelViewSet):
    """GET: unveränderliches Journal. POST: manuelle Buchung (Recht „anlegen“)."""

    permission_classes = [ModulRecht]
    modul_code = "lager"

    def get_serializer_class(self):
        return BuchungSerializer if self.action == "create" else LagerbewegungSerializer

    def get_queryset(self):
        qs = Lagerbewegung.objects.select_related("artikel")
        p = self.request.query_params
        if p.get("artikel"):
            qs = qs.filter(artikel_id=p["artikel"])
        if p.get("art"):
            qs = qs.filter(art=p["art"])
        return qs

    def create(self, request, *args, **kwargs):
        s = BuchungSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        art = d["art"]
        von = (d.get("von_lagerort"), d.get("von_lagerplatz"), d["bestandsart"]) if art != "ZUGANG" else None
        nach = (d.get("nach_lagerort"), d.get("nach_lagerplatz"), d["bestandsart"]) if art != "ABGANG" else None
        try:
            charge = services.charge_holen(d["artikel"], d.get("chargennummer"), anlegen=art == "ZUGANG")
            b = services.buchen(art, d["artikel"], d["menge"], von=von, nach=nach, charge=charge,
                                preis=d.get("preis"), benutzer=request.user, quelle="api", grund=d.get("grund", ""))
        except LagerFehler as exc:
            return Response({"fehler": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(LagerbewegungSerializer(b).data, status=status.HTTP_201_CREATED)

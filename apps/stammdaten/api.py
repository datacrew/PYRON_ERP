from rest_framework import serializers, viewsets

from apps.core.permissions import ModulRecht

from .models import Adresse, Artikel, Geschaeftspartner, Konto, Steuersatz, Stueckliste, StuecklistenPosition


class ArtikelSerializer(serializers.ModelSerializer):
    einheit_code = serializers.CharField(source="einheit.code", read_only=True)

    class Meta:
        model = Artikel
        fields = ["id", "artikelnummer", "bezeichnung", "beschreibung", "art", "einheit", "einheit_code",
                  "chargenpflicht", "steuersatz", "einkaufspreis", "verkaufspreis", "gewicht_kg", "ean", "aktiv"]


class AdresseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Adresse
        fields = ["id", "typ", "zusatz", "strasse", "plz", "ort", "land"]


class GeschaeftspartnerSerializer(serializers.ModelSerializer):
    adressen = AdresseSerializer(many=True, read_only=True)

    class Meta:
        model = Geschaeftspartner
        fields = ["id", "nummer", "name", "name2", "ist_kunde", "ist_lieferant", "ust_id", "steuernummer",
                  "email", "telefon", "zahlungsbedingung", "gln", "aktiv", "adressen"]
        extra_kwargs = {"nummer": {"required": False}}

    def validate(self, daten):
        kunde = daten.get("ist_kunde", getattr(self.instance, "ist_kunde", False))
        lieferant = daten.get("ist_lieferant", getattr(self.instance, "ist_lieferant", False))
        if not (kunde or lieferant):
            raise serializers.ValidationError("Ein Geschäftspartner muss Kunde und/oder Lieferant sein.")
        return daten


class StuecklistenPositionSerializer(serializers.ModelSerializer):
    komponente_nummer = serializers.CharField(source="komponente.artikelnummer", read_only=True)

    class Meta:
        model = StuecklistenPosition
        fields = ["position", "komponente", "komponente_nummer", "menge", "bemerkung"]


class StuecklisteSerializer(serializers.ModelSerializer):
    positionen = StuecklistenPositionSerializer(many=True, read_only=True)

    class Meta:
        model = Stueckliste
        fields = ["id", "artikel", "version", "bezeichnung", "basismenge", "aktiv", "positionen"]


class _Basis(viewsets.ModelViewSet):
    permission_classes = [ModulRecht]
    modul_code = "stammdaten"


class ArtikelViewSet(_Basis):
    queryset = Artikel.objects.select_related("einheit")
    serializer_class = ArtikelSerializer
    filterset_fields = None

    def get_queryset(self):
        qs = super().get_queryset()
        art = self.request.query_params.get("art")
        return qs.filter(art=art) if art else qs


class GeschaeftspartnerViewSet(_Basis):
    queryset = Geschaeftspartner.objects.prefetch_related("adressen")
    serializer_class = GeschaeftspartnerSerializer


class StuecklisteViewSet(_Basis):
    queryset = Stueckliste.objects.prefetch_related("positionen__komponente")
    serializer_class = StuecklisteSerializer


class KontoViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [ModulRecht]
    modul_code = "stammdaten"
    queryset = Konto.objects.all()
    serializer_class = type("KontoSerializer", (serializers.ModelSerializer,),
                            {"Meta": type("Meta", (), {"model": Konto, "fields": "__all__"})})


class SteuersatzViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [ModulRecht]
    modul_code = "stammdaten"
    queryset = Steuersatz.objects.all()
    serializer_class = type("SteuersatzSerializer", (serializers.ModelSerializer,),
                            {"Meta": type("Meta", (), {"model": Steuersatz, "fields": "__all__"})})

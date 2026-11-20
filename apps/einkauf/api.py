"""REST-API Einkauf: Bestellungen/Anfragen mit Positionen und Workflow-Aktionen."""
from django.db import transaction
from django.http import HttpResponse
from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.permissions import ModulRecht

from . import services
from .models import Bestellung, BestellPosition, EinkaufFehler
from .pdf import bestellung_pdf


@extend_schema_serializer(component_name="EinkaufBestellPosition")
class PositionSerializer(serializers.ModelSerializer):
    offen_menge = serializers.DecimalField(max_digits=14, decimal_places=3, read_only=True)
    betrag_netto = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = BestellPosition
        fields = ["id", "position", "artikel", "bezeichnung", "lieferanten_artikelnummer", "menge", "einheit",
                  "einzelpreis", "rabatt_prozent", "steuersatz", "liefertermin", "geliefert_menge", "offen_menge",
                  "erledigt", "betrag_netto"]
        read_only_fields = ["position", "geliefert_menge", "erledigt"]
        extra_kwargs = {"einzelpreis": {"required": False}, "steuersatz": {"required": False},
                        "bezeichnung": {"required": False}, "einheit": {"required": False}}


class BestellungSerializer(serializers.ModelSerializer):
    positionen = PositionSerializer(many=True)

    class Meta:
        model = Bestellung
        fields = ["id", "typ", "nummer", "status", "lieferant", "datum", "liefertermin", "lieferort",
                  "zahlungsbedingung", "lieferbedingung", "ihre_referenz", "kopftext", "fusstext", "summe_netto",
                  "summe_steuer", "summe_brutto", "freigegeben_am", "positionen"]
        read_only_fields = ["nummer", "status", "summe_netto", "summe_steuer", "summe_brutto", "freigegeben_am"]

    def validate_lieferant(self, wert):
        if not wert.ist_lieferant:
            raise serializers.ValidationError("Partner ist kein Lieferant.")
        return wert

    def _positionen(self, b, daten):
        b.positionen.all().delete()
        for i, p in enumerate(daten, start=1):
            pos = BestellPosition(bestellung=b, position=i * 10, **p)
            if "einzelpreis" not in p:
                pos.einzelpreis = None
            if "steuersatz" not in p:
                pos.steuersatz = None
            services.position_vorbelegen(pos).save()
        b.berechne_summen()
        b.save()

    @transaction.atomic
    def create(self, validated):
        positionen = validated.pop("positionen")
        if not validated.get("zahlungsbedingung"):
            validated["zahlungsbedingung"] = validated["lieferant"].zahlungsbedingung
        b = Bestellung.objects.create(erstellt_von=self.context["request"].user, **validated)
        self._positionen(b, positionen)
        return b

    @transaction.atomic
    def update(self, b, validated):
        if not b.ist_bearbeitbar:
            raise EinkaufFehler(f"{b} ist nicht mehr bearbeitbar.")
        positionen = validated.pop("positionen", None)
        for k, v in validated.items():
            setattr(b, k, v)
        b.save()
        if positionen is not None:
            self._positionen(b, positionen)
        return b


class BestellungViewSet(viewsets.ModelViewSet):
    permission_classes = [ModulRecht]
    modul_code = "einkauf"
    recht_aktionen = {"freigeben": "freigeben", "stornieren": "freigeben", "pdf": "lesen", "offen": "lesen"}
    serializer_class = BestellungSerializer

    def get_queryset(self):
        qs = Bestellung.objects.select_related("lieferant").prefetch_related("positionen")
        for feld in ("typ", "status", "lieferant"):
            if self.request.query_params.get(feld):
                qs = qs.filter(**{feld: self.request.query_params[feld]})
        return qs

    def handle_exception(self, exc):
        if isinstance(exc, EinkaufFehler):
            return Response({"fehler": str(exc)}, status=status.HTTP_409_CONFLICT)
        return super().handle_exception(exc)

    def perform_destroy(self, instance):
        if not instance.ist_bearbeitbar:
            raise EinkaufFehler("Nur Entwürfe können gelöscht werden.")
        instance.delete()

    @action(detail=True, methods=["post"])
    def freigeben(self, request, pk=None):
        return Response(self.get_serializer(services.freigeben(self.get_object(), request.user)).data)

    @action(detail=True, methods=["post"])
    def stornieren(self, request, pk=None):
        b = services.stornieren(self.get_object(), request.user, request.data.get("grund", ""))
        return Response(self.get_serializer(b).data)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        return HttpResponse(bestellung_pdf(self.get_object()), content_type="application/pdf")

    @action(detail=False, methods=["get"])
    def offen(self, request):
        """Offene Bestellpositionen mit Liefertermin."""
        daten = [{"bestellung": p.bestellung.nummer, "bestellung_id": p.bestellung_id, "position": p.position,
                  "position_id": p.pk, "artikel": p.artikel.artikelnummer, "lieferant": p.bestellung.lieferant.name,
                  "offen_menge": p.offen_menge, "liefertermin": p.termin, "ueberfaellig": p.ueberfaellig}
                 for p in services.offene_positionen()]
        return Response(daten)

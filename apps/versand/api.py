"""REST-API Versand: Touren und LVS-Schnittstelle (Kommissionieraufträge abholen, Rückmeldungen melden)."""
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.permissions import ModulRecht
from apps.vertrieb.models import Lieferung

from . import services as S
from .models import Tour, TourStopp, VersandFehler


class TourStoppSerializer(serializers.ModelSerializer):
    lieferschein = serializers.CharField(source="lieferung.nummer", read_only=True)
    kunde = serializers.CharField(source="lieferung.auftrag.partner.name", read_only=True)
    plz = serializers.CharField(source="lieferung.plz", read_only=True)
    ort = serializers.CharField(source="lieferung.ort", read_only=True)
    status = serializers.CharField(source="lieferung.status", read_only=True)

    class Meta:
        model = TourStopp
        fields = ["reihenfolge", "lieferung", "lieferschein", "kunde", "plz", "ort", "status", "entfernung_km"]


class TourSerializer(serializers.ModelSerializer):
    region = serializers.CharField(source="region.code", read_only=True)
    fahrzeug = serializers.CharField(source="fahrzeug.kennzeichen", read_only=True, default=None)
    stopps = TourStoppSerializer(many=True, read_only=True)
    auslastung = serializers.SerializerMethodField()

    class Meta:
        model = Tour
        fields = ["id", "nummer", "datum", "region", "fahrzeug", "fahrer", "status", "auslastung", "stopps"]

    def get_auslastung(self, t) -> dict:
        a = t.auslastung()
        return {k: (str(v) if v is not None and not isinstance(v, (bool, list, int)) else v)
                for k, v in a.items()}


class TourViewSet(viewsets.ReadOnlyModelViewSet):
    """Touren lesen; POST /bilden/ verplant offene Lieferscheine eines Datums."""

    serializer_class = TourSerializer
    permission_classes = [ModulRecht]
    modul_code = "versand"
    recht_aktionen = {"bilden": "anlegen"}

    def get_queryset(self):
        qs = Tour.objects.select_related("region", "fahrzeug").prefetch_related("stopps__lieferung__auftrag__partner")
        if self.request.query_params.get("datum"):
            qs = qs.filter(datum=self.request.query_params["datum"])
        return qs

    @extend_schema(request=inline_serializer("TourenBilden", {"datum": serializers.DateField(required=False)}),
                   responses=TourSerializer(many=True))
    @action(detail=False, methods=["post"])
    def bilden(self, request):
        f = serializers.DateField(required=False)
        datum = f.to_internal_value(request.data["datum"]) if request.data.get("datum") else None
        erg = S.touren_bilden(datum, request.user)
        return Response({"touren": TourSerializer(erg["touren"], many=True).data,
                         "nicht_zugeordnet": [{"lieferung": lf.pk, "grund": g} for lf, g in erg["nicht_zugeordnet"]]})


class LvsViewSet(viewsets.ViewSet):
    """LVS-Adapter. GET /kommissionierauftraege/ liefert offene Aufträge (setzt Status „An LVS übergeben“),
    POST /rueckmeldungen/ verarbeitet Rückmeldungen (kommissionierte Mengen, Chargen, Packstücke)."""

    permission_classes = [ModulRecht]
    modul_code = "versand"
    recht_aktionen = {"kommissionierauftraege": "anlegen", "rueckmeldungen": "anlegen"}

    @extend_schema(responses=dict, parameters=[OpenApiParameter("format", str, enum=["JSON", "CSV"])])
    @action(detail=False)
    def kommissionierauftraege(self, request):
        fmt = request.query_params.get("format", "JSON").upper()
        qs = Lieferung.objects.filter(status__in=["ERFASST", "KOMMISSIONIERUNG"]).select_related(
            "auftrag__partner", "lagerort")
        text = S.lvs_export(list(qs), fmt, request.user)
        if fmt == "CSV":
            from django.http import HttpResponse

            return HttpResponse(text, content_type="text/csv; charset=utf-8")
        import json

        return Response(json.loads(text))

    @extend_schema(request=inline_serializer("LvsRueckmeldungen", {
        "rueckmeldungen": serializers.ListField(child=serializers.DictField()),
        "buchen": serializers.BooleanField(required=False)}), responses=dict)
    @action(detail=False, methods=["post"])
    def rueckmeldungen(self, request):
        daten = request.data
        buchen = bool(daten.get("buchen")) if isinstance(daten, dict) else False
        try:
            ok, fehler = S.lvs_import(daten if isinstance(daten, (dict, list)) else dict(daten), "JSON",
                                      request.user, buchen=buchen)
        except VersandFehler as exc:
            return Response({"fehler": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"verarbeitet": [lf.kommissionier_referenz for lf in ok], "fehler": fehler},
                        status=status.HTTP_200_OK if not fehler else 207)

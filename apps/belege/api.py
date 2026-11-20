"""REST-API für Belege (z. B. Auftragsübernahme aus Onlineshops)."""
from django.db import transaction
from django.http import HttpResponse
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.permissions import ModulRecht

from . import services
from .models import Beleg, BelegGesperrt, BelegPosition
from .pdf import beleg_pdf


class PositionSerializer(serializers.ModelSerializer):
    class Meta:
        model = BelegPosition
        fields = ["position", "artikel", "bezeichnung", "menge", "einheit", "einzelpreis", "rabatt_prozent",
                  "steuersatz", "betrag_netto"]
        read_only_fields = ["betrag_netto"]
        extra_kwargs = {"position": {"required": False}, "einzelpreis": {"required": False},
                        "steuersatz": {"required": False}}


class BelegSerializer(serializers.ModelSerializer):
    positionen = PositionSerializer(many=True)

    class Meta:
        model = Beleg
        fields = ["id", "belegart", "nummer", "status", "partner", "belegdatum", "leistungsdatum",
                  "zahlungsbedingung", "ihre_referenz", "betreff", "kopftext", "fusstext", "waehrung",
                  "summe_netto", "summe_steuer", "summe_brutto", "pruefsumme", "positionen"]
        read_only_fields = ["nummer", "status", "summe_netto", "summe_steuer", "summe_brutto", "pruefsumme"]

    def _positionen(self, beleg, daten):
        beleg.positionen.all().delete()
        for i, p in enumerate(daten, start=1):
            artikel = p.get("artikel")
            if artikel:
                p.setdefault("einzelpreis", artikel.verkaufspreis)
                p.setdefault("steuersatz", artikel.steuersatz.satz)
            p["position"] = i * 10
            BelegPosition.objects.create(beleg=beleg, **p)
        beleg.berechne_summen()
        beleg.save()

    @transaction.atomic
    def create(self, validated):
        positionen = validated.pop("positionen")
        beleg = Beleg.objects.create(erstellt_von=self.context["request"].user, **validated)
        self._positionen(beleg, positionen)
        return beleg

    @transaction.atomic
    def update(self, beleg, validated):
        positionen = validated.pop("positionen", None)
        for k, v in validated.items():
            setattr(beleg, k, v)
        beleg.save()
        if positionen is not None:
            self._positionen(beleg, positionen)
        return beleg


class BelegViewSet(viewsets.ModelViewSet):
    permission_classes = [ModulRecht]
    modul_code = "belege"
    recht_aktionen = {"freigeben": "freigeben", "festschreiben": "freigeben", "stornieren": "freigeben",
                      "pdf": "lesen"}
    serializer_class = BelegSerializer

    def get_queryset(self):
        qs = Beleg.objects.select_related("partner").prefetch_related("positionen")
        for feld in ("belegart", "status"):
            if self.request.query_params.get(feld):
                qs = qs.filter(**{feld: self.request.query_params[feld]})
        return qs

    def handle_exception(self, exc):
        if isinstance(exc, (BelegGesperrt, services.WorkflowFehler)):
            return Response({"fehler": str(exc)}, status=status.HTTP_409_CONFLICT)
        return super().handle_exception(exc)

    def _workflow(self, funktion, *args):
        beleg = funktion(self.get_object(), self.request.user, *args)
        return Response(self.get_serializer(beleg).data)

    @action(detail=True, methods=["post"])
    def freigeben(self, request, pk=None):
        return self._workflow(services.freigeben)

    @action(detail=True, methods=["post"])
    def festschreiben(self, request, pk=None):
        return self._workflow(services.festschreiben)

    @action(detail=True, methods=["post"])
    def stornieren(self, request, pk=None):
        return self._workflow(services.stornieren, request.data.get("grund", ""))

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        beleg = self.get_object()
        inhalt = beleg.pdf.open("rb").read() if beleg.pdf else beleg_pdf(beleg)
        return HttpResponse(inhalt, content_type="application/pdf")

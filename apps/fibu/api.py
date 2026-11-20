"""REST-API Finanzbuchhaltung: Sachkonten mit Saldo, Buchungen (inkl. POST), offene Posten, Bankumsätze."""
from datetime import date

from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.permissions import ModulRecht
from apps.stammdaten.models import Konto

from . import services as S
from .auswertungen import _salden, gj_beginn
from .models import Bankumsatz, Buchung, Buchungszeile, FibuGesperrt, OffenerPosten


class _FibuFehlerMixin:
    def handle_exception(self, exc):
        if isinstance(exc, (S.FibuFehler, FibuGesperrt)):
            return Response({"fehler": str(exc)}, status=status.HTTP_409_CONFLICT)
        return super().handle_exception(exc)


class SachkontoSerializer(serializers.ModelSerializer):
    soll = serializers.SerializerMethodField()
    haben = serializers.SerializerMethodField()
    saldo = serializers.SerializerMethodField()

    class Meta:
        model = Konto
        fields = ["id", "kontenrahmen", "nummer", "bezeichnung", "kontoart", "bilanz_position", "guv_position",
                  "automatik", "partner", "soll", "haben", "saldo"]

    def _werte(self, k):
        s, h = self.context.get("salden", {}).get(k.pk, (S.NULL, S.NULL))
        return s, h

    def get_soll(self, k):
        return str(self._werte(k)[0])

    def get_haben(self, k):
        return str(self._werte(k)[1])

    def get_saldo(self, k):
        s, h = self._werte(k)
        return str(s - h)


class SachkontoViewSet(viewsets.ReadOnlyModelViewSet):
    """Konten des Mandanten-Kontenrahmens mit Verkehrszahlen (Parameter `bis`, Standard heute; ab GJ-Beginn)."""
    permission_classes = [ModulRecht]
    modul_code = "fibu"
    serializer_class = SachkontoSerializer

    def get_queryset(self):
        qs = Konto.objects.filter(kontenrahmen=S.rahmen()).order_by("nummer")
        if self.request.query_params.get("nummer"):
            qs = qs.filter(nummer__startswith=self.request.query_params["nummer"])
        return qs

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        try:
            bis = date.fromisoformat(self.request.query_params.get("bis", ""))
        except ValueError:
            from django.utils import timezone
            bis = timezone.localdate()
        ctx["salden"] = _salden(gj_beginn(bis), bis)
        return ctx


class ZeileSerializer(serializers.ModelSerializer):
    konto_nummer = serializers.CharField(source="konto.nummer", read_only=True)
    steuerschluessel_code = serializers.CharField(source="steuerschluessel.code", read_only=True, default="")

    class Meta:
        model = Buchungszeile
        fields = ["position", "konto", "konto_nummer", "soll", "haben", "text", "steuerschluessel_code",
                  "ist_steuer", "partner"]


class ZeileEingabe(serializers.Serializer):
    konto = serializers.CharField(help_text="Kontonummer")
    soll = serializers.DecimalField(max_digits=14, decimal_places=2, required=False)
    haben = serializers.DecimalField(max_digits=14, decimal_places=2, required=False)
    steuerschluessel = serializers.CharField(required=False, allow_blank=True)
    text = serializers.CharField(required=False, allow_blank=True)


class BuchungSerializer(serializers.ModelSerializer):
    zeilen = ZeileSerializer(many=True, read_only=True)
    eingabe = ZeileEingabe(many=True, write_only=True, required=True,
                           help_text="Zeilen: konto, soll|haben (netto), optional steuerschluessel, text")
    brutto = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model = Buchung
        fields = ["id", "nummer", "belegdatum", "belegnr", "text", "status", "quelle", "quelle_id", "storno_von",
                  "erfasst_am", "festgeschrieben_am", "ketten_index", "pruefsumme", "zeilen", "eingabe", "brutto"]
        read_only_fields = ["nummer", "status", "quelle", "quelle_id", "storno_von", "erfasst_am",
                            "festgeschrieben_am", "ketten_index", "pruefsumme"]

    def create(self, d):
        zeilen = []
        for z in d["eingabe"]:
            k = S.konto(z["konto"])
            zeilen.append({"konto": k, "soll": z.get("soll"), "haben": z.get("haben"),
                           "steuerschluessel": z.get("steuerschluessel") or None, "text": z.get("text", ""),
                           "partner": k.partner, "brutto": d.get("brutto", False)})
        return S.buchen(d["belegdatum"], d["belegnr"], d["text"], zeilen, benutzer=self.context["request"].user)


class BuchungViewSet(_FibuFehlerMixin, mixins.CreateModelMixin, viewsets.ReadOnlyModelViewSet):
    """Buchungsjournal. POST bucht (Recht fibu/anlegen); Änderungen nur per Storno-Aktion."""
    permission_classes = [ModulRecht]
    modul_code = "fibu"
    recht_aktionen = {"festschreiben": "freigeben", "stornieren": "anlegen"}
    serializer_class = BuchungSerializer

    def get_queryset(self):
        qs = Buchung.objects.prefetch_related("zeilen__konto", "zeilen__steuerschluessel").order_by("-belegdatum",
                                                                                                    "-pk")
        p = self.request.query_params
        if p.get("von"):
            qs = qs.filter(belegdatum__gte=p["von"])
        if p.get("bis"):
            qs = qs.filter(belegdatum__lte=p["bis"])
        for feld in ("status", "quelle", "belegnr"):
            if p.get(feld):
                qs = qs.filter(**{feld: p[feld]})
        return qs

    @action(detail=True, methods=["post"])
    def festschreiben(self, request, pk=None):
        b = self.get_object()
        S.festschreiben(b, request.user)
        b.refresh_from_db()
        return Response(self.get_serializer(b).data)

    @action(detail=True, methods=["post"])
    def stornieren(self, request, pk=None):
        st = S.stornieren(self.get_object(), request.user, grund=request.data.get("grund", ""))
        return Response(self.get_serializer(st).data, status=status.HTTP_201_CREATED)


class OPSerializer(serializers.ModelSerializer):
    partner_name = serializers.CharField(source="partner.name", read_only=True)
    konto_nummer = serializers.CharField(source="konto.nummer", read_only=True)

    class Meta:
        model = OffenerPosten
        fields = ["id", "art", "partner", "partner_name", "konto_nummer", "belegnr", "fremdbelegnr", "belegdatum",
                  "faellig_am", "skonto_bis", "skonto_prozent", "betrag", "offen", "mahnstufe", "mahnsperre",
                  "letzte_mahnung", "buchung"]


class OPViewSet(viewsets.ReadOnlyModelViewSet):
    """Offene Posten; Filter art=D|K, offen=1, partner."""
    permission_classes = [ModulRecht]
    modul_code = "fibu"
    serializer_class = OPSerializer

    def get_queryset(self):
        qs = OffenerPosten.objects.select_related("partner", "konto")
        p = self.request.query_params
        if p.get("art") in ("D", "K"):
            qs = qs.filter(art=p["art"])
        if p.get("offen") == "1":
            qs = qs.exclude(offen=0)
        if p.get("partner"):
            qs = qs.filter(partner_id=p["partner"])
        return qs


class BankumsatzSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bankumsatz
        fields = ["id", "bankkonto", "bankimport", "buchungsdatum", "valuta", "betrag", "waehrung", "name", "iban",
                  "verwendungszweck", "referenz", "gvc", "status", "buchung", "zuordnung_buchung"]


class BankumsatzViewSet(viewsets.ReadOnlyModelViewSet):
    """Bankumsätze (Recht fibu_bank); Filter status."""
    permission_classes = [ModulRecht]
    modul_code = "fibu_bank"
    serializer_class = BankumsatzSerializer

    def get_queryset(self):
        qs = Bankumsatz.objects.all()
        if self.request.query_params.get("status"):
            qs = qs.filter(status=self.request.query_params["status"])
        return qs

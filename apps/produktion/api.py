"""REST-API Produktion: Fertigungsaufträge, Stempelungen (inkl. POST für externe Terminals), Rückmeldungen."""
from django.http import HttpResponse
from django.utils.dateparse import parse_datetime
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.permissions import ModulRecht
from apps.stammdaten.models import Artikel

from . import services
from .models import FAArbeitsgang, FAMaterial, Fertigungsauftrag, ProduktionFehler, Rueckmeldung, Stempelung
from .pdf import laufkarte_pdf


class _ProduktionFehlerMixin:
    def handle_exception(self, exc):
        if isinstance(exc, ProduktionFehler):
            return Response({"fehler": str(exc)}, status=status.HTTP_409_CONFLICT)
        return super().handle_exception(exc)


class MaterialSerializer(serializers.ModelSerializer):
    artikelnummer = serializers.CharField(source="artikel.artikelnummer", read_only=True)

    class Meta:
        model = FAMaterial
        fields = ["id", "position", "artikel", "artikelnummer", "soll_menge", "entnommen_menge", "unterauftrag"]


class ArbeitsgangSerializer(serializers.ModelSerializer):
    code = serializers.CharField(read_only=True)

    class Meta:
        model = FAArbeitsgang
        fields = ["id", "code", "position", "bezeichnung", "arbeitsplatz", "ruestzeit_min", "stueckzeit_min",
                  "lohngruppe", "lohnart", "status", "gut_menge", "ausschuss_menge", "nacharbeit_menge"]


class FertigungsauftragSerializer(serializers.ModelSerializer):
    materialien = MaterialSerializer(many=True, read_only=True)
    arbeitsgaenge = ArbeitsgangSerializer(many=True, read_only=True)
    artikel = serializers.PrimaryKeyRelatedField(queryset=Artikel.objects.filter(art__in=services.FERTIGBARE_ARTEN))

    class Meta:
        model = Fertigungsauftrag
        fields = ["id", "nummer", "artikel", "stueckliste", "arbeitsplan", "menge", "starttermin", "termin", "status",
                  "uebergeordnet", "ziel_lagerort", "bemerkung", "gut_menge", "ausschuss_menge", "freigegeben_am",
                  "abgeschlossen_am", "materialien", "arbeitsgaenge"]
        read_only_fields = ["nummer", "stueckliste", "arbeitsplan", "status", "uebergeordnet", "gut_menge",
                            "ausschuss_menge", "freigegeben_am", "abgeschlossen_am"]

    def create(self, d):
        return services.fa_anlegen(d["artikel"], d["menge"], termin=d.get("termin"),
                                   starttermin=d.get("starttermin"), ziel_lagerort=d.get("ziel_lagerort"),
                                   bemerkung=d.get("bemerkung", ""), benutzer=self.context["request"].user)

    def update(self, fa, d):
        if fa.status != Fertigungsauftrag.Status.ENTWURF:
            raise ProduktionFehler("Nur Entwürfe können geändert werden.")
        if "artikel" in d and d["artikel"] != fa.artikel:
            raise ProduktionFehler("Der Artikel eines Auftrags kann nicht geändert werden.")
        for feld in ("menge", "starttermin", "termin", "ziel_lagerort", "bemerkung"):
            if feld in d:
                setattr(fa, feld, d[feld])
        fa.save()
        return fa


class FertigungsauftragViewSet(_ProduktionFehlerMixin, viewsets.ModelViewSet):
    permission_classes = [ModulRecht]
    modul_code = "produktion"
    recht_aktionen = {"freigeben": "freigeben", "stornieren": "freigeben", "fehlteile": "lesen",
                      "laufkarte": "lesen", "nachkalkulation": "lesen"}
    serializer_class = FertigungsauftragSerializer

    def get_queryset(self):
        qs = Fertigungsauftrag.objects.select_related("artikel").prefetch_related("materialien", "arbeitsgaenge")
        for feld in ("status", "artikel"):
            if self.request.query_params.get(feld):
                qs = qs.filter(**{feld: self.request.query_params[feld]})
        return qs

    def perform_destroy(self, fa):
        if fa.status != Fertigungsauftrag.Status.ENTWURF:
            raise ProduktionFehler("Nur Entwürfe können gelöscht werden.")
        fa.delete()

    @action(detail=True, methods=["post"])
    def freigeben(self, request, pk=None):
        fa, unter = services.freigeben(self.get_object(), request.user,
                                       unterauftraege=bool(request.data.get("unterauftraege")))
        daten = self.get_serializer(fa).data
        daten["unterauftraege_angelegt"] = [u.nummer for u in unter]
        return Response(daten)

    @action(detail=True, methods=["post"])
    def stornieren(self, request, pk=None):
        return Response(self.get_serializer(services.stornieren(self.get_object(), request.user,
                                                                request.data.get("grund", ""))).data)

    @action(detail=True, methods=["get"])
    def fehlteile(self, request, pk=None):
        return Response([{"artikel": z["artikel"].artikelnummer, "bedarf": z["bedarf"], "offen": z["offen"],
                          "frei": z["frei"], "fehlmenge": z["fehlmenge"], "eigenfertigung": z["eigenfertigung"]}
                         for z in services.fehlteile(self.get_object())])

    @action(detail=True, methods=["get"])
    def laufkarte(self, request, pk=None):
        return HttpResponse(laufkarte_pdf(self.get_object()), content_type="application/pdf")

    @action(detail=True, methods=["get"])
    def nachkalkulation(self, request, pk=None):
        k = services.nachkalkulation(self.get_object())
        return Response({"kostenarten": k["zeilen"], "hk_soll": k["hk_soll"], "hk_ist": k["hk_ist"],
                         "aktiviert": k["aktiviert"]})


class StempelungSerializer(serializers.ModelSerializer):
    personalnummer = serializers.CharField(source="mitarbeiter.personalnummer", read_only=True)
    code = serializers.CharField(source="arbeitsgang.code", read_only=True)
    dauer_min = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = Stempelung
        fields = ["id", "mitarbeiter", "personalnummer", "arbeitsgang", "code", "taetigkeit", "beginn", "ende",
                  "ende_art", "quelle", "korrigiert", "korrektur_grund", "dauer_min"]
        read_only_fields = fields


class TerminalBuchungSerializer(serializers.Serializer):
    personalnummer = serializers.CharField(help_text="Personal- oder Ausweisnummer")
    code = serializers.CharField(required=False, allow_blank=True, default="",
                                 help_text="Arbeitsgang-Code FA-JJJJ-NNNNN-AG (oder KOMMEN/GEHEN)")
    aktion = serializers.ChoiceField(choices=sorted(services.AKTIONEN), default="auto")
    gut = serializers.DecimalField(max_digits=12, decimal_places=3, default=0)
    ausschuss = serializers.DecimalField(max_digits=12, decimal_places=3, default=0)
    grund = serializers.CharField(required=False, allow_blank=True, default="")
    nacharbeit = serializers.DecimalField(max_digits=12, decimal_places=3, default=0)
    zeitpunkt = serializers.DateTimeField(required=False, help_text="optional (Offline-Terminals), sonst jetzt")


class StempelungViewSet(_ProduktionFehlerMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                        viewsets.GenericViewSet):
    """GET: Stempelungen; POST: Buchung wie am Terminal; PATCH …/korrektur/: Korrektur (Recht bde/ändern)."""
    permission_classes = [ModulRecht]
    modul_code = "bde"
    recht_aktionen = {"create": "anlegen", "korrektur": "aendern", "laufend": "lesen"}
    serializer_class = StempelungSerializer

    def get_queryset(self):
        qs = Stempelung.objects.select_related("mitarbeiter", "arbeitsgang__auftrag").order_by("-beginn")
        p = self.request.query_params
        if p.get("mitarbeiter"):
            qs = qs.filter(mitarbeiter_id=p["mitarbeiter"])
        if p.get("auftrag"):
            qs = qs.filter(arbeitsgang__auftrag_id=p["auftrag"])
        return qs

    def get_serializer_class(self):
        return TerminalBuchungSerializer if self.action == "create" else StempelungSerializer

    def create(self, request):
        s = TerminalBuchungSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        ma, text = services.terminal_buchen(d["personalnummer"], d["code"], d["aktion"], gut=d["gut"],
                                            ausschuss=d["ausschuss"], grund=d["grund"], nacharbeit=d["nacharbeit"],
                                            quelle=Stempelung.Quelle.API, benutzer=request.user,
                                            zeit=d.get("zeitpunkt"))
        st = Stempelung.objects.filter(mitarbeiter=ma).order_by("-beginn").first()
        return Response({"meldung": text, "mitarbeiter": ma.personalnummer,
                         "stempelung": StempelungSerializer(st).data if st else None,
                         "angestempelt": services.offene_stempelung(ma) is not None,
                         "anwesend": services.offene_anwesenheit(ma) is not None},
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch"])
    def korrektur(self, request, pk=None):
        st = self.get_object()
        beginn = parse_datetime(request.data["beginn"]) if request.data.get("beginn") else st.beginn
        if "ende" in request.data:
            ende = parse_datetime(request.data["ende"]) if request.data["ende"] else None
        else:
            ende = st.ende
        if beginn is None or ("ende" in request.data and request.data["ende"] and ende is None):
            raise ProduktionFehler("Ungültiges Datumsformat (ISO 8601 erwartet).")
        st = services.stempelung_korrigieren(st, beginn=beginn, ende=ende, grund=request.data.get("grund", ""),
                                             benutzer=request.user, taetigkeit=request.data.get("taetigkeit"))
        return Response(StempelungSerializer(st).data)

    @action(detail=False, methods=["get"])
    def laufend(self, request):
        return Response(StempelungSerializer(services.laufende_stempelungen(), many=True).data)


class RueckmeldungSerializer(serializers.ModelSerializer):
    code = serializers.CharField(source="arbeitsgang.code", read_only=True)

    class Meta:
        model = Rueckmeldung
        fields = ["id", "arbeitsgang", "code", "mitarbeiter", "zeitpunkt", "gut_menge", "ausschuss_menge",
                  "ausschuss_grund", "nacharbeit_menge", "bemerkung"]
        read_only_fields = ["zeitpunkt"]

    def create(self, d):
        return services.rueckmelden(d["arbeitsgang"], d.get("gut_menge", 0), d.get("ausschuss_menge", 0),
                                    d.get("ausschuss_grund", ""), d.get("nacharbeit_menge", 0),
                                    mitarbeiter=d.get("mitarbeiter"), benutzer=self.context["request"].user,
                                    bemerkung=d.get("bemerkung", ""))


class RueckmeldungViewSet(_ProduktionFehlerMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                          mixins.CreateModelMixin, viewsets.GenericViewSet):
    permission_classes = [ModulRecht]
    modul_code = "produktion"
    serializer_class = RueckmeldungSerializer

    def get_queryset(self):
        qs = Rueckmeldung.objects.select_related("arbeitsgang__auftrag")
        if self.request.query_params.get("auftrag"):
            qs = qs.filter(arbeitsgang__auftrag_id=self.request.query_params["auftrag"])
        return qs

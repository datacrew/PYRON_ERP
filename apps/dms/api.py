"""REST-API Dokumentenmanagement (Modul „dms“).

Authentifizierung wie die übrige API (Token „Authorization: Token <schlüssel>“, Session, Basic).
Rechte über ModulRecht: lesen = Liste/Detail/Download/Protokoll, anlegen = Upload/Verarbeiten,
aendern = Tags/Metadaten/Archivieren, freigeben = Verbuchen (zusätzlich fibu/anlegen), loeschen = Löschen.
Archivierte Dokumente bleiben über die API ebenso unveränderbar wie in der Oberfläche (GoBD).
"""
from django.db import transaction
from django.http import FileResponse, Http404
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_serializer, inline_serializer
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.core.permissions import ModulRecht, hat_recht

from . import services as S
from . import verarbeitung as V
from . import verbuchung as VB
from .models import Dokument, DokumentProtokoll, DokumentVersion, DmsGesperrt, Dokumentart, Stapel, Tag

FEHLER = (S.DmsFehler, DmsGesperrt)


@extend_schema_serializer(component_name="DmsTag")
class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "name", "farbe"]


@extend_schema_serializer(component_name="DmsVersion")
class VersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DokumentVersion
        fields = ["id", "version", "art", "quelle", "dateiname", "mime", "groesse", "sha256", "kommentar",
                  "erstellt_am"]


@extend_schema_serializer(component_name="DmsProtokoll")
class ProtokollSerializer(serializers.ModelSerializer):
    class Meta:
        model = DokumentProtokoll
        fields = ["id", "zeitpunkt", "benutzername", "aktion", "text"]


class DokumentListeSerializer(serializers.ModelSerializer):
    tags = TagSerializer(many=True, read_only=True)
    partner_name = serializers.CharField(source="partner.name", read_only=True, default=None)
    status_text = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Dokument
        fields = ["id", "nummer", "titel", "art", "status", "status_text", "stapel", "partner", "partner_name",
                  "belegdatum", "tags", "konfidenz", "archiviert", "aufbewahren_bis", "eingangsrechnung",
                  "erstellt_am"]


class DokumentDetailSerializer(DokumentListeSerializer):
    versionen = VersionSerializer(many=True, read_only=True)
    eingangsrechnung_nummer = serializers.CharField(source="eingangsrechnung.nummer", read_only=True, default=None)
    buchung_nummer = serializers.CharField(source="buchung.nummer", read_only=True, default=None)

    class Meta(DokumentListeSerializer.Meta):
        fields = DokumentListeSerializer.Meta.fields + [
            "bemerkung", "metadaten", "seiten", "textquelle", "ocr_text", "erkennung", "fehlermeldung",
            "eltern", "seiten_von", "seiten_bis", "archiviert_am", "eingangsrechnung_nummer", "buchung_nummer",
            "versionen"]
        read_only_fields = [f for f in fields if f not in ("titel", "belegdatum", "bemerkung", "metadaten",
                                                           "partner")]

    def update(self, d, validated):
        if d.archiviert:
            raise DmsGesperrt(f"Dokument {d.nummer} ist archiviert – Metadaten sind unveränderbar.")
        geaendert = [k for k, v in validated.items() if getattr(d, k) != v]
        d = super().update(d, validated)
        S.suchvektor_aktualisieren(d)
        if geaendert:
            S.protokollieren(d, "Metadaten", "Metadaten geändert (API): " + ", ".join(geaendert),
                             self.context["request"].user)
        return d


class UploadSerializer(serializers.Serializer):
    dateien = serializers.ListField(child=serializers.FileField(), allow_empty=False,
                                    help_text="Eine oder mehrere Dateien (PDF, PNG, JPEG, TIFF, XML)")
    art = serializers.ChoiceField(choices=Dokumentart.choices, default=Dokumentart.EINGANGSRECHNUNG)
    bezeichnung = serializers.CharField(required=False, allow_blank=True, default="")
    partner = serializers.IntegerField(required=False, allow_null=True, default=None)
    stapel = serializers.IntegerField(required=False, allow_null=True, default=None,
                                      help_text="Vorhandenen Stapel ergänzen statt neu anzulegen")
    automatisch_trennen = serializers.BooleanField(default=True)
    sofort_verarbeiten = serializers.BooleanField(default=False,
                                                  help_text="OCR/Erkennung direkt im Request ausführen")


class StapelSerializer(serializers.ModelSerializer):
    fortschritt = serializers.SerializerMethodField()

    class Meta:
        model = Stapel
        fields = ["id", "nummer", "bezeichnung", "quelle", "art", "automatisch_trennen", "erstellt_am",
                  "fortschritt"]

    def get_fortschritt(self, s) -> dict:
        f = s.fortschritt()
        return {"gesamt": f["gesamt"], "fertig": f["fertig"], "offen": f["offen"], "prozent": f["prozent"],
                "zaehlung": f["zaehlung"]}


def _fehler_antwort(exc):
    return Response({"fehler": str(exc)}, status=status.HTTP_409_CONFLICT)


@extend_schema(tags=["DMS"])
class DokumentViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
                      mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """Dokumente: Upload (multipart, Stapel), Suche, Detail mit OCR-Text/Erkennung, Download, Tags,
    Verarbeitung, Archivierung, Verbuchung und Protokoll."""

    permission_classes = [ModulRecht]
    modul_code = "dms"
    recht_aktionen = {"create": "anlegen", "verarbeiten": "anlegen", "tags": "aendern", "archivieren": "aendern",
                      "verbuchen": "freigeben", "datei": "lesen", "protokoll": "lesen", "status_abfrage": "lesen"}
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_serializer_class(self):
        if self.action == "list":
            return DokumentListeSerializer
        if self.action == "create":
            return UploadSerializer
        return DokumentDetailSerializer

    def get_queryset(self):
        qs = Dokument.objects.select_related("partner", "eingangsrechnung", "buchung").prefetch_related("tags")
        if self.action != "list":
            return qs.prefetch_related("versionen")
        p = self.request.query_params
        for feld in ("art", "status", "stapel", "partner"):
            if p.get(feld):
                qs = qs.filter(**{feld: p[feld]})
        if p.get("tag"):
            qs = qs.filter(tags__name=p["tag"]) if not p["tag"].isdigit() else qs.filter(tags__pk=p["tag"])
        if p.get("archiviert") in ("ja", "nein", "true", "false", "1", "0"):
            qs = qs.filter(archiviert=p["archiviert"] in ("ja", "true", "1"))
        if p.get("datum_von"):
            qs = qs.filter(belegdatum__gte=p["datum_von"])
        if p.get("datum_bis"):
            qs = qs.filter(belegdatum__lte=p["datum_bis"])
        return S.suchen(qs, p["q"]).distinct() if p.get("q") else qs.order_by("-erstellt_am", "-pk").distinct()

    def handle_exception(self, exc):
        if isinstance(exc, FEHLER):
            return _fehler_antwort(exc)
        return super().handle_exception(exc)

    @extend_schema(parameters=[
        OpenApiParameter("q", str, description="Volltextsuche (websearch-Syntax) über Nummer, Titel, OCR-Text"),
        OpenApiParameter("art", str, enum=Dokumentart.values), OpenApiParameter("status", str,
                                                                                enum=Dokument.Status.values),
        OpenApiParameter("tag", str, description="Schlagwort (Name oder ID)"),
        OpenApiParameter("partner", int), OpenApiParameter("stapel", int),
        OpenApiParameter("archiviert", str, enum=["ja", "nein"]),
        OpenApiParameter("datum_von", str, description="Dokumentdatum ab (JJJJ-MM-TT)"),
        OpenApiParameter("datum_bis", str, description="Dokumentdatum bis (JJJJ-MM-TT)")])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(request={"multipart/form-data": UploadSerializer},
                   responses={201: inline_serializer("DmsUploadAntwort", {
                       "stapel": StapelSerializer(), "dokumente": DokumentListeSerializer(many=True),
                       "fehler": serializers.ListField(child=serializers.CharField()),
                       "dubletten": serializers.ListField(child=serializers.CharField())})})
    def create(self, request, *args, **kwargs):
        """Upload einer oder mehrerer Dateien als Stapel (Feld „dateien“ mehrfach senden)."""
        ser = UploadSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        cd = ser.validated_data
        partner = None
        if cd["partner"]:
            from apps.stammdaten.models import Geschaeftspartner
            partner = Geschaeftspartner.objects.filter(pk=cd["partner"]).first()
            if not partner:
                return Response({"partner": ["Unbekannter Geschäftspartner."]}, status=status.HTTP_400_BAD_REQUEST)
        if cd["stapel"]:
            stapel = Stapel.objects.filter(pk=cd["stapel"]).first()
            if not stapel:
                return Response({"stapel": ["Unbekannter Stapel."]}, status=status.HTTP_400_BAD_REQUEST)
            neu = False
        else:
            stapel = S.stapel_anlegen(quelle=Stapel.Quelle.API,
                                      bezeichnung=cd["bezeichnung"] or f"API-Upload {timezone.localtime():%d.%m.%Y %H:%M}",
                                      art=cd["art"], automatisch_trennen=cd["automatisch_trennen"],
                                      benutzer=request.user)
            neu = True
        angelegt, fehler, dubletten = [], [], []
        for f in cd["dateien"]:
            daten = f.read()
            vorhanden = S.dublette_datei(daten)
            if vorhanden:
                dubletten.append(f"{f.name}: identische Datei bereits als {vorhanden.nummer} abgelegt.")
            try:
                angelegt.append(S.dokument_anlegen(daten, f.name, art=cd["art"], stapel=stapel, partner=partner,
                                                   benutzer=request.user, quelle_text="API-Upload"))
            except FEHLER as exc:
                fehler.append(str(exc))
        if not angelegt:
            if neu:
                stapel.delete()
            return Response({"fehler": fehler}, status=status.HTTP_400_BAD_REQUEST)
        if cd["sofort_verarbeiten"]:
            for d in angelegt:
                V.verarbeiten(d, request.user)
            V.warteschlange_abarbeiten(benutzer=request.user, stapel=stapel)  # Teile aus Sammel-PDFs
            for d in angelegt:
                d.refresh_from_db()
        return Response({"stapel": StapelSerializer(stapel).data,
                         "dokumente": DokumentListeSerializer(angelegt, many=True).data,
                         "fehler": fehler, "dubletten": dubletten}, status=status.HTTP_201_CREATED)

    def perform_destroy(self, instance):
        S.loeschen(instance, self.request.user)

    @extend_schema(responses=inline_serializer("DmsStatus", {
        "nummer": serializers.CharField(), "status": serializers.CharField(),
        "status_text": serializers.CharField(), "fehlermeldung": serializers.CharField(),
        "archiviert": serializers.BooleanField(), "eingangsrechnung": serializers.CharField(allow_null=True)}))
    @action(detail=True, methods=["get"], url_path="status")
    def status_abfrage(self, request, pk=None):
        """Kompakter Verarbeitungsstatus (für Abfrageschleifen)."""
        d = self.get_object()
        return Response({"nummer": d.nummer, "status": d.status, "status_text": d.get_status_display(),
                         "fehlermeldung": d.fehlermeldung, "archiviert": d.archiviert,
                         "eingangsrechnung": d.eingangsrechnung.nummer if d.eingangsrechnung_id else None})

    @extend_schema(parameters=[
        OpenApiParameter("version", int, description="ID einer Dokumentversion (Standard: Anzeigeversion)"),
        OpenApiParameter("original", bool, description="true = Originaldatei statt PDF/A")],
        responses={(200, "application/octet-stream"): bytes})
    @action(detail=True, methods=["get"])
    def datei(self, request, pk=None):
        """Download der Datei bzw. einer bestimmten Version (SHA-256 im Header X-Sha256)."""
        d = self.get_object()
        vid = request.query_params.get("version")
        if vid:
            v = d.versionen.filter(pk=vid).first() if str(vid).isdigit() else None
        elif request.query_params.get("original") in ("1", "true", "ja"):
            v = d.original
        else:
            v = d.anzeigeversion
        if not v:
            raise Http404("Version nicht gefunden.")
        antwort = FileResponse(v.datei.open("rb"), content_type=v.mime or "application/octet-stream",
                               as_attachment=True, filename=v.dateiname)
        antwort["X-Sha256"] = v.sha256
        return antwort

    @extend_schema(responses=ProtokollSerializer(many=True))
    @action(detail=True, methods=["get"])
    def protokoll(self, request, pk=None):
        return Response(ProtokollSerializer(self.get_object().protokoll.all(), many=True).data)

    @extend_schema(request=inline_serializer("DmsTagsSetzen", {
        "tags": serializers.ListField(child=serializers.CharField(),
                                      help_text="Schlagwortnamen; unbekannte werden angelegt"),
        "modus": serializers.ChoiceField(choices=["setzen", "hinzufuegen", "entfernen"], default="setzen")}),
        responses=TagSerializer(many=True))
    @action(detail=True, methods=["post"])
    def tags(self, request, pk=None):
        d = self.get_object()
        namen = [str(n).strip()[:60] for n in (request.data.get("tags") or []) if str(n).strip()]
        modus = request.data.get("modus", "setzen")
        if modus not in ("setzen", "hinzufuegen", "entfernen"):
            return Response({"modus": ["setzen, hinzufuegen oder entfernen"]}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            tags = [Tag.objects.get_or_create(name=n)[0] for n in namen]
            if modus == "setzen":
                d.tags.set(tags)
            elif modus == "hinzufuegen":
                d.tags.add(*tags)
            else:
                d.tags.remove(*tags)
            S.protokollieren(d, "Schlagwörter", f"Schlagwörter ({modus}, API): {', '.join(namen) or '–'}",
                             request.user)
        return Response(TagSerializer(d.tags.all(), many=True).data)

    @extend_schema(request=None, responses=DokumentDetailSerializer)
    @action(detail=True, methods=["post"])
    def verarbeiten(self, request, pk=None):
        """OCR und Erkennung (erneut) ausführen."""
        d = self.get_object()
        V.verarbeiten(d, request.user)
        d.refresh_from_db()
        return Response(DokumentDetailSerializer(d).data)

    @extend_schema(request=None, responses=DokumentDetailSerializer)
    @action(detail=True, methods=["post"])
    def archivieren(self, request, pk=None):
        d = S.archivieren(self.get_object(), request.user)
        return Response(DokumentDetailSerializer(d).data)

    @extend_schema(request=None, responses=inline_serializer("DmsVerbuchtAntwort", {
            "eingangsrechnung": serializers.CharField(), "eingangsrechnung_id": serializers.IntegerField(),
            "buchung": serializers.CharField(), "dokument": DokumentDetailSerializer()}))
    @action(detail=True, methods=["post"])
    def verbuchen(self, request, pk=None):
        """Freigabe: Eingangsrechnung in der Fibu anlegen und buchen (Recht dms/freigeben + fibu/anlegen)."""
        if not hat_recht(request.user, "fibu", "anlegen"):
            raise PermissionDenied("Kein Recht „anlegen“ im Modul „fibu“.")
        d = self.get_object()
        er = VB.verbuchen(d, request.user)
        d.refresh_from_db()
        return Response({"eingangsrechnung": er.nummer, "eingangsrechnung_id": er.pk, "buchung": er.buchung.nummer,
                         "dokument": DokumentDetailSerializer(d).data})


@extend_schema(tags=["DMS"])
class StapelViewSet(viewsets.ReadOnlyModelViewSet):
    """Verarbeitungsstapel mit Fortschritt; POST …/verarbeiten arbeitet die Warteschlange des Stapels ab."""

    permission_classes = [ModulRecht]
    modul_code = "dms"
    recht_aktionen = {"verarbeiten": "anlegen"}
    serializer_class = StapelSerializer
    queryset = Stapel.objects.all().order_by("-erstellt_am", "-pk")

    @extend_schema(request=None, responses=inline_serializer("DmsStapelVerarbeitet", {
        "ok": serializers.IntegerField(), "fehler": serializers.IntegerField(), "stapel": StapelSerializer()}))
    @action(detail=True, methods=["post"])
    def verarbeiten(self, request, pk=None):
        stapel = self.get_object()
        limit = int(request.query_params.get("limit") or 100)
        ok, fehler = V.warteschlange_abarbeiten(limit=limit, benutzer=request.user, stapel=stapel)
        return Response({"ok": ok, "fehler": fehler, "stapel": StapelSerializer(stapel).data})


@extend_schema(tags=["DMS"])
class TagViewSet(viewsets.ModelViewSet):
    """Schlagwörter (anlegen/ändern/löschen nach DMS-Rechten)."""

    permission_classes = [ModulRecht]
    modul_code = "dms"
    serializer_class = TagSerializer
    queryset = Tag.objects.all()

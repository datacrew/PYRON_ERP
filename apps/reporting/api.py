"""REST-API: Berichtsliste und Abruf eines Berichts als JSON (Rechte: reporting.lesen + Leserecht Quellmodul)."""
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import hat_recht

from . import export, registry
from .filter import als_json, bereinigen


class ReportingRecht(permissions.BasePermission):
    def has_permission(self, request, view):
        return hat_recht(request.user, "reporting")


def _beschreibung(b):
    return {"code": b.code, "name": b.name, "modul": b.modul, "beschreibung": b.beschreibung,
            "filter": b.filter,
            "spalten": [{"key": s.key, "label": s.label, "typ": s.typ, "summe": s.summe} for s in b.spalten]}


class BerichtListeView(APIView):
    """GET /api/v1/berichte/ – alle für den Benutzer sichtbaren Berichte."""

    permission_classes = [permissions.IsAuthenticated, ReportingRecht]

    def get(self, request):
        return Response([_beschreibung(b) for b in registry.fuer_benutzer(request.user)])


class BerichtAbrufView(APIView):
    """GET /api/v1/berichte/<code>/?von=2026-01-01&bis=2026-12-31&kunde=<id> – Bericht als JSON."""

    permission_classes = [permissions.IsAuthenticated, ReportingRecht]

    def get(self, request, code):
        b = registry.holen(code)
        if b is None:
            return Response({"detail": "Unbekannter Bericht."}, status=status.HTTP_404_NOT_FOUND)
        if not registry.darf(request.user, b):
            return Response({"detail": "Kein Leserecht auf das Modul dieses Berichts."},
                            status=status.HTTP_403_FORBIDDEN)
        werte, fehler = bereinigen(b, request.query_params)
        if fehler:
            return Response({"detail": "Ungültige Filter.", "fehler": fehler}, status=status.HTTP_400_BAD_REQUEST)
        return Response({**_beschreibung(b), "filterwerte": als_json(werte),
                         **export.als_json_zeilen(b, b.ausfuehren(werte))})

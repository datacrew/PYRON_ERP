"""Arbeitet die DMS-Warteschlange ab (Status „Neu“): Trennung, Texterkennung, Belegerkennung."""
from django.core.management.base import BaseCommand

from apps.dms import verarbeitung as V


class Command(BaseCommand):
    help = "Verarbeitet wartende DMS-Dokumente (OCR/Erkennung). Ohne Celery – z. B. per systemd-Timer."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None, help="Höchstens N Dokumente verarbeiten.")

    def handle(self, *args, limit=None, **opts):
        wartend = V.warteschlange().count()
        ok, fehler = V.warteschlange_abarbeiten(limit=limit)
        self.stdout.write(self.style.SUCCESS(
            f"DMS-Warteschlange: {ok} verarbeitet, {fehler} mit Fehler, {max(wartend - ok - fehler, 0)} noch offen."))

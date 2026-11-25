"""Prüft den WORM-Speicher: Datei-Hashes und Hash-Kette des Manifests. Exit-Code 1 bei Abweichungen."""
import sys

from django.core.management.base import BaseCommand

from apps.dms import worm
from apps.dms.models import WormEintrag


class Command(BaseCommand):
    help = "WORM-Speicher prüfen (SHA-256 der Dateien und Hash-Kette des Append-only-Manifests)."

    def handle(self, *args, **opts):
        fehler = worm.pruefen()
        anzahl = WormEintrag.objects.count()
        for f in fehler:
            self.stderr.write(self.style.ERROR(f))
        if fehler:
            self.stderr.write(self.style.ERROR(f"{len(fehler)} Abweichung(en) bei {anzahl} Einträgen."))
            sys.exit(1)
        self.stdout.write(self.style.SUCCESS(f"WORM-Speicher in Ordnung: {anzahl} Einträge, Hash-Kette lückenlos."))

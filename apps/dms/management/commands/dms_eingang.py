"""Eingangsordner (Hotfolder) des DMS einlesen – ohne Celery, z. B. alle 5 Minuten per systemd-Timer.

Ordnerstruktur (Standard settings.ERP_DMS_EINGANG):
    eingang/                     → Dateien im Hauptordner erhalten die Vorgabe-Dokumentart (--art)
    eingang/vertrag/ …           → Unterordner mit dem Namen einer Dokumentart (eingangsrechnung, ausgangsrechnung,
                                   vertrag, lieferschein, sonstiges) legen die Art fest
    eingang/verarbeitet/JJJJ-MM-TT/  übernommene Dateien
    eingang/fehler/              abgelehnte Dateien (+ .txt mit Grund)
    eingang/doppelt/             bereits vorhandene Dateien (gleicher SHA-256)
"""
import fcntl
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.dms import services as S
from apps.dms import verarbeitung as V
from apps.dms.models import Dokumentart, Stapel

SYSTEMORDNER = {"verarbeitet", "fehler", "doppelt"}


def _ziel(ordner, unterordner, datei):
    ziel = ordner / unterordner
    ziel.mkdir(parents=True, exist_ok=True)
    pfad = ziel / datei.name
    n = 1
    while pfad.exists():
        pfad = ziel / f"{datei.stem}_{n}{datei.suffix}"
        n += 1
    datei.rename(pfad)
    return pfad


class Command(BaseCommand):
    help = "Liest den DMS-Eingangsordner ein und verarbeitet die neuen Dokumente (OCR/Erkennung)."

    def add_arguments(self, parser):
        parser.add_argument("--ordner", help="Eingangsordner (Standard: settings.ERP_DMS_EINGANG)")
        parser.add_argument("--art", default=Dokumentart.EINGANGSRECHNUNG, choices=Dokumentart.values,
                            help="Dokumentart für Dateien im Hauptordner (Standard: Eingangsrechnung)")
        parser.add_argument("--min-alter", type=int, default=10,
                            help="Nur Dateien übernehmen, die seit N Sekunden unverändert sind (Scanner schreibt noch)")
        parser.add_argument("--ohne-verarbeitung", action="store_true",
                            help="Nur einlesen; OCR später per dms_verarbeiten bzw. in der Oberfläche")
        parser.add_argument("--ohne-trennung", action="store_true", help="Sammel-PDFs nicht an Trennblättern teilen")

    def handle(self, *args, **o):
        ordner = Path(o["ordner"] or settings.ERP_DMS_EINGANG)
        if not ordner.is_dir():
            raise CommandError(f"Eingangsordner {ordner} existiert nicht.")
        with open(ordner / ".dms_eingang.lock", "w") as sperre:
            try:
                fcntl.flock(sperre, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.stdout.write("Eingangsordner wird bereits von einem anderen Lauf verarbeitet – übersprungen.")
                return
            self._einlesen(ordner, o)

    def _dateien(self, ordner, vorgabe_art, min_alter):
        arten = {a.lower(): a for a in Dokumentart.values}
        jetzt = time.time()
        kandidaten = [(p, vorgabe_art) for p in ordner.iterdir() if p.is_file()]
        for unter in ordner.iterdir():
            if unter.is_dir() and unter.name.lower() in arten:
                kandidaten += [(p, arten[unter.name.lower()]) for p in unter.iterdir() if p.is_file()]
        for pfad, art in sorted(kandidaten):
            # versteckte Dateien und noch geschriebene Dateien (.part/.tmp, zu jung) überspringen
            if pfad.name.startswith(".") or pfad.suffix.lower() in (".part", ".tmp", ".txt"):
                continue
            if jetzt - pfad.stat().st_mtime < min_alter:
                continue
            yield pfad, art

    def _einlesen(self, ordner, o):
        stapel, angelegt, fehler, doppelt = None, 0, 0, 0
        heute = timezone.localdate().isoformat()
        for pfad, art in self._dateien(ordner, o["art"], o["min_alter"]):
            daten = pfad.read_bytes()
            vorhanden = S.dublette_datei(daten)
            if vorhanden:
                _ziel(ordner, "doppelt", pfad)
                self.stdout.write(f"  doppelt: {pfad.name} (bereits {vorhanden.nummer})")
                doppelt += 1
                continue
            if stapel is None:
                stapel = S.stapel_anlegen(quelle=Stapel.Quelle.EINGANGSORDNER, art=o["art"],
                                          bezeichnung=f"Eingangsordner {timezone.localtime():%d.%m.%Y %H:%M}",
                                          automatisch_trennen=not o["ohne_trennung"])
            try:
                d = S.dokument_anlegen(daten, pfad.name, art=art, stapel=stapel, quelle_text="Eingangsordner")
            except (S.DmsFehler, ValueError) as exc:
                ziel = _ziel(ordner, "fehler", pfad)
                ziel.with_name(ziel.name + ".txt").write_text(f"{timezone.localtime():%d.%m.%Y %H:%M}: {exc}\n",
                                                              encoding="utf-8")
                self.stdout.write(self.style.WARNING(f"  Fehler: {pfad.name}: {exc}"))
                fehler += 1
                continue
            _ziel(ordner, f"verarbeitet/{heute}", pfad)
            self.stdout.write(f"  {d.nummer}: {pfad.name}")
            angelegt += 1
        ok = fehl = 0
        if stapel and not o["ohne_verarbeitung"]:
            ok, fehl = V.warteschlange_abarbeiten(stapel=stapel)
        self.stdout.write(self.style.SUCCESS(
            f"Eingangsordner: {angelegt} übernommen{f' (Stapel {stapel.nummer})' if stapel else ''}, "
            f"{doppelt} doppelt, {fehler} abgelehnt; verarbeitet {ok}, Verarbeitungsfehler {fehl}."))

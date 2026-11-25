"""Rechnungseingang per E-Mail: IMAP-Postfach abrufen (z. B. alle 5 Minuten per systemd-Timer/Docker-Dienst).

Konfiguration ausschließlich über Umgebung/.env (ERP_DMS_IMAP_HOST, _PORT, _USER, _PASSWORD bzw. _PASSWORD_FILE,
_ORDNER, _ZIELORDNER, _FEHLERORDNER, _ABSENDER, _MAX_MAILS) – siehe deploy/INSTALLATION.md, Abschnitt 11.5.
Ohne konfigurierten Host beendet sich der Befehl ohne Fehler (Abruf deaktiviert).
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.dms import mailabruf as M
from apps.dms.models import Dokumentart


class Command(BaseCommand):
    help = "Ruft das IMAP-Postfach für Eingangsrechnungen ab und legt die Anhänge als DMS-Stapel an."

    def add_arguments(self, parser):
        parser.add_argument("--ordner", help="IMAP-Ordner (Standard: ERP_DMS_IMAP_ORDNER)")
        parser.add_argument("--zielordner", help="Ordner für verarbeitete Mails (Standard: ERP_DMS_IMAP_ZIELORDNER)")
        parser.add_argument("--art", choices=Dokumentart.values, help="Dokumentart der Anhänge (Standard: ER)")
        parser.add_argument("--limit", type=int, help="Höchstens N Mails je Lauf (Standard: ERP_DMS_IMAP_MAX_MAILS)")
        parser.add_argument("--alle", action="store_true", help="Auch bereits gelesene Mails prüfen "
                                                                "(Dubletten werden per Message-ID übersprungen)")
        parser.add_argument("--trocken", action="store_true", help="Nur anzeigen, nichts anlegen oder markieren")
        parser.add_argument("--ohne-verarbeitung", action="store_true", help="OCR/Erkennung später ausführen")

    def handle(self, *args, **o):
        if not settings.ERP_DMS_IMAP_HOST:
            self.stdout.write("E-Mail-Abruf deaktiviert (ERP_DMS_IMAP_HOST nicht gesetzt).")
            return
        try:
            k = M.konfiguration(ordner=o["ordner"], zielordner=o["zielordner"], art=o["art"], max_mails=o["limit"])
            erg = M.abrufen(k, trocken=o["trocken"], alle=o["alle"], verarbeiten=not o["ohne_verarbeitung"],
                            ausgabe=self.stdout.write)
        except M.MailabrufFehler as exc:
            raise CommandError(str(exc)) from exc
        for m in erg.meldungen:
            self.stdout.write(self.style.WARNING(f"  {m}"))
        self.stdout.write(self.style.SUCCESS(
            f"Mailabruf {k['user']}@{k['host']}/{k['ordner']}: {erg.mails} Mail(s), {erg.uebernommen} übernommen "
            f"({erg.dokumente} Dokumente), {erg.doppelt} bereits bekannt, {erg.ohne_anhang} ohne Anhang, "
            f"{erg.ignoriert} ignoriert, {erg.fehler} Fehler."))

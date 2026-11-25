"""Integritätsprüfung nach Wiederherstellung oder Update.

Prüft: GoBD-/Lagerjournal-/SEPA-Sperr-/DMS-Trigger vorhanden und aktiv, Hash-Kette der Belege (inkl.
Archiv-PDFs) und der Fibu-Buchungen sowie die SHA-256-Siegel aller DMS-Dateiversionen. Exit-Code 1 bei Fehlern.

    python manage.py erp_pruefen [--ohne-pdf]
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

PFLICHT_TRIGGER = {
    "belege_beleg_gobd": "belege_beleg",
    "belege_position_gobd": "belege_belegposition",
    "fibu_buchung_gobd": "fibu_buchung",
    "fibu_zeile_gobd": "fibu_buchungszeile",
    "lager_bewegung_unveraenderlich": "lager_lagerbewegung",
    "fibu_sepa_lauf_gesperrt": "fibu_sepa_zahlungslauf",
    "fibu_sepa_posten_gesperrt": "fibu_sepa_zahlungslaufposten",
    "dms_dokument_gobd": "dms_dokument",
    "dms_version_gobd": "dms_dokumentversion",
    "dms_protokoll_gobd": "dms_dokumentprotokoll",
}


class Command(BaseCommand):
    help = "Prüft GoBD-Trigger und Hash-Ketten (Belege, Fibu) – z. B. nach einer Wiederherstellung."

    def add_arguments(self, parser):
        parser.add_argument("--ohne-pdf", action="store_true",
                            help="Archiv-PDFs und DMS-Dateien nicht prüfen (z. B. Datenbank ohne zugehörige Media-Dateien)")

    def handle(self, *args, **opt):
        from apps.belege import services as belege
        from apps.belege.models import Beleg
        from apps.fibu import services as fibu

        fehler = []
        with connection.cursor() as cur:
            cur.execute("""SELECT t.tgname, c.relname, t.tgenabled FROM pg_trigger t
                           JOIN pg_class c ON c.oid = t.tgrelid WHERE NOT t.tgisinternal""")
            vorhanden = {name: (tabelle, aktiv) for name, tabelle, aktiv in cur.fetchall()}
            cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
            tabellen = cur.fetchone()[0]
        for name in PFLICHT_TRIGGER:
            if name not in vorhanden:
                fehler.append(f"Trigger {name} fehlt.")
            elif vorhanden[name][1] == "D":
                fehler.append(f"Trigger {name} ist deaktiviert.")
        self.stdout.write(f"Tabellen: {tabellen}, Belege: {Beleg.objects.count()}, "
                          f"Trigger: {sum(1 for n in PFLICHT_TRIGGER if n in vorhanden)}/{len(PFLICHT_TRIGGER)}")

        if opt["ohne_pdf"]:
            # PDF-Prüfung überspringen: Belege ohne Dateizugriff prüfen
            anzahl_b, fb = 0, []
            vorher = ""
            for b in Beleg.objects.exclude(ketten_index=None).order_by("ketten_index").prefetch_related("positionen"):
                anzahl_b += 1
                if b.vorgaenger_pruefsumme != vorher:
                    fb.append(f"{b}: Verkettung unterbrochen.")
                if belege.pruefsumme_berechnen(b) != b.pruefsumme:
                    fb.append(f"{b}: Prüfsumme abweichend.")
                vorher = b.pruefsumme
        else:
            anzahl_b, fb = belege.kette_pruefen()
        anzahl_f, ff = fibu.kette_pruefen()
        fehler += fb + ff
        if not opt["ohne_pdf"]:
            from apps.dms.services import integritaet_pruefen

            anzahl_d, fd = integritaet_pruefen()
            fehler += fd
            self.stdout.write(f"DMS-Dateien (SHA-256): {anzahl_d} geprüft, {len(fd)} Fehler")
        self.stdout.write(f"Hash-Kette Belege: {anzahl_b} geprüft, {len(fb)} Fehler")
        self.stdout.write(f"Hash-Kette Fibu-Buchungen: {anzahl_f} geprüft, {len(ff)} Fehler")
        if fehler:
            for f in fehler[:50]:
                self.stderr.write(f"  FEHLER: {f}")
            raise CommandError(f"Integritätsprüfung fehlgeschlagen ({len(fehler)} Fehler).")
        self.stdout.write(self.style.SUCCESS("Integritätsprüfung bestanden."))

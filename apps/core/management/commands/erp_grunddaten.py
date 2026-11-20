"""Grunddaten anlegen (idempotent): Module, Mandant, Einheiten, Steuersätze, Zahlungsbedingungen,
Kontenrahmen SKR03/SKR04 inkl. Fibu-Grunddaten, Nummernkreise, Standardrollen und Belegvorlage."""
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.models import AKTION_CODES, Mandant, Modul, Nummernkreis, Recht, Rolle
from apps.core.registry import module_synchronisieren

EINHEITEN = [("Stk", "Stück"), ("kg", "Kilogramm"), ("t", "Tonne"), ("m", "Meter"), ("m²", "Quadratmeter"),
             ("l", "Liter"), ("Std", "Stunde"), ("Pal", "Palette"), ("Krt", "Karton"), ("Satz", "Satz")]

STEUERSAETZE = [("UST19", "Regelsteuersatz", "19.00"), ("UST7", "Ermäßigter Steuersatz", "7.00"),
                ("IGL0", "Innergemeinschaftliche Lieferung (steuerfrei)", "0.00"),
                ("EXPORT0", "Ausfuhrlieferung Drittland (steuerfrei)", "0.00"),
                ("RC0", "Reverse Charge § 13b UStG", "0.00")]

ZAHLUNGSBEDINGUNGEN = [
    ("SOFORT", "Zahlbar sofort ohne Abzug", 0, 0, "0"),
    ("14N", "Zahlbar innerhalb 14 Tagen netto", 14, 0, "0"),
    ("30N", "Zahlbar innerhalb 30 Tagen netto", 30, 0, "0"),
    ("10S2_30N", "10 Tage 2 % Skonto, 30 Tage netto", 30, 10, "2"),
]

# Kontenrahmen SKR03/SKR04 (erweiterter Grundbestand mit Bilanz-/GuV-Zuordnung) → apps/fibu/kontenrahmen.py

NUMMERNKREISE = [
    ("PARTNER", "Geschäftspartner", "GP-", False, 5),
    ("BELEG_ANGEBOT", "Angebote", "AN-", True, 5), ("BELEG_AUFTRAG", "Auftragsbestätigungen", "AB-", True, 5),
    ("BELEG_LIEFERSCHEIN", "Lieferscheine", "LS-", True, 5), ("BELEG_RECHNUNG", "Rechnungen", "RE-", True, 5),
    ("BELEG_GUTSCHRIFT", "Gutschriften", "GS-", True, 5), ("BELEG_ANFRAGE", "Anfragen", "AF-", True, 5),
    ("BELEG_BESTELLUNG", "Bestellungen", "BE-", True, 5), ("BELEG_WARENEINGANG", "Wareneingänge", "WE-", True, 5),
    # Phase 2 – Warenfluss (eigene Tabellen, daher eigene Kreise)
    ("EK_BANF", "Bestellanforderungen", "BA-", True, 5), ("EK_ANFRAGE", "Einkaufsanfragen", "EA-", True, 5),
    ("EK_BESTELLUNG", "Einkaufsbestellungen", "EK-", True, 5),
    ("WARENEINGANG", "Wareneingangsbuchungen", "WEB-", True, 5), ("INVENTUR", "Inventuren", "INV-", True, 4),
    # Phase 3 – Fertigung
    ("FERTIGUNGSAUFTRAG", "Fertigungsaufträge", "FA-", True, 5),
    ("FERTIGUNGSCHARGE", "Fertigungschargen", "FC-", True, 5),
    # Phase 4 – Finanzbuchhaltung
    ("FIBU_BUCHUNG", "Buchungen (Journal)", "FB-", True, 6), ("FIBU_ER", "Eingangsrechnungen", "ER-", True, 5),
    ("FIBU_BANKIMPORT", "Bankimporte", "BI-", True, 4), ("FIBU_MAHNLAUF", "Mahnläufe", "ML-", True, 4),
    # Phase 5 – Vertrieb und Versand
    ("VK_AUFTRAG", "Kundenaufträge", "VA-", True, 5), ("TOUR", "Versandtouren", "TO-", True, 5),
    ("EDI", "EDIFACT-Übertragungsnummern (UNB)", "", False, 8),
]

L, A, AE, F, D = AKTION_CODES  # lesen, anlegen, aendern, freigeben, loeschen
ALLE = AKTION_CODES
ROLLEN = {
    "Administrator": ("Vollzugriff auf alle Module inkl. Verwaltung", {"*": ALLE}),
    "Einkauf": ("Einkauf, Lieferanten- und Artikelpflege, Bestellungen",
                {"stammdaten": [L, A, AE], "einkauf": ALLE, "wareneingang": [L, A], "lager": [L],
                 "belege": [L, A, AE, F], "reporting": [L]}),
    "Vertrieb": ("Kundenbetreuung, Angebote, Aufträge, Rechnungen",
                 {"stammdaten": [L, A, AE], "vertrieb": ALLE, "belege": [L, A, AE, F, D], "versand": [L],
                  "edi": ALLE, "shopapi": [L, A, AE], "lager": [L], "produktion": [L, A], "reporting": [L]}),
    "Produktion": ("Fertigungsaufträge, Rückmeldungen, Stücklisten, Arbeitspläne, Stempel-Terminal",
                   {"stammdaten": [L, A, AE], "produktion": ALLE, "bde": [L, A], "wareneingang": [L],
                    "lager": [L, A]}),
    "Fertigungsleitung": ("Meister/Fertigungsleitung: Produktion, Stempelkorrektur und Akkordauswertung",
                          {"stammdaten": [L, A, AE], "produktion": ALLE, "bde": ALLE, "akkord": ALLE,
                           "lager": [L, A], "wareneingang": [L], "einkauf": [L], "reporting": [L]}),
    "Terminal": ("Nur Stempel-Terminal (Kiosk-Benutzer an der Fertigungsstation)", {"bde": [L, A]}),
    "Lager": ("Lagerbestand, Wareneingang inkl. Qualitätsprüfung und Versand",
              {"stammdaten": [L], "lager": ALLE, "wareneingang": ALLE, "versand": ALLE, "einkauf": [L],
               "belege": [L], "vertrieb": [L], "reporting": [L]}),
    "Buchhaltung": ("Finanzbuchhaltung, Belegprüfung und Festschreibung",
                    {"stammdaten": [L, AE], "fibu": ALLE, "fibu_bank": ALLE, "fibu_mahnwesen": ALLE,
                     "fibu_abschluss": ALLE, "belege": [L, F], "reporting": [L], "lager": [L],
                     "einkauf": [L], "wareneingang": [L], "produktion": [L], "akkord": [L]}),
    "Nur Lesen": ("Lesezugriff auf alle Fachmodule (ohne Lohndaten)", {"*-verwaltung": [L]}),
}


class Command(BaseCommand):
    help = "Legt die Grunddaten des ERP an bzw. ergänzt fehlende Einträge (idempotent)."

    @transaction.atomic
    def handle(self, *args, **options):
        from apps.belege.models import BelegVorlage
        from apps.stammdaten.models import Einheit, Konto, Steuersatz, Zahlungsbedingung

        module_synchronisieren()
        if not Mandant.objects.exists():
            Mandant.objects.create(name="Musterwerk GmbH", strasse="Industriestraße 12", plz="70565",
                                   ort="Stuttgart", telefon="+49 711 123456-0", email="info@musterwerk.example",
                                   ust_id="DE123456789", handelsregister="HRB 123456, Amtsgericht Stuttgart",
                                   geschaeftsfuehrer="Erika Mustermann", bank="Musterbank Stuttgart",
                                   iban="DE02120300000000202051", bic="BYLADEM1001")
        for code, bez in EINHEITEN:
            Einheit.objects.get_or_create(code=code, defaults={"bezeichnung": bez})
        for code, bez, satz in STEUERSAETZE:
            Steuersatz.objects.get_or_create(code=code, defaults={"bezeichnung": bez, "satz": Decimal(satz)})
        for code, bez, netto, sk_tage, sk in ZAHLUNGSBEDINGUNGEN:
            Zahlungsbedingung.objects.get_or_create(code=code, defaults={
                "bezeichnung": bez, "tage_netto": netto, "skonto_tage": sk_tage, "skonto_prozent": Decimal(sk)})
        from apps.fibu.kontenrahmen import grunddaten_anlegen as fibu_grunddaten

        fibu_grunddaten()
        for code, bez, praefix, jahr, stellen in NUMMERNKREISE:
            Nummernkreis.objects.get_or_create(code=code, defaults={
                "bezeichnung": bez, "praefix": praefix, "jahresbezogen": jahr, "stellen": stellen})
        module = {m.code: m for m in Modul.objects.all()}
        for name, (beschreibung, rechte) in ROLLEN.items():
            rolle, angelegt = Rolle.objects.get_or_create(name=name, defaults={"beschreibung": beschreibung})
            if "*" in rechte:
                rechte = {code: rechte["*"] for code in module}
            elif "*-verwaltung" in rechte:
                # Lohndaten (akkord) sind vertraulich und nicht Teil von „Nur Lesen“
                rechte = {code: rechte["*-verwaltung"] for code in module if code not in ("verwaltung", "akkord")}
            # Neue Rollen erhalten alle Rechte; bestehende nur Einträge für neu hinzugekommene Module –
            # vom Administrator angepasste Rechte bleiben unangetastet.
            vorhanden = set() if angelegt else set(rolle.rechte.values_list("modul__code", flat=True))
            for code, aktionen in rechte.items():
                if code in module and code not in vorhanden:
                    Recht.objects.create(rolle=rolle, modul=module[code], **{a: a in aktionen for a in ALLE})
        if not BelegVorlage.objects.exists():
            html = (settings.BASE_DIR / "templates/belege/pdf/standard.html").read_text(encoding="utf-8")
            BelegVorlage.objects.create(name="Standard DIN 5008", belegart="", html=html)
        if not BelegVorlage.objects.filter(belegart="LIEFERSCHEIN").exists():
            html = (settings.BASE_DIR / "templates/belege/pdf/lieferschein.html").read_text(encoding="utf-8")
            BelegVorlage.objects.create(name="Lieferschein ohne Preise", belegart="LIEFERSCHEIN", html=html)
        self.stdout.write(self.style.SUCCESS(
            f"Grunddaten bereit: {Modul.objects.count()} Module, {Konto.objects.count()} Konten, "
            f"{Nummernkreis.objects.count()} Nummernkreise, {Rolle.objects.count()} Rollen."))

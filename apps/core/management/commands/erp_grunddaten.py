"""Grunddaten anlegen (idempotent): Module, Mandant, Einheiten, Steuersätze, Zahlungsbedingungen,
Kontenrahmen SKR03/SKR04 (Grundbestand), Nummernkreise, Standardrollen und Belegvorlage."""
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

# Grundbestand – vor Produktivbetrieb mit Steuerberatung / DATEV-Kontenrahmen abgleichen
SKR03 = [
    ("0200", "Technische Anlagen und Maschinen", "aktiv"), ("0320", "Pkw", "aktiv"),
    ("0420", "Büroeinrichtung", "aktiv"), ("0480", "Geringwertige Wirtschaftsgüter", "aktiv"),
    ("0800", "Gezeichnetes Kapital", "passiv"), ("0860", "Gewinnvortrag vor Verwendung", "passiv"),
    ("0868", "Verlustvortrag vor Verwendung", "passiv"), ("0970", "Sonstige Rückstellungen", "passiv"),
    ("1000", "Kasse", "aktiv"), ("1200", "Bank", "aktiv"), ("1360", "Geldtransit", "aktiv"),
    ("1400", "Forderungen aus Lieferungen und Leistungen", "aktiv"),
    ("1571", "Abziehbare Vorsteuer 7 %", "steuer"), ("1576", "Abziehbare Vorsteuer 19 %", "steuer"),
    ("1590", "Durchlaufende Posten", "aktiv"),
    ("1600", "Verbindlichkeiten aus Lieferungen und Leistungen", "passiv"),
    ("1740", "Verbindlichkeiten aus Lohn und Gehalt", "passiv"),
    ("1771", "Umsatzsteuer 7 %", "steuer"), ("1776", "Umsatzsteuer 19 %", "steuer"),
    ("1780", "Umsatzsteuer-Vorauszahlungen", "steuer"), ("1790", "Umsatzsteuer Vorjahr", "steuer"),
    ("2100", "Zinsen und ähnliche Aufwendungen", "aufwand"), ("2400", "Forderungsverluste", "aufwand"),
    ("2650", "Sonstige Zinsen und ähnliche Erträge", "ertrag"),
    ("3000", "Roh-, Hilfs- und Betriebsstoffe", "aufwand"), ("3300", "Wareneingang 7 % Vorsteuer", "aufwand"),
    ("3400", "Wareneingang 19 % Vorsteuer", "aufwand"), ("3736", "Erhaltene Skonti 19 % Vorsteuer", "aufwand"),
    ("3970", "Bestand Roh-, Hilfs- und Betriebsstoffe", "aktiv"), ("3980", "Bestand Waren", "aktiv"),
    ("4110", "Löhne", "aufwand"), ("4120", "Gehälter", "aufwand"),
    ("4130", "Gesetzliche soziale Aufwendungen", "aufwand"), ("4210", "Miete", "aufwand"),
    ("4240", "Gas, Strom, Wasser", "aufwand"), ("4360", "Versicherungen", "aufwand"),
    ("4500", "Fahrzeugkosten", "aufwand"), ("4600", "Werbekosten", "aufwand"),
    ("4730", "Ausgangsfrachten", "aufwand"),
    ("4800", "Reparaturen und Instandhaltung von technischen Anlagen und Maschinen", "aufwand"),
    ("4830", "Abschreibungen auf Sachanlagen", "aufwand"), ("4910", "Porto", "aufwand"),
    ("4920", "Telefon", "aufwand"), ("4930", "Bürobedarf", "aufwand"),
    ("4950", "Rechts- und Beratungskosten", "aufwand"), ("4970", "Nebenkosten des Geldverkehrs", "aufwand"),
    ("7000", "Unfertige Erzeugnisse", "aktiv"), ("7110", "Fertige Erzeugnisse", "aktiv"),
    ("8120", "Steuerfreie Umsätze § 4 Nr. 1a UStG (Ausfuhr)", "ertrag"),
    ("8125", "Steuerfreie innergemeinschaftliche Lieferungen § 4 Nr. 1b UStG", "ertrag"),
    ("8300", "Erlöse 7 % USt", "ertrag"), ("8400", "Erlöse 19 % USt", "ertrag"),
    ("8736", "Gewährte Skonti 19 % USt", "ertrag"),
    ("9000", "Saldenvorträge Sachkonten", "neutral"),
]
SKR04 = [
    ("0440", "Maschinen", "aktiv"), ("0520", "Pkw", "aktiv"), ("0650", "Büroeinrichtung", "aktiv"),
    ("0670", "Geringwertige Wirtschaftsgüter", "aktiv"),
    ("1000", "Roh-, Hilfs- und Betriebsstoffe (Bestand)", "aktiv"), ("1040", "Unfertige Erzeugnisse", "aktiv"),
    ("1110", "Fertige Erzeugnisse", "aktiv"), ("1140", "Waren (Bestand)", "aktiv"),
    ("1200", "Forderungen aus Lieferungen und Leistungen", "aktiv"),
    ("1401", "Abziehbare Vorsteuer 7 %", "steuer"), ("1406", "Abziehbare Vorsteuer 19 %", "steuer"),
    ("1460", "Geldtransit", "aktiv"), ("1600", "Kasse", "aktiv"), ("1800", "Bank", "aktiv"),
    ("2900", "Gezeichnetes Kapital", "passiv"), ("2970", "Gewinnvortrag vor Verwendung", "passiv"),
    ("2978", "Verlustvortrag vor Verwendung", "passiv"), ("3070", "Sonstige Rückstellungen", "passiv"),
    ("3300", "Verbindlichkeiten aus Lieferungen und Leistungen", "passiv"),
    ("3720", "Verbindlichkeiten aus Lohn und Gehalt", "passiv"),
    ("3801", "Umsatzsteuer 7 %", "steuer"), ("3806", "Umsatzsteuer 19 %", "steuer"),
    ("3820", "Umsatzsteuer-Vorauszahlungen", "steuer"), ("3840", "Umsatzsteuer Vorjahr", "steuer"),
    ("4120", "Steuerfreie Umsätze § 4 Nr. 1a UStG (Ausfuhr)", "ertrag"),
    ("4125", "Steuerfreie innergemeinschaftliche Lieferungen § 4 Nr. 1b UStG", "ertrag"),
    ("4300", "Erlöse 7 % USt", "ertrag"), ("4400", "Erlöse 19 % USt", "ertrag"),
    ("4736", "Gewährte Skonti 19 % USt", "ertrag"),
    ("5000", "Aufwendungen für Roh-, Hilfs- und Betriebsstoffe", "aufwand"),
    ("5300", "Wareneingang 7 % Vorsteuer", "aufwand"), ("5400", "Wareneingang 19 % Vorsteuer", "aufwand"),
    ("5736", "Erhaltene Skonti 19 % Vorsteuer", "aufwand"),
    ("6010", "Löhne", "aufwand"), ("6020", "Gehälter", "aufwand"),
    ("6110", "Gesetzliche soziale Aufwendungen", "aufwand"),
    ("6220", "Abschreibungen auf Sachanlagen", "aufwand"), ("6310", "Miete", "aufwand"),
    ("6325", "Gas, Strom, Wasser", "aufwand"), ("6400", "Versicherungen", "aufwand"),
    ("6460", "Reparaturen und Instandhaltung von technischen Anlagen und Maschinen", "aufwand"),
    ("6500", "Fahrzeugkosten", "aufwand"), ("6600", "Werbekosten", "aufwand"),
    ("6740", "Ausgangsfrachten", "aufwand"), ("6800", "Porto", "aufwand"), ("6805", "Telefon", "aufwand"),
    ("6815", "Bürobedarf", "aufwand"), ("6825", "Rechts- und Beratungskosten", "aufwand"),
    ("6855", "Nebenkosten des Geldverkehrs", "aufwand"), ("6930", "Forderungsverluste", "aufwand"),
    ("7100", "Sonstige Zinsen und ähnliche Erträge", "ertrag"),
    ("7300", "Zinsen und ähnliche Aufwendungen", "aufwand"),
    ("9000", "Saldenvorträge Sachkonten", "neutral"),
]

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
                  "reporting": [L]}),
    "Produktion": ("Fertigungsaufträge, Rückmeldungen, Stücklisten, Arbeitspläne, Stempel-Terminal",
                   {"stammdaten": [L, A, AE], "produktion": ALLE, "bde": [L, A], "wareneingang": [L],
                    "lager": [L, A]}),
    "Fertigungsleitung": ("Meister/Fertigungsleitung: Produktion, Stempelkorrektur und Akkordauswertung",
                          {"stammdaten": [L, A, AE], "produktion": ALLE, "bde": ALLE, "akkord": ALLE,
                           "lager": [L, A], "wareneingang": [L], "einkauf": [L]}),
    "Terminal": ("Nur Stempel-Terminal (Kiosk-Benutzer an der Fertigungsstation)", {"bde": [L, A]}),
    "Lager": ("Lagerbestand, Wareneingang inkl. Qualitätsprüfung und Versand",
              {"stammdaten": [L], "lager": ALLE, "wareneingang": ALLE, "versand": ALLE, "einkauf": [L],
               "belege": [L]}),
    "Buchhaltung": ("Finanzbuchhaltung, Belegprüfung und Festschreibung",
                    {"stammdaten": [L, AE], "fibu": ALLE, "belege": [L, F], "reporting": [L], "lager": [L],
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
        for rahmen, konten in (("SKR03", SKR03), ("SKR04", SKR04)):
            for nr, bez, art in konten:
                Konto.objects.get_or_create(kontenrahmen=rahmen, nummer=nr,
                                            defaults={"bezeichnung": bez, "kontoart": art})
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
        self.stdout.write(self.style.SUCCESS(
            f"Grunddaten bereit: {Modul.objects.count()} Module, {Konto.objects.count()} Konten, "
            f"{Nummernkreis.objects.count()} Nummernkreise, {Rolle.objects.count()} Rollen."))

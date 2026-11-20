"""Demodaten für Test und Präsentation (idempotent). NICHT im Produktivsystem ausführen!

Demo-Zugänge (Passwort für alle Fachbenutzer: Demo!2026, Administrator: Admin!2026):
admin, einkauf, vertrieb, produktion, meister, terminal, lager, buchhaltung, gast
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.models import Rolle

BENUTZER = [
    # Benutzername, Vorname, Nachname, Rolle, Superuser
    ("admin", "Anna", "Admin", "Administrator", True),
    ("einkauf", "Erik", "Einkäufer", "Einkauf", False),
    ("vertrieb", "Vera", "Vertrieb", "Vertrieb", False),
    ("produktion", "Paul", "Produktion", "Produktion", False),
    ("meister", "Martin", "Meister", "Fertigungsleitung", False),
    ("terminal", "Stempel", "Terminal", "Terminal", False),
    ("lager", "Lars", "Lager", "Lager", False),
    ("buchhaltung", "Berta", "Buchhaltung", "Buchhaltung", False),
    ("gast", "Gerd", "Gast", "Nur Lesen", False),
]
ADMIN_PASSWORT = "Admin!2026"
DEMO_PASSWORT = "Demo!2026"

ARTIKEL = [
    # Nr, Bezeichnung, Art, Einheit, Charge, EK, VK
    ("R-1000", "Stahlblech DC01 1,5 mm", "ROHSTOFF", "kg", True, "1.15", "0"),
    ("R-1010", "Rundstahl C45 Ø 20 mm", "ROHSTOFF", "m", True, "4.80", "0"),
    ("R-1020", "Sechskantschraube M8×20 DIN 933 verzinkt", "ROHSTOFF", "Stk", False, "0.06", "0"),
    ("H-2000", "Blechzuschnitt 200×300 mm", "HALBZEUG", "Stk", False, "0", "0"),
    ("H-2010", "Welle gedreht Ø 18 mm, L = 250 mm", "HALBZEUG", "Stk", False, "0", "0"),
    ("W-3000", "Stanzwerkzeug ST-01 Lagerbock", "WERKZEUG", "Stk", False, "8500", "0"),
    ("B-3100", "Schweißdraht G3Si1 Ø 1,0 mm", "BETRIEBSMITTEL", "kg", True, "3.20", "0"),
    ("F-5000", "Lagerbock LB-200", "FERTIGARTIKEL", "Stk", False, "0", "48.50"),
    ("F-5010", "Antriebseinheit AE-1", "FERTIGARTIKEL", "Stk", False, "0", "189.00"),
]

PARTNER = [
    # Name, Kunde, Lieferant, USt-ID, Straße, PLZ, Ort, Zahlungsbedingung
    ("Müller Maschinenbau GmbH", True, False, "DE811234567", "Hauptstraße 5", "73728", "Esslingen", "10S2_30N"),
    ("Autoteile Weber AG", True, False, "DE298765432", "Leopoldstraße 120", "80804", "München", "30N"),
    ("Stahlhandel Schmidt GmbH", False, True, "DE119876543", "Hafenstraße 40", "47119", "Duisburg", "30N"),
    ("Normteile Becker KG", False, True, "", "Schraubenweg 3", "74653", "Künzelsau", "14N"),
]


class Command(BaseCommand):
    help = "Legt Demodaten an (Benutzer, Artikel, Partner, Stücklisten, Arbeitsplan, Beispielbelege)."

    def handle(self, *args, **options):
        call_command("erp_grunddaten")
        with transaction.atomic():
            self._benutzer()
            artikel = self._artikel()
            partner = self._partner()
            self._stuecklisten(artikel)
        self._fibu(partner)
        self._belege(artikel, partner)
        self._lager(artikel, partner)
        self._einkauf(artikel, partner)
        self._produktion(artikel)
        self._fibu_nach(partner)
        self.stdout.write(self.style.SUCCESS(
            f"Demodaten bereit. Logins: admin / {ADMIN_PASSWORT}; einkauf, vertrieb, produktion, meister, terminal, lager, "
            f"buchhaltung, gast / {DEMO_PASSWORT}"))

    def _benutzer(self):
        User = get_user_model()
        for name, vor, nach, rolle, superuser in BENUTZER:
            user, angelegt = User.objects.get_or_create(username=name, defaults={
                "first_name": vor, "last_name": nach, "email": f"{name}@musterwerk.example",
                "is_superuser": superuser, "is_staff": superuser})
            if angelegt:
                user.set_password(ADMIN_PASSWORT if superuser else DEMO_PASSWORT)
                user.save()
            Rolle.objects.get(name=rolle).benutzer.add(user)

    def _artikel(self):
        from apps.stammdaten.models import Artikel, Einheit, Steuersatz

        ust19 = Steuersatz.objects.get(code="UST19")
        ergebnis = {}
        for nr, bez, art, einheit, charge, ek, vk in ARTIKEL:
            ergebnis[nr], _ = Artikel.objects.get_or_create(artikelnummer=nr, defaults={
                "bezeichnung": bez, "art": art, "einheit": Einheit.objects.get(code=einheit),
                "chargenpflicht": charge, "steuersatz": ust19, "einkaufspreis": Decimal(ek),
                "verkaufspreis": Decimal(vk)})
        return ergebnis

    def _partner(self):
        from apps.stammdaten.models import Adresse, Ansprechpartner, Geschaeftspartner, Zahlungsbedingung

        ergebnis = {}
        for name, kunde, lieferant, ustid, strasse, plz, ort, zb in PARTNER:
            p = Geschaeftspartner.objects.filter(name=name).first()
            if not p:
                p = Geschaeftspartner.objects.create(
                    name=name, ist_kunde=kunde, ist_lieferant=lieferant, ust_id=ustid,
                    zahlungsbedingung=Zahlungsbedingung.objects.get(code=zb),
                    email=f"info@{name.split()[0].lower()}.example")
                Adresse.objects.create(partner=p, typ="HAUPT", strasse=strasse, plz=plz, ort=ort)
                Ansprechpartner.objects.create(partner=p, anrede="Frau", vorname="Sabine", nachname="Beispiel",
                                               funktion="Einkauf" if kunde else "Vertrieb")
            ergebnis[name] = p
        return ergebnis

    def _stuecklisten(self, a):
        from apps.stammdaten.models import Arbeitsgang, Arbeitsplan, Stueckliste, StuecklistenPosition

        struktur = {
            "H-2000": ("Zuschnitt", [("R-1000", "0.720")]),
            "H-2010": ("Welle", [("R-1010", "0.260")]),
            "F-5000": ("Lagerbock komplett", [("H-2000", "2"), ("R-1020", "4")]),
            "F-5010": ("Antriebseinheit", [("F-5000", "1"), ("H-2010", "1"), ("R-1020", "2")]),
        }
        for nr, (bez, positionen) in struktur.items():
            sl, angelegt = Stueckliste.objects.get_or_create(artikel=a[nr], version=1, defaults={"bezeichnung": bez})
            if angelegt:
                for i, (komp, menge) in enumerate(positionen, start=1):
                    StuecklistenPosition.objects.create(stueckliste=sl, position=i * 10, komponente=a[komp],
                                                        menge=Decimal(menge))
        self._arbeitsplaene(a)

    def _arbeitsplaene(self, a):
        """Arbeitsplätze und Arbeitspläne (Fertigartikel + Halbzeuge). Bestehende Pläne werden nur angepasst,
        solange es noch keine Fertigungsaufträge gibt."""
        from apps.produktion.models import Fertigungsauftrag
        from apps.stammdaten.models import Arbeitsgang, Arbeitsplan, Arbeitsplatz, Lohnart

        plaetze = {}
        for code, bez, art, kst, satz in [
            ("TS-2", "Tafelschere TS-2", "MASCHINE", "4100", "45.00"),
            ("EP-63", "Exzenterpresse EP-63", "MASCHINE", "4200", "65.00"),
            ("SP-1", "Schweißplatz SP-1", "HAND", "4300", "38.00"),
            ("MP-3", "Montageplatz MP-3", "HAND", "4400", "25.00"),
            ("DR-1", "CNC-Drehmaschine DR-1", "MASCHINE", "4500", "55.00"),
            ("EG-1", "Entgratplatz EG-1", "HAND", "4100", "22.00"),
        ]:
            ap, _ = Arbeitsplatz.objects.get_or_create(code=code, defaults={"bezeichnung": bez})
            if not ap.stundensatz:
                ap.bezeichnung, ap.art, ap.kostenstelle, ap.stundensatz = bez, art, kst, Decimal(satz)
                ap.save()
            plaetze[code] = ap
        if Fertigungsauftrag.objects.exists():
            return
        A, Z = Lohnart.AKKORD, Lohnart.ZEITLOHN
        plaene = {
            "F-5000": ("Standardfertigung", [
                (10, "Bereitstellen & Ausrichten", "MP-3", "5", "0.30", "LG4", A),
                (20, "Stanzen (Werkzeug W-3000)", "EP-63", "30", "0.35", "LG5", A),
                (30, "Schweißen", "SP-1", "10", "2.50", "LG5", A),
                (40, "Montage & Prüfung", "MP-3", "5", "1.20", "LG6", Z)]),
            "H-2000": ("Zuschnitt", [
                (10, "Zuschneiden", "TS-2", "10", "0.40", "LG4", A),
                (20, "Entgraten", "EG-1", "0", "0.25", "LG4", A)]),
            "H-2010": ("Welle drehen", [
                (10, "Drehen", "DR-1", "20", "3.00", "LG6", A),
                (20, "Maßprüfung", "MP-3", "0", "0.50", "LG6", Z)]),
            "F-5010": ("Endmontage", [
                (10, "Endmontage", "MP-3", "15", "6.00", "LG5", A)]),
        }
        for nr, (bez, gaenge) in plaene.items():
            plan, _ = Arbeitsplan.objects.get_or_create(artikel=a[nr], version=1, defaults={"bezeichnung": bez})
            plan.arbeitsgaenge.exclude(position__in=[g[0] for g in gaenge]).delete()
            for pos, gbez, platz, ruest, stueck, lg, lohnart in gaenge:
                Arbeitsgang.objects.update_or_create(arbeitsplan=plan, position=pos, defaults={
                    "bezeichnung": gbez, "arbeitsplatz": plaetze[platz], "ruestzeit_min": Decimal(ruest),
                    "stueckzeit_min": Decimal(stueck), "lohngruppe": lg, "lohnart": lohnart})

    def _belege(self, a, p):
        from apps.belege import services
        from apps.belege.models import Beleg, BelegPosition

        if Beleg.objects.exists():
            return
        admin = get_user_model().objects.get(username="admin")
        kunde = p["Müller Maschinenbau GmbH"]

        def anlegen(art, partner, betreff, positionen, datum=None):
            b = Beleg.objects.create(belegart=art, partner=partner, betreff=betreff, erstellt_von=admin,
                                     zahlungsbedingung=partner.zahlungsbedingung,
                                     **({"belegdatum": datum} if datum else {}))
            for i, (nr, menge, preis) in enumerate(positionen, start=1):
                art_obj = a[nr]
                BelegPosition.objects.create(beleg=b, position=i * 10, artikel=art_obj, menge=Decimal(menge),
                                             einzelpreis=Decimal(preis or art_obj.verkaufspreis or art_obj.einkaufspreis))
            b.berechne_summen()
            b.save()
            return b

        def rechnung(partner, betreff, positionen, datum):
            """Ausgangsrechnung freigeben und festschreiben → Fibu-Automatik bucht Debitor an Erlöse/USt."""
            services.festschreiben(services.freigeben(anlegen("RECHNUNG", partner, betreff, positionen, datum),
                                                      admin), admin)

        weber = p["Autoteile Weber AG"]
        anlegen("ANGEBOT", weber, "Rahmenangebot Antriebseinheiten",
                [("F-5010", "250", None), ("F-5000", "500", "46.90")])
        # Ausgangsrechnungen 2026 (chronologisch, damit die Nummern zu den Demo-Kontoauszügen passen):
        rechnung(weber, "Antriebseinheiten Juni", [("F-5010", "30", None)], date(2026, 6, 15))  # RE-2026-00001 bezahlt
        rechnung(weber, "Antriebseinheiten August", [("F-5010", "40", None)], date(2026, 8, 14))  # 00002 überfällig
        rechnung(kunde, "Lagerböcke September", [("F-5000", "200", None)], date(2026, 9, 10))  # 00003 Skonto bezahlt
        rechnung(weber, "Lagerböcke Oktober", [("F-5000", "100", "46.90")], date(2026, 10, 2))  # 00004 Teilzahlung
        rechnung(kunde, "Lieferung KW 46", [("F-5000", "120", None), ("F-5010", "10", None)],
                 date(2026, 11, 16))  # RE-2026-00005 → Zahlung im CAMT-Demoauszug
        anlegen("BESTELLUNG", p["Stahlhandel Schmidt GmbH"], "Blechbestellung", [("R-1000", "2000", "1.15")])

    # ------------------------------------------------------------ Phase 4: Finanzbuchhaltung

    IBAN = {"Müller Maschinenbau GmbH": "DE89370400440532013000", "Autoteile Weber AG": "DE12500105170648489890",
            "Stahlhandel Schmidt GmbH": "DE75512108001245126199", "Normteile Becker KG": "DE02100100100006820101"}

    def _fibu(self, p):
        """Geschäftsjahr(e), Hausbank, Partner-IBANs und Eröffnungsbilanz 01.01.2026 (idempotent)."""
        from django.utils import timezone

        from apps.fibu import services as S
        from apps.fibu.models import Bankkonto

        for jahr in sorted({2026, timezone.localdate().year}):
            S.geschaeftsjahr_anlegen(jahr)
        Bankkonto.objects.get_or_create(iban="DE02120300000000202051", defaults={
            "bezeichnung": "Hausbank (Girokonto)", "bic": "BYLADEM1001", "konto": S.konto("1200")})
        for name, iban in self.IBAN.items():
            if name in p and not p[name].iban:
                p[name].iban = iban
                p[name].save(update_fields=["iban"])
        admin = get_user_model().objects.get(username="admin")
        # Eröffnungsbilanz (Werte passend zu den Lager-Anfangsbeständen); idempotent über quelle/quelle_id
        S.buchen(date(2026, 1, 1), "EB2026", "Eröffnungsbilanz 01.01.2026", [
            {"konto": "1200", "soll": "85000.00", "text": "Bank"},
            {"konto": "0210", "soll": "120000.00", "text": "Maschinen"},
            {"konto": "3970", "soll": "12933.00", "text": "Roh-, Hilfs- und Betriebsstoffe"},
            {"konto": "7110", "soll": "9612.00", "text": "Fertige Erzeugnisse"},
            {"konto": "0800", "haben": "50000.00", "text": "Gezeichnetes Kapital"},
            {"konto": "0630", "haben": "80000.00", "text": "Bankdarlehen"},
            {"konto": "0860", "haben": "97545.00", "text": "Gewinnvortrag"},
        ], quelle="demo_eb", quelle_id="2026", benutzer=admin)

    def _fibu_nach(self, p):
        """Eingangsrechnungen, Mahnhistorie und MT940-Kontoauszug Juli–November (nur einmalig).

        Der CAMT.053-Auszug docs/beispiele/camt053_demo.xml bleibt für den Demo-Ablauf in der Oberfläche übrig.
        """
        from pathlib import Path

        from django.conf import settings

        from apps.fibu import bank as B
        from apps.fibu import mahnwesen as MW
        from apps.fibu import services as S
        from apps.fibu.models import Bankimport, Bankumsatz, Eingangsrechnung, OffenerPosten, Periode
        from apps.wareneingang.models import Wareneingang

        if Eingangsrechnung.objects.exists() or Bankimport.objects.exists():
            return
        admin = get_user_model().objects.get(username="buchhaltung")
        schmidt, becker = p["Stahlhandel Schmidt GmbH"], p["Normteile Becker KG"]
        with transaction.atomic():
            # Wareneingangs-Events laufen per on_commit – hier sicherheitshalber idempotent nachbuchen
            for we in Wareneingang.objects.all():
                S.we_buchen(we, admin)

            def er_ohne_bestellung(lieferant, rnr, datum, positionen):
                er = Eingangsrechnung.objects.create(
                    nummer=S.naechste_nummer("FIBU_ER", S.timezone.localdate()), lieferant=lieferant,
                    rechnungsnummer=rnr, rechnungsdatum=datum, eingangsdatum=datum,
                    zahlungsbedingung=lieferant.zahlungsbedingung, erfasst_von=admin)
                for i, (bez, menge, preis) in enumerate(positionen, start=1):
                    er.positionen.create(position=i * 10, bezeichnung=bez, menge=Decimal(menge),
                                         einzelpreis=Decimal(preis), steuerschluessel=S.steuerschluessel("V19"))
                S.er_buchen(er, admin)
                return er

            er_ohne_bestellung(becker, "NB-26-0912", date(2026, 9, 15),
                               [("Normteile-Sortiment Instandhaltung", "1", "1250.00")])
            er_ohne_bestellung(schmidt, "SH-RE-2026-3980", date(2026, 10, 20),
                               [("Lohnzuschnitt Stahlbleche (Fremdbearbeitung)", "1", "6400.00")])
            # Rechnung zum Wareneingang: erfasst, Prüfung/Buchung erfolgt im Demo-Ablauf
            we = Wareneingang.objects.order_by("pk").first()
            if we:
                S.er_aus_wareneingang(we, "SH-RE-2026-4471", we.datum, admin)
            # Kontoauszug Juli–November importieren und abarbeiten
            datei = Path(settings.BASE_DIR) / "docs" / "beispiele" / "mt940_demo.sta"
            if datei.exists():
                imp = B.importieren(datei.read_bytes(), datei.name, admin)
                umsaetze = list(imp.umsaetze.order_by("buchungsdatum", "pk"))
                B.automatisch_zuordnen(umsaetze, admin)
                for u in Bankumsatz.objects.filter(bankimport=imp, status=Bankumsatz.Status.OFFEN):
                    zweck = u.verwendungszweck
                    if "Lohn/Gehalt" in zweck:
                        B.auf_konto_buchen(u, "4120", admin, text="Löhne und Gehälter Oktober 2026 (Sammelbuchung)")
                    elif "Miete" in zweck:
                        B.auf_konto_buchen(u, "4210", admin, steuerschluessel="V19", text="Miete Halle 3 11/2026")
                    elif "Teilzahlung" in zweck:
                        op = OffenerPosten.objects.filter(art="D", belegnr="RE-2026-00004", offen__gt=0).first()
                        if op:
                            B.zuordnen(u, [op], admin)
            # Mahnhistorie: Zahlungserinnerung Ende September (nach Bankabgleich) für RE-2026-00002
            try:
                MW.ausfuehren(MW.vorschlag(date(2026, 9, 25), admin), admin)
            except S.FibuFehler:
                pass
            # Perioden Januar–September abgeschlossen: Buchungen festgeschrieben (GoBD) und gesperrt
            for periode in Periode.objects.filter(geschaeftsjahr__jahr=2026, nummer__lte=9, gesperrt=False):
                S.periode_sperren(periode, admin)
    # ------------------------------------------------------------ Phase 2: Warenfluss

    def _lager(self, a, p):
        """Lagerorte, Dispositionsdaten und Anfangsbestände (nur wenn noch kein Lagerort existiert)."""
        from apps.lager import services as lager
        from apps.lager.models import ArtikelLager, Bestandsart, Lagerbewegung, Lagerort, Lagerplatz

        if Lagerort.objects.exists():
            return
        admin = get_user_model().objects.get(username="admin")
        with transaction.atomic():
            rl = Lagerort.objects.create(code="RL", bezeichnung="Rohstofflager", beschreibung="Halle 1, Regalanlage")
            fl = Lagerort.objects.create(code="FL", bezeichnung="Fertigwarenlager", beschreibung="Halle 3, Versand")
            Lagerort.objects.create(code="WE", bezeichnung="Wareneingang / QS-Zone", ist_wareneingang=True)
            plaetze = {c: Lagerplatz.objects.create(lagerort=rl, code=c, bezeichnung=b) for c, b in [
                ("RL-01", "Blechregal"), ("RL-02", "Langgutregal"), ("RL-03", "Kleinteile / Betriebsmittel")]}
            Lagerplatz.objects.create(lagerort=fl, code="FL-01", bezeichnung="Palettenplatz 1")
            schmidt, becker = p["Stahlhandel Schmidt GmbH"], p["Normteile Becker KG"]
            dispo = {
                # Nr: (Mindest, Melde, Bestellmenge, WBZ, Lieferant, Lagerort, QS)
                "R-1000": ("500", "2000", "3000", 10, schmidt, rl, True),
                "R-1010": ("200", "1000", "500", 10, schmidt, rl, False),
                "R-1020": ("2000", "5000", "10000", 5, becker, rl, False),
                "B-3100": ("20", "50", "100", 7, schmidt, rl, True),
                "W-3000": ("0", "0", "0", 60, None, rl, False),
                "F-5000": ("50", "100", "0", 0, None, fl, False),
                "F-5010": ("5", "10", "0", 0, None, fl, False),
            }
            for nr, (mindest, melde, menge, wbz, lief, ort, qs) in dispo.items():
                ArtikelLager.objects.update_or_create(artikel=a[nr], defaults={
                    "mindestbestand": Decimal(mindest), "meldebestand": Decimal(melde),
                    "bestellmenge": Decimal(menge), "wiederbeschaffungszeit_tage": wbz,
                    "bevorzugter_lieferant": lief, "standard_lagerort": ort, "qs_pflicht": qs})
            frei = Bestandsart.FREI
            anfang = [
                # Nr, Menge, Preis, Ort, Platz, Charge
                ("R-1000", "1800", "1.10", rl, plaetze["RL-01"], "CH-2026-0001"),
                ("R-1010", "420", "4.75", rl, plaetze["RL-02"], "CH-2026-0002"),
                ("R-1020", "3500", "0.06", rl, plaetze["RL-03"], None),
                ("B-3100", "80", "3.10", rl, plaetze["RL-03"], "CH-2026-0003"),
                ("W-3000", "1", "8500", rl, None, None),
                ("F-5000", "240", "31.20", fl, None, None),
                ("F-5010", "18", "118.00", fl, None, None),
            ]
            for nr, menge, preis, ort, platz, charge_nr in anfang:
                charge = lager.charge_holen(a[nr], charge_nr, anlegen=True, lieferant=schmidt) if charge_nr else None
                lager.buchen(Lagerbewegung.Art.ZUGANG, a[nr], Decimal(menge), nach=(ort, platz, frei), charge=charge,
                             preis=Decimal(preis), benutzer=admin, quelle="anfangsbestand",
                             grund="Anfangsbestand Datenübernahme")
            # Ein Entnahme-Beispiel für das Journal
            lager.buchen(Lagerbewegung.Art.ABGANG, a["R-1020"], Decimal("400"),
                         von=(rl, plaetze["RL-03"], frei), benutzer=admin, grund="Entnahme Montage (Demo)")

    def _einkauf(self, a, p):
        """Beispielbestellungen inkl. Teil-Wareneingang mit Charge in QS-Sperre (nur wenn noch keine existieren)."""
        from datetime import timedelta

        from django.utils import timezone

        from apps.einkauf import services as ek
        from apps.einkauf.models import Bestellung, BestellPosition
        from apps.lager.models import Lagerort
        from apps.wareneingang import services as we_services

        if Bestellung.objects.exists():
            return
        User = get_user_model()
        einkauf, lager_user = User.objects.get(username="einkauf"), User.objects.get(username="lager")
        heute = timezone.localdate()
        rl, we_ort = Lagerort.objects.get(code="RL"), Lagerort.objects.get(code="WE")
        schmidt, becker = p["Stahlhandel Schmidt GmbH"], p["Normteile Becker KG"]

        def anlegen(lieferant, positionen, typ=Bestellung.Typ.BESTELLUNG, termin=14, **kopf):
            b = Bestellung.objects.create(typ=typ, lieferant=lieferant, zahlungsbedingung=lieferant.zahlungsbedingung,
                                          lieferort=rl, liefertermin=heute + timedelta(days=termin),
                                          erstellt_von=einkauf, **kopf)
            for i, (nr, menge, preis) in enumerate(positionen, start=1):
                ek.position_vorbelegen(BestellPosition(
                    bestellung=b, position=i * 10, artikel=a[nr], menge=Decimal(menge),
                    einzelpreis=Decimal(preis) if preis else None, steuersatz=None,
                    liefertermin=heute + timedelta(days=termin))).save()
            b.berechne_summen()
            b.save()
            return b

        with transaction.atomic():
            # 1) Bestellung an Schmidt, freigegeben, teilgeliefert (R-1000 mit Charge in QS-Sperre)
            b1 = anlegen(schmidt, [("R-1000", "3000", "1.15"), ("R-1010", "500", "4.80")], termin=-2,
                         ihre_referenz="Angebot 2026-417", kopftext="Wir bestellen zu den vereinbarten Konditionen:")
            b1 = ek.freigeben(b1, einkauf)
            p1, p2 = b1.positionen.order_by("position")
            we_services.buchen(b1, [
                {"bestellposition": p1, "menge": Decimal("1200"), "chargennummer": "CH-2026-0101",
                 "lieferantencharge": "SH-88231", "herstelldatum": heute - timedelta(days=12),
                 "lieferpreis": Decimal("1.18")},
                {"bestellposition": p2, "menge": Decimal("500"), "chargennummer": "CH-2026-0102",
                 "lieferantencharge": "SH-88240"},
            ], lieferschein_nr="LS-448812", lagerort=we_ort, benutzer=lager_user,
                bemerkung="Teillieferung, Rest laut Lieferant in KW 49")
            # 2) Freigegebene Bestellung an Becker (offen, Termin in einer Woche)
            ek.freigeben(anlegen(becker, [("R-1020", "10000", "0.055")], termin=7), einkauf)
            # 3) Bestellentwurf Schweißdraht
            anlegen(schmidt, [("B-3100", "100", None)], termin=21)
            # 4) Anfrage Werkzeug
            ek.freigeben(anlegen(becker, [("W-3000", "1", "0")], typ=Bestellung.Typ.ANFRAGE, termin=60,
                                 kopftext="Bitte unterbreiten Sie uns ein Angebot für folgendes Werkzeug:"),
                         einkauf)
            ek.banf_anlegen(a["B-3100"], Decimal("50"), heute + timedelta(days=10),
                            "Mehrbedarf Schweißerei Auftrag Weber", User.objects.get(username="produktion"))

    def _produktion(self, a):
        """Mitarbeiter, Fertigungsaufträge mit Stempelungen/Rückmeldungen der letzten Werktage (nur einmalig)."""
        from datetime import datetime, time, timedelta

        from django.utils import timezone

        from apps.lager.models import Bestand, Lagerort
        from apps.produktion import services as fs
        from apps.produktion.models import Fertigungsauftrag, Stempelung
        from apps.stammdaten.models import Mitarbeiter

        User = get_user_model()
        prod = User.objects.get(username="produktion")
        mitarbeiter = {}
        for nr, ausweis, vor, nach, lg, lohn, richt, user in [
            ("P1001", "0001001", "Klaus", "Becker", "LG5", "17.50", "19.25", prod),
            ("P1002", "0001002", "Ayşe", "Yılmaz", "LG5", "17.50", "19.25", None),
            ("P1003", "0001003", "Jan", "Nowak", "LG4", "13.50", "16.00", None),
            ("P1004", "0001004", "Sabine", "Krüger", "LG6", "19.00", "21.00", None),
            ("P1005", "0001005", "Tobias", "Wagner", "LG4", "15.50", "17.00", None),
        ]:
            ma, _ = Mitarbeiter.objects.get_or_create(personalnummer=nr, defaults={
                "ausweisnummer": ausweis, "vorname": vor, "nachname": nach, "lohngruppe": lg,
                "stundenlohn": Decimal(lohn), "akkordrichtsatz": Decimal(richt), "benutzer": user})
            mitarbeiter[nr] = ma
        if Fertigungsauftrag.objects.exists():
            return
        jetzt = timezone.localtime()
        heute = jetzt.date()
        tage = []  # die letzten vier Werktage vor heute
        tag = heute
        while len(tage) < 4:
            tag -= timedelta(days=1)
            if tag.weekday() < 5:
                tage.insert(0, tag)
        tz = timezone.get_current_timezone()

        def t(tag_idx, hhmm):
            h, m = map(int, hhmm.split(":"))
            return timezone.make_aware(datetime.combine(tage[tag_idx], time(h, m)), tz)

        def schicht(nr, tag_idx, von="06:00", bis="14:30"):
            fs.kommen(mitarbeiter[nr], t(tag_idx, von))
            return lambda: fs.gehen(mitarbeiter[nr], t(tag_idx, bis))

        def arbeit(nr, ag, tag_idx, von, bis, gut=0, ausschuss=0, grund="", ruesten=False, ende=False):
            ma = mitarbeiter[nr]
            fs.stempel_start(ma, ag, Stempelung.Taetigkeit.RUESTEN if ruesten else Stempelung.Taetigkeit.FERTIGEN,
                             zeit=t(tag_idx, von))
            fs.stempel_beenden(ma, Stempelung.EndeArt.ENDE if ende else Stempelung.EndeArt.UNTERBRECHUNG,
                               zeit=t(tag_idx, bis), gut=gut, ausschuss=ausschuss, grund=grund, benutzer=prod)

        def ag(fa, pos):
            return fa.arbeitsgaenge.get(position=pos)

        fl = Lagerort.objects.get(code="FL")
        with transaction.atomic():
            # FA 1: Halbzeug H-2000 (200 Stk) – komplett gefertigt und eingelagert
            fa1 = fs.fa_anlegen(a["H-2000"], 200, starttermin=tage[0], termin=tage[0], ziel_lagerort=fl,
                                bemerkung="Zuschnitte für Lagerbock-Serie", benutzer=prod)
            fa1, _ = fs.freigeben(fa1, prod)
            blech = Bestand.objects.filter(artikel=a["R-1000"], charge__chargennummer="CH-2026-0001",
                                           bestandsart="FREI").first()
            fs.material_entnehmen(fa1, fa1.materialien.get(), fa1.materialien.get().soll_menge, bestand=blech,
                                  benutzer=prod)
            g3, g5 = schicht("P1003", 0), schicht("P1005", 0)
            arbeit("P1003", ag(fa1, 10), 0, "06:10", "06:22", ruesten=True)
            arbeit("P1003", ag(fa1, 10), 0, "06:22", "07:20", gut=96, ausschuss=2, grund="MASS")
            arbeit("P1003", ag(fa1, 10), 0, "07:35", "08:15", gut=104, ende=True)
            arbeit("P1005", ag(fa1, 20), 0, "09:10", "09:52", gut=200, ende=True)
            g3()
            g5()
            fs.fertigmelden(fa1, 200, lagerort=fl, abschliessen=True, benutzer=prod)

            # FA 2: Lagerbock F-5000 (100 Stk) – in Arbeit, teilweise fertig gemeldet
            fa2 = fs.fa_anlegen(a["F-5000"], 100, starttermin=tage[1], termin=heute + timedelta(days=3),
                                bemerkung="Kundenauftrag Müller Maschinenbau", benutzer=prod)
            fa2, _ = fs.freigeben(fa2, prod)
            fs.restmaterial_entnehmen(fa2, prod)
            g5, g1 = schicht("P1005", 1), schicht("P1001", 1)
            arbeit("P1005", ag(fa2, 10), 1, "06:05", "06:35", gut=100, ende=True)
            arbeit("P1001", ag(fa2, 20), 1, "06:45", "07:13", ruesten=True)
            arbeit("P1001", ag(fa2, 20), 1, "07:13", "07:45", gut=99, ausschuss=1, grund="EINRICHTEN", ende=True)
            g5()
            g1()
            g2 = schicht("P1002", 2)
            arbeit("P1002", ag(fa2, 30), 2, "06:00", "06:09", ruesten=True)
            arbeit("P1002", ag(fa2, 30), 2, "06:09", "07:15", gut=30)
            arbeit("P1002", ag(fa2, 30), 2, "07:30", "08:40", gut=28, ausschuss=0)
            g2()
            g2, g4 = schicht("P1002", 3), schicht("P1004", 3)
            arbeit("P1002", ag(fa2, 30), 3, "06:00", "07:05", gut=26, ausschuss=1, grund="BEARBEITUNG")
            arbeit("P1004", ag(fa2, 40), 3, "08:00", "09:05", gut=40)
            g2()
            fs.fertigmelden(fa2, 40, lagerort=fl, benutzer=prod)

            # FA 3: Welle H-2010 (20 Stk) – Drehen läuft, Nacharbeit gemeldet
            fa3 = fs.fa_anlegen(a["H-2010"], 20, starttermin=tage[3], termin=heute + timedelta(days=5),
                                benutzer=prod)
            fa3, _ = fs.freigeben(fa3, prod)
            rund = Bestand.objects.filter(artikel=a["R-1010"], bestandsart="FREI", menge__gt=0).order_by("pk").first()
            if rund:
                m = fa3.materialien.get()
                fs.material_entnehmen(fa3, m, m.soll_menge, bestand=rund, benutzer=prod)
            arbeit("P1004", ag(fa3, 10), 3, "09:20", "09:45", ruesten=True)
            arbeit("P1004", ag(fa3, 10), 3, "09:45", "10:20", gut=12)
            g4()
            fs.rueckmelden(ag(fa3, 10), 0, 0, "", 1, mitarbeiter=mitarbeiter["P1004"], benutzer=prod,
                           bemerkung="Durchmesser an Oberkante – nacharbeiten", zeitpunkt=t(3, "10:21"))

            # FA 4: Antriebseinheit F-5010 – Entwurf (zeigt Fehlteile/Unteraufträge)
            fs.fa_anlegen(a["F-5010"], 20, termin=heute + timedelta(days=14), benutzer=prod,
                          bemerkung="Rahmenauftrag Autoteile Weber")

            # heute: zwei Mitarbeiter angemeldet, einer am Arbeitsgang angestempelt
            beginn = jetzt - timedelta(minutes=40)
            fs.kommen(mitarbeiter["P1002"], beginn - timedelta(minutes=20))
            fs.stempel_start(mitarbeiter["P1002"], ag(fa2, 30), zeit=beginn)
            fs.kommen(mitarbeiter["P1004"], beginn - timedelta(minutes=10))

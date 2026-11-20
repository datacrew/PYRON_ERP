"""Demodaten für Test und Präsentation (idempotent). NICHT im Produktivsystem ausführen!

Demo-Zugänge (Passwort für alle Fachbenutzer: Demo!2026, Administrator: Admin!2026):
admin, einkauf, vertrieb, produktion, lager, buchhaltung, gast
"""
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
        self._belege(artikel, partner)
        self._lager(artikel, partner)
        self._einkauf(artikel, partner)
        self.stdout.write(self.style.SUCCESS(
            f"Demodaten bereit. Logins: admin / {ADMIN_PASSWORT}; einkauf, vertrieb, produktion, lager, "
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
        plan, angelegt = Arbeitsplan.objects.get_or_create(artikel=a["F-5000"], version=1,
                                                          defaults={"bezeichnung": "Standardfertigung"})
        if angelegt:
            for pos, bez, platz, ruest, stueck in [
                (10, "Zuschneiden", "Tafelschere TS-2", "15", "0.80"),
                (20, "Stanzen", "Exzenterpresse EP-63 (Werkzeug W-3000)", "30", "0.35"),
                (30, "Schweißen", "Schweißplatz SP-1", "10", "2.50"),
                (40, "Montage & Prüfung", "Montageplatz MP-3", "5", "1.20"),
            ]:
                Arbeitsgang.objects.create(arbeitsplan=plan, position=pos, bezeichnung=bez, arbeitsplatz=platz,
                                           ruestzeit_min=Decimal(ruest), stueckzeit_min=Decimal(stueck),
                                           lohngruppe="LG5")

    def _belege(self, a, p):
        from apps.belege import services
        from apps.belege.models import Beleg, BelegPosition

        if Beleg.objects.exists():
            return
        admin = get_user_model().objects.get(username="admin")
        kunde = p["Müller Maschinenbau GmbH"]

        def anlegen(art, partner, betreff, positionen):
            b = Beleg.objects.create(belegart=art, partner=partner, betreff=betreff, erstellt_von=admin,
                                     zahlungsbedingung=partner.zahlungsbedingung)
            for i, (nr, menge, preis) in enumerate(positionen, start=1):
                art_obj = a[nr]
                BelegPosition.objects.create(beleg=b, position=i * 10, artikel=art_obj, menge=Decimal(menge),
                                             einzelpreis=Decimal(preis or art_obj.verkaufspreis or art_obj.einkaufspreis))
            b.berechne_summen()
            b.save()
            return b

        anlegen("ANGEBOT", p["Autoteile Weber AG"], "Rahmenangebot Antriebseinheiten",
                [("F-5010", "250", None), ("F-5000", "500", "46.90")])
        rechnung = anlegen("RECHNUNG", kunde, "Lieferung KW 46", [("F-5000", "120", None), ("F-5010", "10", None)])
        rechnung = services.freigeben(rechnung, admin)
        services.festschreiben(rechnung, admin)
        anlegen("BESTELLUNG", p["Stahlhandel Schmidt GmbH"], "Blechbestellung", [("R-1000", "2000", "1.15")])

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

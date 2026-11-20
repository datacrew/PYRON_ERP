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

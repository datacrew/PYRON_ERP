"""Tests Vertrieb (Phase 5): Kernfluss Angebot → Auftrag → Lieferschein → Rechnung inkl. Lager und Fibu/OP,
Teillieferung, Preise, Verfügbarkeit, Gutschrift/Storno, ZUGFeRD/XRechnung, Shop-API, EDIFACT und Rechte."""
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import DatabaseError, transaction
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from lxml import etree

from apps.belege.models import Beleg
from apps.core.management.commands.erp_demodaten import DEMO_SHOP_TOKEN
from apps.fibu.models import Buchung, OffenerPosten
from apps.lager import services as lager
from apps.lager.models import Bestand, Bestandsart, Charge, Lagerbewegung, Lagerort
from apps.stammdaten.models import Adresse, Artikel, Einheit, Geschaeftspartner, Steuersatz

from . import edifact as ED
from . import erechnung as ER
from . import services as S
from .models import Auftrag, EdiNachricht, Lead, Lieferung, Shop, ShopBestellung

D = Decimal
ORDERS_DEMO = (Path(settings.BASE_DIR) / "docs" / "beispiele" / "orders_demo.edi").read_text(encoding="utf-8")


class Basis(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("erp_demodaten", verbosity=0)
        User = get_user_model()
        cls.admin = User.objects.get(username="admin")
        cls.vt = User.objects.get(username="vertrieb")
        cls.lg = User.objects.get(username="lager")
        cls.gast = User.objects.get(username="gast")
        cls.mueller = Geschaeftspartner.objects.get(name="Müller Maschinenbau GmbH")
        cls.weber = Geschaeftspartner.objects.get(name="Autoteile Weber AG")
        cls.f5000 = Artikel.objects.get(artikelnummer="F-5000")
        cls.f5010 = Artikel.objects.get(artikelnummer="F-5010")
        cls.fl = Lagerort.objects.get(code="FL")

    def bestand(self, artikel):
        return Bestand.objects.filter(artikel=artikel, lagerort=self.fl, bestandsart=Bestandsart.FREI).aggregate(
            s=Sum("menge"))["s"] or D(0)

    def auftrag(self, partner, positionen, bestaetigen=True, **felder):
        a = S.auftrag_anlegen(partner, [{"artikel": art, "menge": m} for art, m in positionen], self.vt, **felder)
        return S.auftrag_bestaetigen(a, self.vt) if bestaetigen else a


class KernflussTests(Basis):
    def test_lead_bis_offener_posten(self):
        lead = Lead.objects.create(firma="Testkunde Fluss GmbH", ansprechpartner="Eva Test", plz="70173",
                                   ort="Stuttgart", strasse="Königstraße 1", email="eva@fluss.example")
        kunde = S.lead_umwandeln(lead, self.vt)
        self.assertTrue(kunde.ist_kunde)
        self.assertEqual(kunde.adressen.get().plz, "70173")
        angebot = S.angebot_anlegen(kunde, [(self.f5000, 10), (self.f5010, 1)], self.vt, "Test", freigeben=True)
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.ANGEBOT)
        a = S.auftrag_bestaetigen(S.auftrag_aus_angebot(angebot, self.vt, "PO-77"), self.vt)
        self.assertEqual(a.ab_beleg.status, Beleg.Status.FESTGESCHRIEBEN)
        self.assertEqual(a.ab_beleg.belegart, Beleg.Belegart.AUFTRAG)
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.GEWONNEN)
        with self.assertRaises(S.VertriebFehler):
            S.auftrag_aus_angebot(angebot, self.vt)  # doppelte Übernahme

        vorher = self.bestand(self.f5000)
        pos = {p.artikel_id: p for p in a.positionen.all()}
        lf1 = S.warenausgang_buchen(S.lieferung_anlegen(a, {pos[self.f5000.pk].pk: 6}, self.lg), self.lg)
        self.assertEqual(lf1.status, Lieferung.Status.GEBUCHT)
        self.assertEqual(lf1.beleg.status, Beleg.Status.FESTGESCHRIEBEN)
        self.assertEqual(self.bestand(self.f5000), vorher - 6)
        self.assertTrue(Lagerbewegung.objects.filter(quelle="lieferschein", quelle_id=lf1.pk, menge=6).exists())
        a.refresh_from_db()
        self.assertEqual(a.status, Auftrag.Status.TEILGELIEFERT)

        lf2 = S.warenausgang_buchen(S.lieferung_anlegen(a, None, self.lg), self.lg)
        self.assertEqual(sum(p.menge for p in lf2.positionen.all()), D(5))  # 4 × F-5000 + 1 × F-5010
        a.refresh_from_db()
        self.assertEqual(a.status, Auftrag.Status.GELIEFERT)

        re = S.rechnung_erstellen([lf1, lf2], self.vt)  # Sammelrechnung
        self.assertEqual(re.status, Beleg.Status.FESTGESCHRIEBEN)
        self.assertEqual(re.summe_netto, a.summe_netto)
        a.refresh_from_db()
        self.assertEqual(a.status, Auftrag.Status.ABGESCHLOSSEN)
        self.assertTrue(Buchung.objects.filter(belegnr=re.nummer).exists())
        op = OffenerPosten.objects.get(beleg=re)
        self.assertEqual((op.partner, op.offen), (kunde, re.summe_brutto))
        with self.assertRaises(S.VertriebFehler):
            S.rechnung_erstellen([lf1], self.vt)  # bereits berechnet
        # GoBD: festgeschriebene Rechnung ist in der Datenbank gesperrt
        with self.assertRaises(DatabaseError), transaction.atomic():
            Beleg.objects.filter(pk=re.pk).update(summe_brutto=D("1.00"))

    def test_teillieferung_und_uebermenge(self):
        a = self.auftrag(self.mueller, [(self.f5000, 10)])
        p = a.positionen.get()
        lf = S.lieferung_anlegen(a, {p.pk: 7}, self.lg)
        with self.assertRaises(S.VertriebFehler):  # 7 bereits eingeplant, nur noch 3 offen
            S.lieferung_anlegen(a, {p.pk: 4}, self.lg)
        S.warenausgang_buchen(lf, self.lg)
        p.refresh_from_db()
        self.assertEqual((p.menge_geliefert, p.offen_liefern), (D(7), D(3)))
        with self.assertRaises(S.VertriebFehler):
            S.warenausgang_buchen(lf, self.lg)  # doppelt buchen
        unbestaetigt = self.auftrag(self.mueller, [(self.f5000, 1)], bestaetigen=False)
        with self.assertRaises(S.VertriebFehler):
            S.lieferung_anlegen(unbestaetigt, None, self.lg)

    def test_chargengenauer_abgang_fifo(self):
        art = Artikel.objects.create(artikelnummer="T-CH", bezeichnung="Chargenartikel", art="FERTIGARTIKEL",
                                     einheit=Einheit.objects.get(code="Stk"), chargenpflicht=True,
                                     steuersatz=Steuersatz.objects.get(code="UST19"), verkaufspreis=D("10"))
        from datetime import date
        for nr, tag in (("ALT-1", date(2026, 1, 1)), ("NEU-2", date(2026, 6, 1))):
            ch = Charge.objects.create(artikel=art, chargennummer=nr, herstelldatum=tag)
            lager.buchen(lager.Art.ZUGANG, art, 10, nach=(self.fl, None, Bestandsart.FREI), charge=ch, preis=D("5"))
        a = self.auftrag(self.mueller, [(art, 15)])
        lf = S.warenausgang_buchen(S.lieferung_anlegen(a, None, self.lg), self.lg)
        zeilen = sorted((lp.charge.chargennummer, lp.menge) for lp in lf.positionen.all())
        self.assertEqual(zeilen, [("ALT-1", D(10)), ("NEU-2", D(5))])
        self.assertIn("Charge ALT-1", " ".join(lf.beleg.positionen.values_list("bezeichnung", flat=True)))
        # zu wenig Bestand → nichts gebucht
        a2 = self.auftrag(self.mueller, [(art, 9)])
        lf2 = S.lieferung_anlegen(a2, None, self.lg)
        with self.assertRaises(S.VertriebFehler):
            S.warenausgang_buchen(lf2, self.lg)
        lf2.refresh_from_db()
        self.assertEqual(lf2.status, Lieferung.Status.ERFASST)

    def test_preisfindung_kundenpreis_und_staffel(self):
        self.assertEqual(S.preis_finden(self.mueller, self.f5000, 50), (D("46.00"), D("0.00")))
        self.assertEqual(S.preis_finden(self.mueller, self.f5000, 150)[0], D("44.50"))
        self.assertEqual(S.preis_finden(self.weber, self.f5010, 12), (self.f5010.verkaufspreis, D("5.00")))
        self.assertEqual(S.preis_finden(self.weber, self.f5010, 2), (self.f5010.verkaufspreis, D("0")))

    def test_verfuegbarkeit_und_fertigungsauftrag(self):
        a = self.auftrag(self.mueller, [(self.f5010, 500)], bestaetigen=False)
        z = S.verfuegbarkeit(a)[0]
        self.assertGreater(z["fehlmenge"], 0)
        fa = S.fa_aus_position(a.positionen.get(), self.vt)
        self.assertEqual(fa.artikel, self.f5010)
        self.assertEqual(a.positionen.get().fertigungsauftrag, fa)

    def test_gutschrift_und_lieferschein_storno(self):
        a = self.auftrag(self.mueller, [(self.f5000, 4)])
        lf = S.warenausgang_buchen(S.lieferung_anlegen(a, None, self.lg), self.lg)
        re = S.rechnung_erstellen([lf], self.vt)
        gs = S.gutschrift_erstellen(re, self.vt, grund="Reklamation")
        self.assertEqual((gs.belegart, gs.status), (Beleg.Belegart.GUTSCHRIFT, Beleg.Status.FESTGESCHRIEBEN))
        self.assertEqual(OffenerPosten.objects.get(beleg=gs).offen, -re.summe_brutto)
        ER.pruefen_ubl(ER.xrechnung_xml(gs), gutschrift=True)
        # ungebuchter Storno: Lagerrückbuchung
        a2 = self.auftrag(self.mueller, [(self.f5000, 3)])
        vorher = self.bestand(self.f5000)
        lf2 = S.warenausgang_buchen(S.lieferung_anlegen(a2, None, self.lg), self.lg)
        self.assertEqual(self.bestand(self.f5000), vorher - 3)
        with self.assertRaises(S.VertriebFehler):
            S.lieferung_stornieren(lf2, self.lg, "")
        S.lieferung_stornieren(lf2, self.lg, "Falsch erfasst")
        self.assertEqual(self.bestand(self.f5000), vorher)
        a2.refresh_from_db()
        self.assertEqual(a2.status, Auftrag.Status.BESTAETIGT)


class ERechnungTests(Basis):
    def _rechnung(self, partner):
        a = self.auftrag(partner, [(self.f5000, 3), (self.f5010, 1)], ihre_referenz="PO-ER")
        return S.rechnung_erstellen([S.warenausgang_buchen(S.lieferung_anlegen(a, None, self.lg), self.lg)],
                                    self.vt)

    def test_zugferd_inhalt_summen_einbettung(self):
        re = self._rechnung(self.mueller)
        pdf = ER.zugferd_rechnung(re)
        self.assertTrue(pdf.startswith(b"%PDF"))
        name, xml = ER.xml_aus_pdf(pdf)
        self.assertEqual(name, "factur-x.xml")
        ER.pruefen_cii(xml)  # XSD Factur-X EN 16931
        self.assertEqual(ER.summen_pruefen(xml), [])
        root = etree.fromstring(xml)
        ns = {"rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
              "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"}
        self.assertEqual(root.findtext(".//rsm:ExchangedDocument/ram:ID", namespaces=ns), re.nummer)
        self.assertEqual(root.findtext(".//rsm:ExchangedDocument/ram:TypeCode", namespaces=ns), "380")
        self.assertIn("en16931", root.findtext(".//ram:GuidelineSpecifiedDocumentContextParameter/ram:ID",
                                               namespaces=ns))
        self.assertEqual(D(root.findtext(".//ram:GrandTotalAmount", namespaces=ns)), re.summe_brutto)
        self.assertEqual(D(root.findtext(".//ram:TaxBasisTotalAmount", namespaces=ns)), re.summe_netto)
        self.assertEqual(len(root.findall(".//ram:IncludedSupplyChainTradeLineItem", namespaces=ns)), 2)
        ids = root.findall(".//ram:SpecifiedTaxRegistration/ram:ID", namespaces=ns)
        self.assertIn("DE811234567", [e.text for e in ids])  # USt-ID des Käufers
        import io

        import pikepdf
        with pikepdf.open(io.BytesIO(pdf)) as dok:  # PDF/A-3-Kennzeichnung (XMP) und eingebettete Datei
            self.assertEqual(str(dok.open_metadata().get("pdfaid:part")), "3")
            self.assertIn("factur-x.xml", dok.attachments)

    def test_xrechnung_ubl_mit_leitweg_id(self):
        self.weber.leitweg_id = "991-12345-67"
        self.weber.save()
        re = self._rechnung(self.weber)
        xml = ER.xrechnung_xml(re)
        ER.pruefen_ubl(xml)
        root = etree.fromstring(xml)
        cbc = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
        self.assertIn("xrechnung_3.0", root.findtext(f"{{{cbc}}}CustomizationID"))
        self.assertEqual(root.findtext(f"{{{cbc}}}BuyerReference"), "991-12345-67")
        self.assertEqual(root.findtext(f"{{{cbc}}}ID"), re.nummer)
        payable = root.find(".//{*}LegalMonetaryTotal/{*}PayableAmount")
        self.assertEqual(D(payable.text), re.summe_brutto)


class ShopApiTests(Basis):
    def setUp(self):
        self.shop = Shop.objects.get(name="Musterwerk B2B-Shop")
        self.auth = {"HTTP_AUTHORIZATION": f"Shop {DEMO_SHOP_TOKEN}"}

    def _bestellung(self, nummer="WEB-9001"):
        return {"externe_nummer": nummer,
                "kunde": {"name": "Neukunde Shop GmbH", "strasse": "Weg 1", "plz": "70376", "ort": "Stuttgart",
                          "email": "neu@shop-kunde.example"},
                "positionen": [{"artikelnummer": "F-5000", "menge": "5"}]}

    def test_token_pflicht(self):
        self.assertIn(self.client.get("/api/v1/shop/artikel/").status_code, (401, 403))
        self.assertEqual(self.client.get("/api/v1/shop/artikel/", HTTP_AUTHORIZATION="Shop falsch").status_code, 401)
        antwort = self.client.get("/api/v1/shop/artikel/", **self.auth)
        self.assertEqual(antwort.status_code, 200)
        self.assertIn("F-5000", antwort.content.decode())
        self.assertEqual(self.client.get("/api/v1/shop/bestaende/", HTTP_X_SHOP_TOKEN=DEMO_SHOP_TOKEN).status_code,
                         200)
        self.shop.aktiv = False
        self.shop.save()
        self.assertEqual(self.client.get("/api/v1/shop/artikel/", **self.auth).status_code, 401)
        # Benutzer-Login genügt nicht für die Shop-Endpunkte
        self.client.force_login(self.admin)
        self.assertIn(self.client.get("/api/v1/shop/artikel/").status_code, (401, 403))

    def test_bestellung_idempotent_und_status(self):
        url = "/api/v1/shop/bestellungen/"
        r1 = self.client.post(url, self._bestellung(), content_type="application/json", **self.auth)
        self.assertEqual(r1.status_code, 201, r1.content)
        r2 = self.client.post(url, self._bestellung(), content_type="application/json", **self.auth)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r1.json()["auftrag"], r2.json()["auftrag"])
        self.assertEqual(ShopBestellung.objects.filter(externe_nummer="WEB-9001").count(), 1)
        a = Auftrag.objects.get(nummer=r1.json()["auftrag"])
        self.assertEqual((a.quelle, a.status, a.partner.name), ("SHOP", "BESTAETIGT", "Neukunde Shop GmbH"))
        self.assertEqual(Geschaeftspartner.objects.filter(email="neu@shop-kunde.example").count(), 1)
        # zweite Bestellung desselben Kunden → Kunde wird zugeordnet, nicht neu angelegt
        self.client.post(url, self._bestellung("WEB-9002"), content_type="application/json", **self.auth)
        self.assertEqual(Geschaeftspartner.objects.filter(email="neu@shop-kunde.example").count(), 1)
        S.warenausgang_buchen(S.lieferung_anlegen(a, None, self.lg), self.lg)
        st = self.client.get(url + "WEB-9001/", **self.auth).json()
        self.assertEqual(st["status"], "GELIEFERT")
        self.assertEqual(st["lieferungen"][0]["status"], "GEBUCHT")
        fehler = self.client.post(url, {**self._bestellung("WEB-9003"), "positionen": [
            {"artikelnummer": "GIBT-ES-NICHT", "menge": "1"}]}, content_type="application/json", **self.auth)
        self.assertEqual(fehler.status_code, 400)
        self.assertFalse(ShopBestellung.objects.filter(externe_nummer="WEB-9003").exists())


class EdifactTests(Basis):
    def test_parser_demo_datei(self):
        b = ED.orders_parsen(ORDERS_DEMO)
        self.assertEqual((b.bestellnummer, b.kaeufer_gln), ("4500098765", "4298765000007"))
        self.assertEqual([p["kundenartikelnummer"] for p in b.positionen], ["WEB-LB200", "WEB-AE1"])
        self.assertEqual([p["menge"] for p in b.positionen], [D(40), D(2)])
        self.assertEqual(b.lieferadresse["plz"], "85049")
        with self.assertRaises(ED.EdiFehler):
            ED.orders_parsen("UNB+UNOC:3+X+Y+261120:1200+1'UNH+1+INVOIC:D:96A:UN'UNT+2+1'UNZ+1+1'")
        self.assertEqual(ED.segmente("A+b?+c:d'B+e'"), [("A", [["b+c", "d"]]), ("B", [["e"]])])

    def test_import_dublette_und_fehlerprotokoll(self):
        dublette = ED.orders_importieren(ORDERS_DEMO, "orders_demo.edi", self.vt)  # bereits in den Demodaten
        self.assertEqual(dublette.status, EdiNachricht.Status.FEHLER)
        self.assertIn("bereits importiert", dublette.fehler)
        neu = ED.orders_importieren(ORDERS_DEMO.replace("4500098765", "4500099999"), "neu.edi", self.vt)
        self.assertEqual(neu.status, EdiNachricht.Status.VERARBEITET, neu.fehler)
        a = neu.auftrag
        self.assertEqual((a.quelle, a.partner, a.ihre_referenz), ("EDI", self.weber, "4500099999"))
        self.assertEqual(sorted(a.positionen.values_list("kundenartikelnummer", flat=True)), ["WEB-AE1", "WEB-LB200"])
        unbekannt = ED.orders_importieren(ORDERS_DEMO.replace("4298765000007", "4000000000000").replace(
            "4500098765", "1"), "x.edi", self.vt)
        self.assertEqual(unbekannt.status, EdiNachricht.Status.FEHLER)
        self.assertIsNone(unbekannt.auftrag)
        kaputt = ED.orders_importieren("das ist kein EDIFACT", "kaputt.edi", self.vt)
        self.assertEqual(kaputt.status, EdiNachricht.Status.FEHLER)

    def _pruefe_rahmen(self, text, typ):
        segs = ED.segmente(text)
        tags = [t for t, _ in segs]
        self.assertEqual(tags[0], "UNB")
        self.assertEqual(tags[-1], "UNZ")
        unh = tags.index("UNH")
        self.assertEqual(segs[unh][1][1][0], typ)
        unt = tags.index("UNT")
        self.assertEqual(int(segs[unt][1][0][0]), unt - unh + 1)  # Segmentzähler inkl. UNH/UNT
        return segs

    def test_generatoren_ordrsp_desadv_invoic(self):
        neu = ED.orders_importieren(ORDERS_DEMO.replace("4500098765", "4500088888"), "neu.edi", self.vt,
                                    preise_uebernehmen=True)
        a = S.auftrag_bestaetigen(neu.auftrag, self.vt)
        self._pruefe_rahmen(ED.ordrsp_erzeugen(a).inhalt, "ORDRSP")
        lf = S.warenausgang_buchen(S.lieferung_anlegen(a, None, self.lg), self.lg)
        segs = self._pruefe_rahmen(ED.desadv_erzeugen(lf).inhalt, "DESADV")
        self.assertIn(["WEB-LB200", "IN"], [e[1] for t, e in segs if t == "PIA" and len(e) > 1])
        re = S.rechnung_erstellen([lf], self.vt)
        n = ED.invoic_erzeugen(re)
        segs = self._pruefe_rahmen(n.inhalt, "INVOIC")
        self.assertEqual((n.typ, n.richtung, n.beleg), ("INVOIC", "AUS", re))
        moa = {e[0][0]: D(e[0][1]) for t, e in segs if t == "MOA"}
        self.assertEqual(moa["77"], re.summe_brutto)
        self.assertEqual(moa["125"], re.summe_netto)
        self.assertIn(("BGM", [["380"], [re.nummer], ["9"]]), segs)
        ED.als_uebertragen_markieren(n)
        n.refresh_from_db()
        self.assertEqual(n.status, EdiNachricht.Status.UEBERTRAGEN)


class RechteTests(Basis):
    def test_modulrechte_getrennt(self):
        self.client.force_login(self.lg)  # Rolle Lager: Vertrieb nur lesen, kein EDI/Shop-API
        self.assertEqual(self.client.get(reverse("vertrieb:auftraege")).status_code, 200)
        self.assertEqual(self.client.get(reverse("vertrieb:auftrag_neu")).status_code, 403)
        self.assertEqual(self.client.get(reverse("vertrieb:edi")).status_code, 403)
        self.assertEqual(self.client.get(reverse("vertrieb:shops")).status_code, 403)
        self.assertEqual(self.client.post("/api/v1/edi/nachrichten/orders/", {"inhalt": ORDERS_DEMO}).status_code,
                         403)
        self.client.force_login(self.vt)
        self.assertEqual(self.client.get(reverse("vertrieb:auftrag_neu")).status_code, 200)
        self.assertEqual(self.client.get(reverse("vertrieb:edi")).status_code, 200)
        self.assertEqual(self.client.post("/api/v1/versand/touren/bilden/", {}).status_code, 403)
        self.client.force_login(self.gast)
        self.assertEqual(self.client.get(reverse("vertrieb:index")).status_code, 200)
        a = Auftrag.objects.filter(status="ERFASST").first()
        self.assertEqual(self.client.post(reverse("vertrieb:auftrag_aktion", args=[a.pk, "bestaetigen"]))
                         .status_code, 403)

    def test_seiten_und_dateien(self):
        self.client.force_login(self.admin)
        a = Auftrag.objects.filter(quelle="EDI").first()
        lf = Lieferung.objects.exclude(beleg__nummer="").first()
        re = Beleg.objects.filter(belegart="RECHNUNG", status="FESTGESCHRIEBEN").order_by("-pk").first()
        n = EdiNachricht.objects.first()
        for url in [reverse("vertrieb:auftrag", args=[a.pk]), reverse("vertrieb:lieferschein", args=[lf.pk]),
                    reverse("vertrieb:rechnung", args=[re.pk]), reverse("vertrieb:edi_nachricht", args=[n.pk]),
                    reverse("vertrieb:leads_liste"), reverse("vertrieb:chancen_liste"),
                    reverse("vertrieb:aktivitaeten_liste"), reverse("vertrieb:kundenpreise_liste")]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        for art, typ in (("zugferd", "application/pdf"), ("xrechnung", "xml")):
            r = self.client.get(reverse("vertrieb:rechnung_datei", args=[re.pk, art]))
            self.assertEqual(r.status_code, 200, art)
            self.assertIn(typ, r["Content-Type"])


class OberflaechentestRegressionTests(Basis):
    """Regressionen aus dem Playwright-Oberflächentest (tests_ui/)."""

    def test_lieferschein_leere_mengenfelder_liefern_alles(self):
        a = self.auftrag(self.mueller, [(self.f5000, 2), (self.f5010, 1)])
        pos = list(a.positionen.order_by("position"))
        self.client.force_login(self.admin)
        r = self.client.post(reverse("vertrieb:auftrag_aktion", args=[a.pk, "liefern"]),
                             {f"menge_{pos[0].pk}": "", f"menge_{pos[1].pk}": ""})
        lf = Lieferung.objects.get(auftrag=a)
        self.assertRedirects(r, reverse("vertrieb:lieferschein", args=[lf.pk]), fetch_redirect_response=False)
        self.assertEqual(lf.positionen.count(), 2)

    def test_lieferschein_teilmenge_und_ungueltige_eingabe(self):
        a = self.auftrag(self.mueller, [(self.f5000, 5), (self.f5010, 1)])
        pos = list(a.positionen.order_by("position"))
        self.client.force_login(self.admin)
        url = reverse("vertrieb:auftrag_aktion", args=[a.pk, "liefern"])
        r = self.client.post(url, {f"menge_{pos[0].pk}": "abc"})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Lieferung.objects.filter(auftrag=a).exists())
        self.client.post(url, {f"menge_{pos[0].pk}": "2", f"menge_{pos[1].pk}": "0"})
        lf = Lieferung.objects.get(auftrag=a)
        self.assertEqual([(p.menge) for p in lf.positionen.all()], [D(2)])

    def test_links_ohne_recht_ausgeblendet(self):
        self.client.force_login(self.gast)
        self.assertNotContains(self.client.get(reverse("vertrieb:pipeline")), reverse("vertrieb:chancen_neu"))
        self.assertNotContains(self.client.get(reverse("versand:index")), reverse("versand:planen"))

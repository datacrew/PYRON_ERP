"""Erzeugt Beispiel-Eingangsrechnungen für Tests und Demodaten (keine echten Lieferantenrechnungen).

Dieselbe Rechnung lässt sich als digitales PDF (Textlayer), als „Scan“ (Bild-PDF/PNG/TIFF ohne Text),
als ZUGFeRD/Factur-X-PDF und als XRechnung (CII-XML) erzeugen. Dazu kommen Trennblätter für Sammel-PDFs.
"""
import io
from datetime import date, timedelta
from decimal import Decimal

D = Decimal
SCHRIFT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
SCHRIFT_FETT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
TRENNBLATT_CODE = "PYRON-TRENNBLATT"


def beispiel(**abweichend):
    """Rechnungsdaten (Dictionary). Summen werden aus den Positionen berechnet."""
    r = {
        "lieferant": "Bürobedarf Schneider GmbH",
        "strasse": "Industriestraße 12", "ort": "90402 Nürnberg",
        "ust_id": "DE811569869", "iban": "DE89370400440532013000", "bic": "COBADEFFXXX",
        "kunde": "PYRON Musterfirma GmbH", "kunde_ort": "Hauptstraße 1, 80331 München",
        "rechnungsnummer": "RE-2026-0815", "datum": date(2026, 3, 12), "zahlungsziel_tage": 30,
        "positionen": [
            {"menge": D("10"), "einheit": "Stk", "text": "Kopierpapier A4 80 g/m²", "preis": D("4.50"), "satz": 19},
            {"menge": D("2"), "einheit": "Stk", "text": "Toner schwarz HP 26A", "preis": D("89.00"), "satz": 19},
            {"menge": D("5"), "einheit": "kg", "text": "Kaffeebohnen Crema", "preis": D("12.00"), "satz": 7},
        ],
    }
    r.update(abweichend)
    netto, steuer = D("0"), {}
    for p in r["positionen"]:
        p["betrag"] = (p["menge"] * p["preis"]).quantize(D("0.01"))
        netto += p["betrag"]
        steuer.setdefault(p["satz"], D("0"))
        steuer[p["satz"]] += p["betrag"]
    r["netto"] = netto
    r["steuern"] = {s: (b, (b * s / 100).quantize(D("0.01"))) for s, b in sorted(steuer.items(), reverse=True)}
    r["steuer"] = sum((v[1] for v in r["steuern"].values()), D("0"))
    r["brutto"] = r["netto"] + r["steuer"]
    r["faellig"] = r["datum"] + timedelta(days=r["zahlungsziel_tage"])
    return r


def _de(betrag, stellen=2):
    s = f"{betrag:,.{stellen}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _iban_lesbar(iban):
    return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))


def zeilen(r):
    """Layout als Liste (y_mm, [(x_mm, text, ausrichtung, fett)]) – gemeinsam für PDF und Scan."""
    z, y = [], 20
    z.append((y, [(20, r["lieferant"], "L", True)]))
    y += 6
    z.append((y, [(20, f'{r["strasse"]} · {r["ort"]}', "L", False)]))
    y += 14
    z.append((y, [(20, r["kunde"], "L", False)]))
    y += 5
    z.append((y, [(20, r["kunde_ort"], "L", False)]))
    y += 16
    z.append((y, [(20, "Rechnung", "L", True)]))
    y += 9
    z.append((y, [(20, f'Rechnungsnummer: {r["rechnungsnummer"]}', "L", False)]))
    y += 6
    z.append((y, [(20, f'Rechnungsdatum: {r["datum"]:%d.%m.%Y}', "L", False)]))
    y += 6
    z.append((y, [(20, f'USt-IdNr.: {r["ust_id"]}', "L", False)]))
    y += 12
    z.append((y, [(20, "Pos.", "L", True), (32, "Menge", "L", True), (52, "Einheit", "L", True),
                  (70, "Bezeichnung", "L", True), (150, "Einzelpreis", "R", True), (165, "USt", "R", True),
                  (190, "Gesamt", "R", True)]))
    for i, p in enumerate(r["positionen"], 1):
        y += 7
        z.append((y, [(20, str(i), "L", False), (32, _de(p["menge"]), "L", False), (52, p["einheit"], "L", False),
                      (70, p["text"], "L", False), (150, _de(p["preis"]), "R", False),
                      (165, f'{p["satz"]} %', "R", False), (190, _de(p["betrag"]), "R", False)]))
    y += 12
    z.append((y, [(120, "Nettobetrag", "L", False), (190, f'{_de(r["netto"])} EUR', "R", False)]))
    for satz, (basis, betrag) in r["steuern"].items():
        y += 6
        z.append((y, [(120, f"zzgl. USt {satz} %", "L", False), (190, f"{_de(betrag)} EUR", "R", False)]))
    y += 7
    z.append((y, [(120, "Gesamtbetrag", "L", True), (190, f'{_de(r["brutto"])} EUR', "R", True)]))
    y += 16
    z.append((y, [(20, f'Zahlbar innerhalb von {r["zahlungsziel_tage"]} Tagen netto bis zum '
                       f'{r["faellig"]:%d.%m.%Y}.', "L", False)]))
    y += 6
    z.append((y, [(20, f'Bankverbindung: IBAN {_iban_lesbar(r["iban"])} · BIC {r["bic"]}', "L", False)]))
    return z


# ------------------------------------------------------------------ Ausgabeformate

def pdf(r=None, seiten_davor=0):
    """Digitales PDF mit Textlayer."""
    from fpdf import FPDF

    r = r or beispiel()
    doc = FPDF(format="A4", unit="mm")
    doc.add_font("DejaVu", "", SCHRIFT)
    doc.add_font("DejaVu", "B", SCHRIFT_FETT)
    doc.add_page()
    for y, felder in zeilen(r):
        for x, text, ausrichtung, fett in felder:
            doc.set_font("DejaVu", "B" if fett else "", 14 if text == "Rechnung" else 10)
            w = doc.get_string_width(text)
            doc.text(x - w if ausrichtung == "R" else x, y, text)
    return bytes(doc.output())


def bild(r=None, dpi=300):
    """Gerenderte Rechnung als PIL-Bild (simulierter Scan, kein Textlayer)."""
    from PIL import Image, ImageDraw, ImageFont

    r = r or beispiel()
    mm = dpi / 25.4
    img = Image.new("L", (int(210 * mm), int(297 * mm)), 255)
    zeichnen = ImageDraw.Draw(img)
    schriften = {}
    for y, felder in zeilen(r):
        for x, text, ausrichtung, fett in felder:
            groesse = int((14 if text == "Rechnung" else 10) * dpi / 72)
            f = schriften.setdefault((fett, groesse), ImageFont.truetype(SCHRIFT_FETT if fett else SCHRIFT, groesse))
            w = zeichnen.textlength(text, font=f)
            px = x * mm - (w if ausrichtung == "R" else 0)
            zeichnen.text((px, y * mm - groesse * 0.8), text, font=f, fill=0)
    return img


def scan_pdf(r=None, dpi=300):
    puffer = io.BytesIO()
    bild(r, dpi).save(puffer, "PDF", resolution=float(dpi))
    return puffer.getvalue()


def png(r=None, dpi=300):
    puffer = io.BytesIO()
    bild(r, dpi).save(puffer, "PNG", dpi=(dpi, dpi))
    return puffer.getvalue()


def tiff(r=None, dpi=300):
    puffer = io.BytesIO()
    bild(r, dpi).save(puffer, "TIFF", dpi=(dpi, dpi), compression="tiff_lzw")
    return puffer.getvalue()


def cii_xml(r=None):
    """Factur-X/ZUGFeRD bzw. XRechnung im CII-Format (Profil EN 16931)."""
    r = r or beispiel()
    pos = []
    for i, p in enumerate(r["positionen"], 1):
        pos.append(f"""
  <ram:IncludedSupplyChainTradeLineItem>
   <ram:AssociatedDocumentLineDocument><ram:LineID>{i}</ram:LineID></ram:AssociatedDocumentLineDocument>
   <ram:SpecifiedTradeProduct><ram:Name>{p["text"]}</ram:Name></ram:SpecifiedTradeProduct>
   <ram:SpecifiedLineTradeAgreement><ram:NetPriceProductTradePrice><ram:ChargeAmount>{p["preis"]}</ram:ChargeAmount></ram:NetPriceProductTradePrice></ram:SpecifiedLineTradeAgreement>
   <ram:SpecifiedLineTradeDelivery><ram:BilledQuantity unitCode="{"KGM" if p["einheit"] == "kg" else "H87"}">{p["menge"]}</ram:BilledQuantity></ram:SpecifiedLineTradeDelivery>
   <ram:SpecifiedLineTradeSettlement>
    <ram:ApplicableTradeTax><ram:TypeCode>VAT</ram:TypeCode><ram:CategoryCode>S</ram:CategoryCode><ram:RateApplicablePercent>{p["satz"]}</ram:RateApplicablePercent></ram:ApplicableTradeTax>
    <ram:SpecifiedTradeSettlementLineMonetarySummation><ram:LineTotalAmount>{p["betrag"]}</ram:LineTotalAmount></ram:SpecifiedTradeSettlementLineMonetarySummation>
   </ram:SpecifiedLineTradeSettlement>
  </ram:IncludedSupplyChainTradeLineItem>""")
    steuern = "".join(f"""
   <ram:ApplicableTradeTax><ram:CalculatedAmount>{betrag}</ram:CalculatedAmount><ram:TypeCode>VAT</ram:TypeCode><ram:BasisAmount>{basis}</ram:BasisAmount><ram:CategoryCode>S</ram:CategoryCode><ram:RateApplicablePercent>{satz}</ram:RateApplicablePercent></ram:ApplicableTradeTax>"""
                      for satz, (basis, betrag) in r["steuern"].items())
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100" xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100" xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100" xmlns:qdt="urn:un:unece:uncefact:data:standard:QualifiedDataType:100">
 <rsm:ExchangedDocumentContext><ram:GuidelineSpecifiedDocumentContextParameter><ram:ID>urn:cen.eu:en16931:2017</ram:ID></ram:GuidelineSpecifiedDocumentContextParameter></rsm:ExchangedDocumentContext>
 <rsm:ExchangedDocument><ram:ID>{r["rechnungsnummer"]}</ram:ID><ram:TypeCode>380</ram:TypeCode><ram:IssueDateTime><udt:DateTimeString format="102">{r["datum"]:%Y%m%d}</udt:DateTimeString></ram:IssueDateTime></rsm:ExchangedDocument>
 <rsm:SupplyChainTradeTransaction>{"".join(pos)}
  <ram:ApplicableHeaderTradeAgreement>
   <ram:SellerTradeParty><ram:Name>{r["lieferant"]}</ram:Name><ram:PostalTradeAddress><ram:CountryID>DE</ram:CountryID></ram:PostalTradeAddress><ram:SpecifiedTaxRegistration><ram:ID schemeID="VA">{r["ust_id"]}</ram:ID></ram:SpecifiedTaxRegistration></ram:SellerTradeParty>
   <ram:BuyerTradeParty><ram:Name>{r["kunde"]}</ram:Name></ram:BuyerTradeParty>
  </ram:ApplicableHeaderTradeAgreement>
  <ram:ApplicableHeaderTradeDelivery/>
  <ram:ApplicableHeaderTradeSettlement>
   <ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
   <ram:SpecifiedTradeSettlementPaymentMeans><ram:TypeCode>58</ram:TypeCode><ram:PayeePartyCreditorFinancialAccount><ram:IBANID>{r["iban"]}</ram:IBANID></ram:PayeePartyCreditorFinancialAccount></ram:SpecifiedTradeSettlementPaymentMeans>{steuern}
   <ram:SpecifiedTradePaymentTerms><ram:Description>Zahlbar innerhalb von {r["zahlungsziel_tage"]} Tagen netto</ram:Description><ram:DueDateDateTime><udt:DateTimeString format="102">{r["faellig"]:%Y%m%d}</udt:DateTimeString></ram:DueDateDateTime></ram:SpecifiedTradePaymentTerms>
   <ram:SpecifiedTradeSettlementHeaderMonetarySummation><ram:LineTotalAmount>{r["netto"]}</ram:LineTotalAmount><ram:TaxBasisTotalAmount>{r["netto"]}</ram:TaxBasisTotalAmount><ram:TaxTotalAmount currencyID="EUR">{r["steuer"]}</ram:TaxTotalAmount><ram:GrandTotalAmount>{r["brutto"]}</ram:GrandTotalAmount><ram:DuePayableAmount>{r["brutto"]}</ram:DuePayableAmount></ram:SpecifiedTradeSettlementHeaderMonetarySummation>
  </ram:ApplicableHeaderTradeSettlement>
 </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>
""".encode("utf-8")


def zugferd_pdf(r=None):
    """PDF/A-3 mit eingebetteter Factur-X-XML (ZUGFeRD 2.x, Profil EN 16931)."""
    from facturx import generate_from_binary

    r = r or beispiel()
    return generate_from_binary(pdf(r), cii_xml(r), flavor="factur-x", level="en16931", check_xsd=False)


def ubl_xml(r=None):
    """Minimale XRechnung im UBL-Format."""
    r = r or beispiel()
    zeilen_xml = "".join(f"""
 <cac:InvoiceLine><cbc:ID>{i}</cbc:ID><cbc:InvoicedQuantity unitCode="H87">{p["menge"]}</cbc:InvoicedQuantity><cbc:LineExtensionAmount currencyID="EUR">{p["betrag"]}</cbc:LineExtensionAmount>
  <cac:Item><cbc:Name>{p["text"]}</cbc:Name><cac:ClassifiedTaxCategory><cbc:ID>S</cbc:ID><cbc:Percent>{p["satz"]}</cbc:Percent><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:ClassifiedTaxCategory></cac:Item>
  <cac:Price><cbc:PriceAmount currencyID="EUR">{p["preis"]}</cbc:PriceAmount></cac:Price></cac:InvoiceLine>"""
                         for i, p in enumerate(r["positionen"], 1))
    steuern = "".join(f"""<cac:TaxSubtotal><cbc:TaxableAmount currencyID="EUR">{basis}</cbc:TaxableAmount><cbc:TaxAmount currencyID="EUR">{betrag}</cbc:TaxAmount><cac:TaxCategory><cbc:ID>S</cbc:ID><cbc:Percent>{satz}</cbc:Percent><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:TaxCategory></cac:TaxSubtotal>"""
                      for satz, (basis, betrag) in r["steuern"].items())
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
 <cbc:CustomizationID>urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0</cbc:CustomizationID>
 <cbc:ID>{r["rechnungsnummer"]}</cbc:ID><cbc:IssueDate>{r["datum"]:%Y-%m-%d}</cbc:IssueDate><cbc:DueDate>{r["faellig"]:%Y-%m-%d}</cbc:DueDate>
 <cbc:InvoiceTypeCode>380</cbc:InvoiceTypeCode><cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
 <cac:AccountingSupplierParty><cac:Party><cac:PartyName><cbc:Name>{r["lieferant"]}</cbc:Name></cac:PartyName><cac:PartyTaxScheme><cbc:CompanyID>{r["ust_id"]}</cbc:CompanyID><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme><cac:PartyLegalEntity><cbc:RegistrationName>{r["lieferant"]}</cbc:RegistrationName></cac:PartyLegalEntity></cac:Party></cac:AccountingSupplierParty>
 <cac:AccountingCustomerParty><cac:Party><cac:PartyLegalEntity><cbc:RegistrationName>{r["kunde"]}</cbc:RegistrationName></cac:PartyLegalEntity></cac:Party></cac:AccountingCustomerParty>
 <cac:PaymentMeans><cbc:PaymentMeansCode>58</cbc:PaymentMeansCode><cac:PayeeFinancialAccount><cbc:ID>{r["iban"]}</cbc:ID></cac:PayeeFinancialAccount></cac:PaymentMeans>
 <cac:PaymentTerms><cbc:Note>Zahlbar innerhalb von {r["zahlungsziel_tage"]} Tagen netto</cbc:Note></cac:PaymentTerms>
 <cac:TaxTotal><cbc:TaxAmount currencyID="EUR">{r["steuer"]}</cbc:TaxAmount>{steuern}</cac:TaxTotal>
 <cac:LegalMonetaryTotal><cbc:LineExtensionAmount currencyID="EUR">{r["netto"]}</cbc:LineExtensionAmount><cbc:TaxExclusiveAmount currencyID="EUR">{r["netto"]}</cbc:TaxExclusiveAmount><cbc:TaxInclusiveAmount currencyID="EUR">{r["brutto"]}</cbc:TaxInclusiveAmount><cbc:PayableAmount currencyID="EUR">{r["brutto"]}</cbc:PayableAmount></cac:LegalMonetaryTotal>{zeilen_xml}
</Invoice>
""".encode("utf-8")

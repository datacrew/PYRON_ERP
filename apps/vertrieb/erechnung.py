"""E-Rechnung nach EN 16931.

* ZUGFeRD 2.x / Factur-X, Profil EN 16931 (COMFORT): UN/CEFACT-CII-XML, eingebettet als
  „factur-x.xml“ in ein PDF/A-3b (WeasyPrint erzeugt das PDF/A-3b, die Bibliothek factur-x bettet
  die XML samt XMP-Metadaten ein).
* XRechnung 3.0 (UBL 2.1 Invoice/CreditNote) als reine XML-Datei.

Offline-Validierung: XSD-Prüfung über die in factur-x mitgelieferten Schemas (CII EN16931, UBL 2.1).
Eine Schematron-Prüfung (EN16931-/XRechnung-Geschäftsregeln, KoSIT-Validator) ist offline nicht
enthalten – siehe README.
"""
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from lxml import etree

from apps.core.models import Mandant

ZWEI, VIER = Decimal("0.01"), Decimal("0.0001")

RSM = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
RAM = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
UDT = "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"
QDT = "urn:un:unece:uncefact:data:standard:QualifiedDataType:100"
UBL_INV = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
UBL_CN = "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"
CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"

EN16931 = "urn:cen.eu:en16931:2017"
XRECHNUNG = "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"
PEPPOL_PROFIL = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"

# Einheiten → UN/ECE Recommendation 20
EINHEITEN = {"Stk": "H87", "kg": "KGM", "t": "TNE", "m": "MTR", "m²": "MTK", "l": "LTR", "Std": "HUR",
             "Pal": "PF", "Krt": "CT", "Satz": "SET"}
EU = {"AT", "BE", "BG", "CY", "CZ", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU", "IE", "IT", "LT", "LU",
      "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK"}


class ERechnungFehler(Exception):
    pass


def _b(wert):
    return f"{Decimal(wert).quantize(ZWEI, ROUND_HALF_UP):.2f}"


def _p(wert):
    return f"{Decimal(wert).quantize(VIER, ROUND_HALF_UP):.4f}".rstrip("0").rstrip(".") or "0"


def _m(wert):
    return f"{Decimal(wert).normalize():f}"


# ---------------------------------------------------------------- gemeinsames Datenmodell

def rechnungsdaten(beleg):
    """Fachliche Inhalte nach EN 16931 (BT-Nummern in Kommentaren) aus Beleg, Mandant und Partner."""
    from apps.belege.pdf import beleg_kontext

    if beleg.belegart not in ("RECHNUNG", "GUTSCHRIFT"):
        raise ERechnungFehler("E-Rechnungen gibt es nur für Rechnungen und Gutschriften.")
    if not beleg.nummer:
        raise ERechnungFehler("Der Beleg hat noch keine Nummer (bitte freigeben).")
    m = Mandant.aktuell()
    k = beleg_kontext(beleg)
    kd = k["partner"]
    partner = beleg.partner
    land = (kd.get("land") or "DE").upper()
    eu_ausland = land in EU and land != "DE"
    steuern = []
    for satz, (netto, steuer) in beleg.steuer_aufstellung().items():
        if satz > 0:
            kat, grund = "S", ""
        elif eu_ausland and kd.get("ust_id"):
            kat, grund = "K", "Steuerfreie innergemeinschaftliche Lieferung (§ 4 Nr. 1b UStG)"
        elif land not in EU and land != "DE":
            kat, grund = "G", "Steuerfreie Ausfuhrlieferung (§ 4 Nr. 1a UStG)"
        else:
            kat, grund = "E", "Steuerbefreit"
        steuern.append({"satz": satz, "netto": netto, "steuer": steuer, "kategorie": kat, "grund": grund})
    kategorie = {s["satz"]: s["kategorie"] for s in steuern}
    zb = beleg.zahlungsbedingung or partner.zahlungsbedingung
    faellig = beleg.belegdatum + timedelta(days=zb.tage_netto if zb else 30)
    zahlungstext = zb.bezeichnung if zb else "Zahlbar innerhalb 30 Tagen netto"
    if zb and zb.skonto_prozent:
        # XRechnung-Konvention für Skonto (BT-20)
        zahlungstext += f"\n#SKONTO#TAGE={zb.skonto_tage}#PROZENT={zb.skonto_prozent:.2f}#\n"
    positionen = []
    for p in k["positionen"]:
        faktor = (Decimal(100) - (p.rabatt_prozent or 0)) / 100
        positionen.append({
            "nr": str(p.position), "artikelnummer": p.artikel.artikelnummer if p.artikel_id else "",
            "ean": (p.artikel.ean if p.artikel_id else "") or "", "name": p.bezeichnung or "Position",
            "menge": p.menge, "einheit": EINHEITEN.get(p.einheit, "C62"),
            "nettopreis": (p.einzelpreis * faktor).quantize(VIER, ROUND_HALF_UP), "betrag": p.betrag_netto,
            "satz": p.steuersatz, "kategorie": kategorie.get(p.steuersatz, "S")})
    netto = sum((s["netto"] for s in steuern), Decimal(0))
    steuer = sum((s["steuer"] for s in steuern), Decimal(0))
    kaeufer_email = partner.email_rechnung or partner.email
    return {
        "nummer": beleg.nummer,                                            # BT-1
        "typ": "381" if beleg.belegart == "GUTSCHRIFT" else "380",         # BT-3
        "gutschrift": beleg.belegart == "GUTSCHRIFT",
        "datum": beleg.belegdatum,                                          # BT-2
        "lieferdatum": beleg.leistungsdatum or beleg.belegdatum,            # BT-72
        "waehrung": beleg.waehrung,                                         # BT-5
        "notiz": "\n".join(x for x in (beleg.betreff, beleg.kopftext) if x),  # BT-22
        # BT-10 Käuferreferenz: Leitweg-ID (öffentliche Auftraggeber) oder Bestellnummer/Kundennummer
        "kaeuferreferenz": kd.get("leitweg_id") or beleg.ihre_referenz or kd.get("nummer") or partner.nummer,
        "bestellnummer": "" if beleg.belegart == "GUTSCHRIFT" else beleg.ihre_referenz[:100],  # BT-13
        "vorrechnung": beleg.ihre_referenz if beleg.belegart == "GUTSCHRIFT" else "",          # BT-25
        "verkaeufer": {"name": m.name, "strasse": m.strasse, "plz": m.plz, "ort": m.ort, "land": m.land or "DE",
                       "ust_id": m.ust_id, "steuernummer": m.steuernummer, "email": m.email,
                       "telefon": m.telefon, "kontakt": m.geschaeftsfuehrer or m.name, "iban": m.iban.replace(" ", ""),
                       "bic": m.bic, "register": m.handelsregister},
        "kaeufer": {"name": kd.get("name") or partner.name, "nummer": kd.get("nummer") or partner.nummer,
                    "strasse": kd.get("strasse", ""), "plz": kd.get("plz", ""), "ort": kd.get("ort", ""),
                    "land": land, "ust_id": kd.get("ust_id") or "", "email": kaeufer_email,
                    "leitweg_id": kd.get("leitweg_id") or "", "gln": partner.gln},
        "steuern": steuern, "positionen": positionen,
        "netto": netto, "steuer": steuer, "brutto": netto + steuer,       # BT-106/109/110/112
        "faellig": faellig, "zahlungstext": zahlungstext,                 # BT-9/BT-20
    }


# ---------------------------------------------------------------- CII (ZUGFeRD / Factur-X EN 16931)

def _el(eltern, ns, name, text=None, **attr):
    e = etree.SubElement(eltern, f"{{{ns}}}{name}", **attr)
    if text is not None:
        e.text = str(text)
    return e


def _cii_datum(eltern, name, datum, dt_name="DateTimeString"):
    e = _el(eltern, RAM, name)
    _el(e, UDT, dt_name, datum.strftime("%Y%m%d"), format="102")


def _cii_partei(eltern, name, p, verkaeufer=False):
    e = _el(eltern, RAM, name)
    if not verkaeufer and p.get("nummer"):
        _el(e, RAM, "ID", p["nummer"])
    if p.get("gln"):
        _el(e, RAM, "GlobalID", p["gln"], schemeID="0088")
    _el(e, RAM, "Name", p["name"])
    if verkaeufer and p.get("register"):
        org = _el(e, RAM, "SpecifiedLegalOrganization")
        _el(org, RAM, "TradingBusinessName", p["name"])
    if verkaeufer:
        kontakt = _el(e, RAM, "DefinedTradeContact")
        _el(kontakt, RAM, "PersonName", p["kontakt"])
        if p.get("telefon"):
            _el(_el(kontakt, RAM, "TelephoneUniversalCommunication"), RAM, "CompleteNumber", p["telefon"])
        if p.get("email"):
            _el(_el(kontakt, RAM, "EmailURIUniversalCommunication"), RAM, "URIID", p["email"])
    adr = _el(e, RAM, "PostalTradeAddress")
    _el(adr, RAM, "PostcodeCode", p["plz"])
    _el(adr, RAM, "LineOne", p["strasse"])
    _el(adr, RAM, "CityName", p["ort"])
    _el(adr, RAM, "CountryID", p["land"])
    if p.get("email"):
        _el(_el(e, RAM, "URIUniversalCommunication"), RAM, "URIID", p["email"], schemeID="EM")
    if p.get("ust_id"):
        _el(_el(e, RAM, "SpecifiedTaxRegistration"), RAM, "ID", p["ust_id"], schemeID="VA")
    if verkaeufer and p.get("steuernummer"):
        _el(_el(e, RAM, "SpecifiedTaxRegistration"), RAM, "ID", p["steuernummer"], schemeID="FC")


def cii_xml(beleg, profil=EN16931):
    """Erzeugt die CII-XML (Factur-X/ZUGFeRD EN 16931 bzw. mit `profil=XRECHNUNG` XRechnung-CII)."""
    d = rechnungsdaten(beleg)
    root = etree.Element(f"{{{RSM}}}CrossIndustryInvoice", nsmap={"rsm": RSM, "ram": RAM, "udt": UDT, "qdt": QDT})
    ctx = _el(root, RSM, "ExchangedDocumentContext")
    _el(_el(ctx, RAM, "GuidelineSpecifiedDocumentContextParameter"), RAM, "ID", profil)
    doc = _el(root, RSM, "ExchangedDocument")
    _el(doc, RAM, "ID", d["nummer"])
    _el(doc, RAM, "TypeCode", d["typ"])
    dt = _el(doc, RAM, "IssueDateTime")
    _el(dt, UDT, "DateTimeString", d["datum"].strftime("%Y%m%d"), format="102")
    if d["notiz"]:
        _el(_el(doc, RAM, "IncludedNote"), RAM, "Content", d["notiz"])
    v = d["verkaeufer"]
    if v.get("register"):
        note = _el(doc, RAM, "IncludedNote")
        _el(note, RAM, "Content", f"{v['name']}, {v['strasse']}, {v['plz']} {v['ort']}; {v['register']}; "
                                  f"Geschäftsführung: {v['kontakt']}")
        _el(note, RAM, "SubjectCode", "REG")
    tx = _el(root, RSM, "SupplyChainTradeTransaction")
    for p in d["positionen"]:
        li = _el(tx, RAM, "IncludedSupplyChainTradeLineItem")
        _el(_el(li, RAM, "AssociatedDocumentLineDocument"), RAM, "LineID", p["nr"])
        prod = _el(li, RAM, "SpecifiedTradeProduct")
        if p["ean"]:
            _el(prod, RAM, "GlobalID", p["ean"], schemeID="0160")
        if p["artikelnummer"]:
            _el(prod, RAM, "SellerAssignedID", p["artikelnummer"])
        _el(prod, RAM, "Name", p["name"])
        ag = _el(li, RAM, "SpecifiedLineTradeAgreement")
        _el(_el(ag, RAM, "NetPriceProductTradePrice"), RAM, "ChargeAmount", _p(p["nettopreis"]))
        _el(_el(li, RAM, "SpecifiedLineTradeDelivery"), RAM, "BilledQuantity", _m(p["menge"]), unitCode=p["einheit"])
        st = _el(li, RAM, "SpecifiedLineTradeSettlement")
        tax = _el(st, RAM, "ApplicableTradeTax")
        _el(tax, RAM, "TypeCode", "VAT")
        _el(tax, RAM, "CategoryCode", p["kategorie"])
        _el(tax, RAM, "RateApplicablePercent", _b(p["satz"]))
        _el(_el(st, RAM, "SpecifiedTradeSettlementLineMonetarySummation"), RAM, "LineTotalAmount", _b(p["betrag"]))
    ag = _el(tx, RAM, "ApplicableHeaderTradeAgreement")
    _el(ag, RAM, "BuyerReference", d["kaeuferreferenz"])
    _cii_partei(ag, "SellerTradeParty", v, verkaeufer=True)
    _cii_partei(ag, "BuyerTradeParty", d["kaeufer"])
    if d["bestellnummer"]:
        _el(_el(ag, RAM, "BuyerOrderReferencedDocument"), RAM, "IssuerAssignedID", d["bestellnummer"])
    dl = _el(tx, RAM, "ApplicableHeaderTradeDelivery")
    ev = _el(dl, RAM, "ActualDeliverySupplyChainEvent")
    _cii_datum(ev, "OccurrenceDateTime", d["lieferdatum"])
    se = _el(tx, RAM, "ApplicableHeaderTradeSettlement")
    _el(se, RAM, "PaymentReference", d["nummer"])
    _el(se, RAM, "InvoiceCurrencyCode", d["waehrung"])
    if v["iban"] and not d["gutschrift"]:
        pm = _el(se, RAM, "SpecifiedTradeSettlementPaymentMeans")
        _el(pm, RAM, "TypeCode", "58")
        _el(_el(pm, RAM, "PayeePartyCreditorFinancialAccount"), RAM, "IBANID", v["iban"])
    for s in d["steuern"]:
        t = _el(se, RAM, "ApplicableTradeTax")
        _el(t, RAM, "CalculatedAmount", _b(s["steuer"]))
        _el(t, RAM, "TypeCode", "VAT")
        if s["grund"]:
            _el(t, RAM, "ExemptionReason", s["grund"])
        _el(t, RAM, "BasisAmount", _b(s["netto"]))
        _el(t, RAM, "CategoryCode", s["kategorie"])
        _el(t, RAM, "RateApplicablePercent", _b(s["satz"]))
    terms = _el(se, RAM, "SpecifiedTradePaymentTerms")
    _el(terms, RAM, "Description", d["zahlungstext"])
    if not d["gutschrift"]:
        _cii_datum(terms, "DueDateDateTime", d["faellig"])
    su = _el(se, RAM, "SpecifiedTradeSettlementHeaderMonetarySummation")
    _el(su, RAM, "LineTotalAmount", _b(d["netto"]))
    _el(su, RAM, "TaxBasisTotalAmount", _b(d["netto"]))
    _el(su, RAM, "TaxTotalAmount", _b(d["steuer"]), currencyID=d["waehrung"])
    _el(su, RAM, "GrandTotalAmount", _b(d["brutto"]))
    _el(su, RAM, "DuePayableAmount", _b(d["brutto"]))
    if d["vorrechnung"]:
        _el(_el(se, RAM, "InvoiceReferencedDocument"), RAM, "IssuerAssignedID", d["vorrechnung"])
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


# ---------------------------------------------------------------- UBL (XRechnung)

def _u(eltern, ns, name, text=None, **attr):
    return _el(eltern, ns, name, text, **attr)


def _ubl_partei(eltern, rolle, p, verkaeufer=False):
    party = _u(_u(eltern, CAC, rolle), CAC, "Party")
    # BT-34/BT-49 elektronische Adresse (XRechnung 3.0 Pflicht): Leitweg-ID (0204) oder E-Mail (EM)
    if not verkaeufer and p.get("leitweg_id"):
        _u(party, CBC, "EndpointID", p["leitweg_id"], schemeID="0204")
    elif p.get("email"):
        _u(party, CBC, "EndpointID", p["email"], schemeID="EM")
    if not verkaeufer and p.get("nummer"):
        _u(_u(party, CAC, "PartyIdentification"), CBC, "ID", p["nummer"])
    adr = _u(party, CAC, "PostalAddress")
    _u(adr, CBC, "StreetName", p["strasse"])
    _u(adr, CBC, "CityName", p["ort"])
    _u(adr, CBC, "PostalZone", p["plz"])
    _u(_u(adr, CAC, "Country"), CBC, "IdentificationCode", p["land"])
    if p.get("ust_id"):
        pts = _u(party, CAC, "PartyTaxScheme")
        _u(pts, CBC, "CompanyID", p["ust_id"])
        _u(_u(pts, CAC, "TaxScheme"), CBC, "ID", "VAT")
    if verkaeufer and p.get("steuernummer"):
        pts = _u(party, CAC, "PartyTaxScheme")
        _u(pts, CBC, "CompanyID", p["steuernummer"])
        _u(_u(pts, CAC, "TaxScheme"), CBC, "ID", "FC")
    le = _u(party, CAC, "PartyLegalEntity")
    _u(le, CBC, "RegistrationName", p["name"])
    if verkaeufer and p.get("register"):
        _u(le, CBC, "CompanyLegalForm", p["register"])
    if verkaeufer:
        c = _u(party, CAC, "Contact")
        _u(c, CBC, "Name", p["kontakt"])
        _u(c, CBC, "Telephone", p["telefon"] or "-")
        _u(c, CBC, "ElectronicMail", p["email"] or "-")


def xrechnung_xml(beleg):
    """XRechnung 3.0 im UBL-2.1-Format (Invoice bzw. CreditNote)."""
    d = rechnungsdaten(beleg)
    gs = d["gutschrift"]
    ns = UBL_CN if gs else UBL_INV
    root = etree.Element(f"{{{ns}}}{'CreditNote' if gs else 'Invoice'}", nsmap={None: ns, "cac": CAC, "cbc": CBC})
    _u(root, CBC, "CustomizationID", XRECHNUNG)
    _u(root, CBC, "ProfileID", PEPPOL_PROFIL)
    _u(root, CBC, "ID", d["nummer"])
    _u(root, CBC, "IssueDate", d["datum"].isoformat())
    if not gs:
        _u(root, CBC, "DueDate", d["faellig"].isoformat())
    _u(root, CBC, "CreditNoteTypeCode" if gs else "InvoiceTypeCode", d["typ"])
    if d["notiz"]:
        _u(root, CBC, "Note", d["notiz"])
    _u(root, CBC, "DocumentCurrencyCode", d["waehrung"])
    _u(root, CBC, "BuyerReference", d["kaeuferreferenz"])
    if d["bestellnummer"]:
        _u(_u(root, CAC, "OrderReference"), CBC, "ID", d["bestellnummer"])
    if d["vorrechnung"]:
        _u(_u(_u(root, CAC, "BillingReference"), CAC, "InvoiceDocumentReference"), CBC, "ID", d["vorrechnung"])
    _ubl_partei(root, "AccountingSupplierParty", d["verkaeufer"], verkaeufer=True)
    _ubl_partei(root, "AccountingCustomerParty", d["kaeufer"])
    _u(_u(root, CAC, "Delivery"), CBC, "ActualDeliveryDate", d["lieferdatum"].isoformat())
    if d["verkaeufer"]["iban"] and not gs:
        pm = _u(root, CAC, "PaymentMeans")
        _u(pm, CBC, "PaymentMeansCode", "58")
        _u(pm, CBC, "PaymentID", d["nummer"])
        acc = _u(pm, CAC, "PayeeFinancialAccount")
        _u(acc, CBC, "ID", d["verkaeufer"]["iban"])
    _u(_u(root, CAC, "PaymentTerms"), CBC, "Note", d["zahlungstext"])
    tt = _u(root, CAC, "TaxTotal")
    _u(tt, CBC, "TaxAmount", _b(d["steuer"]), currencyID=d["waehrung"])
    for s in d["steuern"]:
        sub = _u(tt, CAC, "TaxSubtotal")
        _u(sub, CBC, "TaxableAmount", _b(s["netto"]), currencyID=d["waehrung"])
        _u(sub, CBC, "TaxAmount", _b(s["steuer"]), currencyID=d["waehrung"])
        cat = _u(sub, CAC, "TaxCategory")
        _u(cat, CBC, "ID", s["kategorie"])
        _u(cat, CBC, "Percent", _b(s["satz"]))
        if s["grund"]:
            _u(cat, CBC, "TaxExemptionReason", s["grund"])
        _u(_u(cat, CAC, "TaxScheme"), CBC, "ID", "VAT")
    lmt = _u(root, CAC, "LegalMonetaryTotal")
    for name, wert in (("LineExtensionAmount", d["netto"]), ("TaxExclusiveAmount", d["netto"]),
                       ("TaxInclusiveAmount", d["brutto"]), ("PayableAmount", d["brutto"])):
        _u(lmt, CBC, name, _b(wert), currencyID=d["waehrung"])
    for p in d["positionen"]:
        line = _u(root, CAC, "CreditNoteLine" if gs else "InvoiceLine")
        _u(line, CBC, "ID", p["nr"])
        _u(line, CBC, "CreditedQuantity" if gs else "InvoicedQuantity", _m(p["menge"]), unitCode=p["einheit"])
        _u(line, CBC, "LineExtensionAmount", _b(p["betrag"]), currencyID=d["waehrung"])
        item = _u(line, CAC, "Item")
        _u(item, CBC, "Name", p["name"])
        if p["artikelnummer"]:
            _u(_u(item, CAC, "SellersItemIdentification"), CBC, "ID", p["artikelnummer"])
        if p["ean"]:
            _u(_u(item, CAC, "StandardItemIdentification"), CBC, "ID", p["ean"], schemeID="0160")
        cat = _u(item, CAC, "ClassifiedTaxCategory")
        _u(cat, CBC, "ID", p["kategorie"])
        _u(cat, CBC, "Percent", _b(p["satz"]))
        _u(_u(cat, CAC, "TaxScheme"), CBC, "ID", "VAT")
        _u(_u(line, CAC, "Price"), CBC, "PriceAmount", _p(p["nettopreis"]), currencyID=d["waehrung"])
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


# ---------------------------------------------------------------- Validierung / Einbettung

def pruefen_cii(xml):
    """XSD-Prüfung Factur-X EN 16931 (wirft Exception bei Fehler)."""
    from facturx import xml_check_xsd

    return xml_check_xsd(xml, flavor="factur-x", level="en16931")


def pruefen_ubl(xml, gutschrift=False):
    from facturx import xml_check_xsd

    return xml_check_xsd(xml, flavor="ubl-2.1-creditnote" if gutschrift else "ubl-2.1-invoice")


def summen_pruefen(xml):
    """Rechnerische Plausibilität (Teilmenge der EN-16931-Regeln BR-CO-10/13/15, BR-S-08) für CII."""
    root = etree.fromstring(xml)
    ns = {"ram": RAM}

    def zahl(pfad):
        return sum((Decimal(e.text) for e in root.iterfind(pfad, ns)), Decimal(0))

    fehler = []
    zeilen = zahl(".//ram:SpecifiedTradeSettlementLineMonetarySummation/ram:LineTotalAmount")
    su = ".//ram:SpecifiedTradeSettlementHeaderMonetarySummation/"
    if zeilen != zahl(su + "ram:LineTotalAmount"):
        fehler.append("BR-CO-10: Summe der Positionen ≠ LineTotalAmount")
    basis, steuer = zahl(su + "ram:TaxBasisTotalAmount"), zahl(su + "ram:TaxTotalAmount")
    if basis + steuer != zahl(su + "ram:GrandTotalAmount"):
        fehler.append("BR-CO-15: Netto + Steuer ≠ Brutto")
    kopf = ".//ram:ApplicableHeaderTradeSettlement/ram:ApplicableTradeTax/"
    if zahl(kopf + "ram:CalculatedAmount") != steuer:
        fehler.append("BR-CO-14: Summe der Steuerbeträge ≠ TaxTotalAmount")
    if zahl(kopf + "ram:BasisAmount") != basis:
        fehler.append("BR-CO-13: Summe der Bemessungsgrundlagen ≠ TaxBasisTotalAmount")
    return fehler


def zugferd_pdf(pdf_bytes, xml):
    """Bettet die CII-XML als factur-x.xml (AFRelationship Data) in ein PDF/A-3 ein."""
    from facturx import generate_from_binary

    return generate_from_binary(pdf_bytes, xml, flavor="factur-x", level="en16931", check_xsd=True,
                                lang="de-DE")


def xml_aus_pdf(pdf_bytes):
    """Liest die eingebettete Factur-X/ZUGFeRD-XML aus einem PDF → (Dateiname, XML-Bytes)."""
    from facturx import get_xml_from_pdf

    return get_xml_from_pdf(pdf_bytes, check_xsd=False)


def zugferd_rechnung(beleg):
    """ZUGFeRD-/Factur-X-PDF (EN 16931) zu einer Rechnung/Gutschrift: das Belegformular wird mit derselben
    Vorlage wie das Archiv-PDF als PDF/A-3b gerendert und die geprüfte CII-XML eingebettet.
    Das GoBD-Archiv-PDF des Belegs bleibt unverändert (Prüfsumme)."""
    from django.conf import settings
    from weasyprint import HTML

    from apps.belege.pdf import html_rendern

    xml = cii_xml(beleg)
    fehler = summen_pruefen(xml)
    if fehler:
        raise ERechnungFehler("; ".join(fehler))
    pdf = HTML(string=html_rendern(beleg), base_url=str(settings.BASE_DIR)).write_pdf(pdf_variant="pdf/a-3b")
    return zugferd_pdf(pdf, xml)

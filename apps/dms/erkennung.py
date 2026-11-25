"""Belegerkennung für Eingangsrechnungen.

Quellen (in dieser Reihenfolge):
1. Strukturierte E-Rechnung (ZUGFeRD/Factur-X/XRechnung CII oder UBL) → Felder direkt aus der XML,
   Konfidenz 1,0.
2. Text (Textlayer oder OCR) → regelbasierte Extraktion: beschriftete Felder per regulärem Ausdruck,
   Positionen per Tabellenheuristik (Zeilen mit Menge … Beschreibung … Einzelpreis … [Satz] … Betrag).

Jedes Feld wird als {"wert": …, "konfidenz": 0..1} abgelegt (Beträge als Zeichenkette „283.00“, Datumswerte
ISO „2026-03-12“), damit das Ergebnis JSON-serialisierbar im Dokument gespeichert werden kann.

`extrahieren()` ist rein (ohne Datenbank). `erkennen()` ergänzt Lieferantenzuordnung, Kontierungsvorschlag
(Lernregeln) und Plausibilitätsprüfungen und speichert das Ergebnis am Dokument.

Hinweis: Die Regeln sind auf übliche deutsche Rechnungslayouts ausgelegt und mit erzeugten Testrechnungen
geprüft – nicht gegen eine große Zahl echter Lieferantenrechnungen. Deshalb landet jede Rechnung vor der
Verbuchung in der Prüfmaske.
"""
import difflib
import logging
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Count
from django.utils import timezone

log = logging.getLogger(__name__)

D = Decimal
NULL = D("0")
CENT = D("0.01")

# Ab dieser Gesamtkonfidenz (und ohne Plausibilitätsfehler) gilt eine Rechnung als „erkannt“
SCHWELLE_ERKANNT = D("0.80")

IBAN_LAENGEN = {"DE": 22, "AT": 20, "CH": 21, "LI": 21, "NL": 18, "BE": 16, "LU": 20, "FR": 27, "IT": 27,
                "ES": 24, "PL": 28, "CZ": 24, "DK": 18, "SE": 24, "GB": 22, "IE": 22, "PT": 25, "FI": 18}

BETRAG = r"-?\d{1,3}(?:\.\d{3})*,\d{2}|-?\d+,\d{2}"
# Einzelpreise häufig mit 3–4 Nachkommastellen (Kleinteile), Mengen auch mit Tausenderpunkt („1.000,00“)
EINZELPREIS = r"-?\d{1,3}(?:\.\d{3})*,\d{2,4}|-?\d+,\d{2,4}"
MENGE = r"\d{1,3}(?:\.\d{3})+(?:,\d{1,3})?|\d+(?:[.,]\d{1,3})?"
DATUM = r"\d{1,2}\.\s?\d{1,2}\.\s?(?:\d{4}|\d{2})\b"

FELDER_KOPF = ("lieferant_name", "ust_id", "iban", "rechnungsnummer", "rechnungsdatum", "faellig_am",
               "zahlungsziel_tage", "zahlungsbedingung", "bestellnummer", "netto", "steuer", "brutto",
               "skonto_prozent", "skonto_tage", "skonto_bis", "skonto_betrag")


# ------------------------------------------------------------------ Hilfen

def zahl(text):
    """Deutsche oder technische Zahlschreibweise → Decimal (None, wenn nicht lesbar)."""
    if text is None:
        return None
    s = str(text).strip().replace("€", "").replace("EUR", "").replace(" ", "").replace("\u00a0", "")
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return D(s)
    except InvalidOperation:
        return None


def datum(text):
    """„12.03.2026“, „12.3.26“, „2026-03-12“ oder „20260312“ → date (None, wenn nicht lesbar)."""
    if not text:
        return None
    s = str(text).strip().replace(" ", "")
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def iban_gueltig(iban):
    """Prüfziffernkontrolle nach ISO 13616 (Modulo 97)."""
    iban = re.sub(r"\s", "", iban or "").upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", iban):
        return False
    if iban[:2] in IBAN_LAENGEN and len(iban) != IBAN_LAENGEN[iban[:2]]:
        return False
    zahlen = "".join(str(int(c, 36)) for c in iban[4:] + iban[:4])
    return int(zahlen) % 97 == 1


def ust_id_normal(wert):
    return re.sub(r"[\s.\-]", "", wert or "").upper()


def _feld(wert, konfidenz):
    if isinstance(wert, Decimal):
        wert = str(wert.quantize(CENT))
    elif isinstance(wert, date):
        wert = wert.isoformat()
    return {"wert": wert, "konfidenz": round(float(konfidenz), 2)}


def _num(wert, stellen=CENT):
    return str(D(wert).quantize(stellen)) if wert is not None else None


def feldwert(felder, name, typ=None):
    """Wert eines Feldes typisiert lesen (typ: 'betrag', 'datum', 'int')."""
    w = (felder.get(name) or {}).get("wert")
    if w in (None, ""):
        return None
    if typ == "betrag":
        return zahl(w)
    if typ == "datum":
        return datum(w)
    if typ == "int":
        try:
            return int(w)
        except (TypeError, ValueError):
            return None
    return w


# ------------------------------------------------------------------ E-Rechnung (XML)

def _kinder(el, name):
    return [k for k in el if isinstance(k.tag, str) and k.tag.rsplit("}", 1)[-1] == name]


def _pfad(el, pfad, alle=False):
    """Namensraumunabhängiger Pfad „A/B/C“ (lokale Namen) → erstes Element bzw. Liste."""
    knoten = [el]
    for teil in pfad.split("/"):
        knoten = [k for n in knoten for k in _kinder(n, teil)]
    return knoten if alle else (knoten[0] if knoten else None)


def _txt(el, pfad=None):
    k = _pfad(el, pfad) if pfad else el
    return (k.text or "").strip() if k is not None and k.text else ""


def _xml_cii(w):
    tx = _pfad(w, "SupplyChainTradeTransaction")
    verk = _pfad(tx, "ApplicableHeaderTradeAgreement/SellerTradeParty")
    abr = _pfad(tx, "ApplicableHeaderTradeSettlement")
    summen = _pfad(abr, "SpecifiedTradeSettlementHeaderMonetarySummation")
    ust = ""
    for reg in _pfad(verk, "SpecifiedTaxRegistration/ID", alle=True) if verk is not None else []:
        if reg.get("schemeID") == "VA":
            ust = (reg.text or "").strip()
    bedingungen = _pfad(abr, "SpecifiedTradePaymentTerms", alle=True)
    bed = bedingungen[0] if bedingungen else None
    r = {
        "rechnungsnummer": _txt(w, "ExchangedDocument/ID"),
        "rechnungsdatum": datum(_txt(w, "ExchangedDocument/IssueDateTime/DateTimeString")),
        "lieferant_name": _txt(verk, "Name") if verk is not None else "",
        "ust_id": ust,
        "iban": _txt(abr, "SpecifiedTradeSettlementPaymentMeans/PayeePartyCreditorFinancialAccount/IBANID"),
        "faellig_am": datum(_txt(bed, "DueDateDateTime/DateTimeString")) if bed is not None else None,
        # BT-20 Zahlungsbedingungen (ggf. mehrere Einträge, z. B. Skontostaffel + netto)
        "zahlungsbedingung": "; ".join(t for t in (_txt(b, "Description") for b in bedingungen) if t),
        # BT-13 Bestellnummer des Käufers
        "bestellnummer": _txt(tx, "ApplicableHeaderTradeAgreement/BuyerOrderReferencedDocument/IssuerAssignedID"),
        "netto": zahl(_txt(summen, "TaxBasisTotalAmount") or _txt(summen, "LineTotalAmount")),
        "steuer": zahl(_txt(summen, "TaxTotalAmount")),
        "brutto": zahl(_txt(summen, "GrandTotalAmount")),
        "steuersaetze": [{"satz": zahl(_txt(t, "RateApplicablePercent")), "basis": zahl(_txt(t, "BasisAmount")),
                          "betrag": zahl(_txt(t, "CalculatedAmount"))}
                         for t in _pfad(abr, "ApplicableTradeTax", alle=True)],
        "positionen": [],
    }
    for z in _pfad(tx, "IncludedSupplyChainTradeLineItem", alle=True):
        menge_el = _pfad(z, "SpecifiedLineTradeDelivery/BilledQuantity")
        r["positionen"].append({
            "menge": zahl(_txt(menge_el)) or D("1"),
            "einheit": {"KGM": "kg", "H87": "Stk", "C62": "Stk", "HUR": "h", "LTR": "l", "MTR": "m"}.get(
                menge_el.get("unitCode") if menge_el is not None else "", ""),
            "beschreibung": _txt(z, "SpecifiedTradeProduct/Name"),
            "einzelpreis": zahl(_txt(z, "SpecifiedLineTradeAgreement/NetPriceProductTradePrice/ChargeAmount")),
            "steuersatz": zahl(_txt(z, "SpecifiedLineTradeSettlement/ApplicableTradeTax/RateApplicablePercent")),
            "betrag": zahl(_txt(z, "SpecifiedLineTradeSettlement/SpecifiedTradeSettlementLineMonetarySummation/"
                                   "LineTotalAmount")),
        })
    # Strukturierter Skonto (CII ApplicableTradePaymentDiscountTerms) – sonst Text der Zahlungsbedingung
    for b in bedingungen:
        rabatt = _pfad(b, "ApplicableTradePaymentDiscountTerms")
        prozent = zahl(_txt(rabatt, "CalculationPercent")) if rabatt is not None else None
        if prozent:
            tage = _txt(rabatt, "BasisPeriodMeasure")
            r["skonto"] = {"prozent": prozent, "tage": int(zahl(tage)) if zahl(tage) is not None else None,
                           "bis": datum(_txt(rabatt, "BasisDateDateTime/DateTimeString")),
                           "basis": zahl(_txt(rabatt, "BasisAmount")),
                           "betrag": zahl(_txt(rabatt, "ActualDiscountAmount"))}
            break
    return r


def _xml_ubl(w):
    lief = _pfad(w, "AccountingSupplierParty/Party")
    summen = _pfad(w, "LegalMonetaryTotal")
    name = ""
    if lief is not None:
        name = _txt(lief, "PartyName/Name") or _txt(lief, "PartyLegalEntity/RegistrationName")
    r = {
        "rechnungsnummer": _txt(w, "ID"),
        "rechnungsdatum": datum(_txt(w, "IssueDate")),
        "lieferant_name": name,
        "ust_id": _txt(lief, "PartyTaxScheme/CompanyID") if lief is not None else "",
        "iban": _txt(w, "PaymentMeans/PayeeFinancialAccount/ID"),
        "faellig_am": datum(_txt(w, "DueDate")),
        "zahlungsbedingung": _txt(w, "PaymentTerms/Note"),
        "bestellnummer": _txt(w, "OrderReference/ID"),  # BT-13
        "netto": zahl(_txt(summen, "TaxExclusiveAmount") or _txt(summen, "LineExtensionAmount")),
        "steuer": zahl(_txt(w, "TaxTotal/TaxAmount")),
        "brutto": zahl(_txt(summen, "TaxInclusiveAmount") or _txt(summen, "PayableAmount")),
        "steuersaetze": [{"satz": zahl(_txt(t, "TaxCategory/Percent")), "basis": zahl(_txt(t, "TaxableAmount")),
                          "betrag": zahl(_txt(t, "TaxAmount"))}
                         for t in _pfad(w, "TaxTotal/TaxSubtotal", alle=True)],
        "positionen": [],
    }
    for z in _pfad(w, "InvoiceLine", alle=True) + _pfad(w, "CreditNoteLine", alle=True):
        r["positionen"].append({
            "menge": zahl(_txt(z, "InvoicedQuantity") or _txt(z, "CreditedQuantity")) or D("1"),
            "einheit": "",
            "beschreibung": _txt(z, "Item/Name"),
            "einzelpreis": zahl(_txt(z, "Price/PriceAmount")),
            "steuersatz": zahl(_txt(z, "Item/ClassifiedTaxCategory/Percent")),
            "betrag": zahl(_txt(z, "LineExtensionAmount")),
        })
    # UBL: strukturierter Skonto (PaymentTerms/SettlementDiscountPercent + SettlementPeriod)
    prozent = zahl(_txt(w, "PaymentTerms/SettlementDiscountPercent"))
    if prozent:
        r["skonto"] = {"prozent": prozent, "tage": None,
                       "bis": datum(_txt(w, "PaymentTerms/SettlementPeriod/EndDate")),
                       "basis": None, "betrag": zahl(_txt(w, "PaymentTerms/SettlementDiscountAmount"))}
    return r


def aus_xml(xml):
    """E-Rechnung (CII/UBL) → Rohwerte (dict) oder None."""
    from lxml import etree

    try:
        w = etree.fromstring(xml, parser=etree.XMLParser(resolve_entities=False, no_network=True))
    except (etree.XMLSyntaxError, ValueError):
        return None
    name = etree.QName(w).localname
    if name == "CrossIndustryInvoice":
        return _xml_cii(w)
    if name in ("Invoice", "CreditNote"):
        return _xml_ubl(w)
    return None


# ------------------------------------------------------------------ Text (Regex / Tabellenheuristik)

RE_RECHNUNGSNR = re.compile(
    r"\b(?:Rechnungs[\s-]?(?:nummer|nr\.?)|Rechnung\s+Nr\.?|Re\.?[\s-]?Nr\.?|Beleg(?:nummer|nr\.?)|"
    r"Invoice\s*(?:No\.?|Number))\s*[:#.]?\s*([A-Z0-9][A-Z0-9\-/_.]{1,40})", re.I)
RE_RECHNUNGSDATUM = re.compile(r"\b(?:Rechnungsdatum|Belegdatum|Datum(?:\s+der\s+Rechnung)?|Invoice\s+date)\s*[:.]?\s*"
                               rf"({DATUM})", re.I)
RE_FAELLIG = re.compile(rf"(?:f[äa]llig\s+(?:am|bis|zum)?|zahlbar\s+bis(?:\s+zum)?|bis\s+zum|Zahlungsziel|"
                        rf"F[äa]lligkeit(?:sdatum)?)\s*[:.]?\s*({DATUM})", re.I)
RE_TAGE = re.compile(r"(?:innerhalb|binnen)\s+(?:von\s+)?(\d{1,3})\s+Tag(?:en)?|(\d{1,3})\s+Tage\s+netto", re.I)
_PROZ = r"(?P<p>\d{1,2}(?:[,.]\d{1,2})?)\s?%"
_ABZUG = r"(?:abz(?:üglich|ugl\.?|gl\.?)\s+|mit\s+|unter\s+Abzug\s+von\s+)?"
# Skontoangaben in üblichen Schreibweisen (Reihenfolge = Priorität bei Überschneidung)
RE_SKONTO_LISTE = [
    # XRechnung-Konvention (BT-20): #SKONTO#TAGE=14#PROZENT=2.00#[BASISBETRAG=119.00#]
    re.compile(r"#SKONTO#TAGE=(?P<t>\d{1,3})#PROZENT=(?P<p>\d{1,2}(?:\.\d{1,2})?)#"
               r"(?:BASISBETRAG=(?P<b>-?\d+(?:\.\d{1,2})?)#)?", re.I),
    # „2 % Skonto bei Zahlung bis 24.11.2026“ / „2 % Skonto bis zum 24.11.2026“
    re.compile(rf"{_PROZ}\s+Skonto\s+(?:bei\s+Zahlung\s+)?(?:bis(?:\s+zum)?|am)\s+(?P<d>{DATUM})", re.I),
    # „2 % Skonto (bei Zahlung) innerhalb von 10 Tagen“
    re.compile(rf"{_PROZ}\s+Skonto\s+(?:bei\s+Zahlung\s+)?(?:innerhalb|binnen)\s+(?:von\s+)?(?P<t>\d{{1,3}})\s+Tag",
               re.I),
    # „bei Zahlung bis 24.11.2026 abzüglich 2 % Skonto“
    re.compile(rf"(?:Zahlung\s+)?bis(?:\s+zum)?\s+(?P<d>{DATUM})\s+{_ABZUG}{_PROZ}\s+Skonto", re.I),
    # „14 Tage 2 % Skonto“ / „innerhalb von 14 Tagen abzüglich 2 % Skonto“ / „Zahlbar in 14 Tagen mit 2 % Skonto“
    re.compile(rf"(?:(?:innerhalb|binnen|in)\s+(?:von\s+)?)?(?P<t>\d{{1,3}})\s+Tag(?:e|en)?\s*[:,]?\s+{_ABZUG}"
               rf"{_PROZ}\s+Skonto", re.I),
]


def skonto_aus_text(text):
    """Erste Skontoangabe im Text → {"prozent", "tage", "bis", "basis", "spanne"} oder None.

    Erkennt u. a. „14 Tage 2 % Skonto, 30 Tage netto“, „2 % Skonto innerhalb 10 Tagen“,
    „2 % Skonto bei Zahlung bis 24.11.2026“ und die XRechnung-Konvention „#SKONTO#TAGE=14#PROZENT=2.00#“.
    """
    treffer = []
    for rang, muster in enumerate(RE_SKONTO_LISTE):
        for m in muster.finditer(text or ""):
            treffer.append((m.start(), rang, m))
    if not treffer:
        return None
    _, _, m = min(treffer, key=lambda t: (t[0], t[1]))
    g = m.groupdict()
    prozent = zahl(g["p"]) if "," in g["p"] else D(g["p"])
    if not prozent or prozent <= 0 or prozent >= 100:
        return None
    return {"prozent": prozent, "tage": int(g["t"]) if g.get("t") else None,
            "bis": datum(g["d"]) if g.get("d") else None,
            "basis": D(g["b"]) if g.get("b") else None, "spanne": m.span()}


def skonto_bereiche(text):
    """Textstellen aller Skontoangaben (damit Skontofristen nicht als Zahlungsziel/Fälligkeit gelten)."""
    return [m.span() for muster in RE_SKONTO_LISTE for m in muster.finditer(text or "")]


def _proz_text(p):
    return f"{D(p).normalize():f}".replace(".", ",")


def skonto_bedingung(sk):
    """Lesbare Kurzform: „2 % Skonto innerhalb 10 Tagen“ bzw. „2 % Skonto bis 24.11.2026“."""
    if sk.get("tage") is not None:
        return f"{_proz_text(sk['prozent'])} % Skonto innerhalb {sk['tage']} Tagen"
    if sk.get("bis"):
        return f"{_proz_text(sk['prozent'])} % Skonto bis {sk['bis']:%d.%m.%Y}"
    return f"{_proz_text(sk['prozent'])} % Skonto"


RE_BESTELLNR = re.compile(r"\b(?:Ihre\s+|Unsere\s+)?(?:Bestell(?:ung|nummer|[\s-]?Nr\.?)|Auftrags?[\s-]?Nr\.?|"
                          r"Order\s*(?:No\.?|Number))\s*(?:Nr\.?\s*)?[:#.]?\s*([A-Z0-9][A-Z0-9\-/_.]{2,40})", re.I)
RE_UST_ID = re.compile(r"\b(DE\s?\d{3}\s?\d{3}\s?\d{3}|ATU\d{8}|(?:FR|IT|NL|BE|LU|ES|PL|CZ|DK)[0-9A-Z]{8,12})\b")
RE_IBAN = re.compile(r"\b([A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,3})?)\b")
RE_NETTO = re.compile(r"^\s*(?:Summe\s+netto|Netto[\s-]?(?:betrag|summe|wert)?|Zwischensumme|Warenwert|"
                      r"Gesamt\s+netto|Rechnungsbetrag\s+netto)\b", re.I)
RE_BRUTTO = re.compile(r"^\s*(?:Gesamt[\s-]?(?:betrag|summe)|Rechnungsbetrag|Brutto[\s-]?(?:betrag|summe)?|"
                       r"Summe\s+brutto|Endbetrag|zu\s+zahlen(?:der\s+Betrag)?|Zahlbetrag|Gesamt\s+brutto)\b", re.I)
RE_STEUER = re.compile(r"(?:USt\.?|MwSt\.?|Umsatzsteuer|Mehrwertsteuer|Vorsteuer)\s*\.?\s*(\d{1,2}(?:,\d{1,2})?)\s?%",
                       re.I)
RE_STEUER_SUMME = re.compile(r"^\s*(?:Summe\s+(?:USt|MwSt|Umsatzsteuer)|(?:USt|MwSt)[\s-]?(?:Betrag|Summe)|"
                             r"Steuerbetrag)\b(?!.*%)", re.I)
EINHEITEN = r"Stk\.?|St\.?|Stück|kg|g|t|l|ml|m|m²|m2|qm|h|Std\.?|Pck\.?|Paket|Karton|Ktn\.?|Psch\.?|pauschal|Pal\.?|x"
RE_POSITION = re.compile(
    rf"^\s*(?:(?P<pos>\d{{1,3}})\s+)?(?P<menge>{MENGE})\s+(?:(?P<einheit>{EINHEITEN})\s+)?"
    rf"(?P<text>\S.*?)\s+(?P<ep>{EINZELPREIS})(?:\s?(?:€|EUR))?\s+(?:(?P<satz>\d{{1,2}})\s?%\s+)?"
    rf"(?P<betrag>{BETRAG})(?:\s?(?:€|EUR))?(?:\s+(?P<satz2>\d{{1,2}})\s?%)?\s*$", re.I)


def _letzter_betrag(zeile):
    treffer = re.findall(BETRAG, zeile)
    return zahl(treffer[-1]) if treffer else None


def _ibans(text):
    """Alle gültigen IBANs im Text (Leerzeichen-Gruppen werden passend gekürzt)."""
    gefunden = []
    for m in RE_IBAN.finditer(text.upper()):
        roh = re.sub(r"\s", "", m.group(1))
        laenge = IBAN_LAENGEN.get(roh[:2])
        kandidaten = [roh[:laenge]] if laenge else [roh[:n] for n in range(len(roh), 14, -1)]
        for k in kandidaten:
            if iban_gueltig(k) and k not in gefunden:
                gefunden.append(k)
                break
    return gefunden


def positionen_aus_text(text):
    """Tabellenheuristik: jede Zeile mit Menge, Beschreibung, Einzelpreis, [Steuersatz] und Betrag."""
    ergebnis = []
    for zeile in text.splitlines():
        if RE_NETTO.search(zeile) or RE_BRUTTO.search(zeile) or RE_STEUER.search(zeile.split("   ")[0]):
            continue
        m = RE_POSITION.match(zeile)
        if not m:
            continue
        menge, ep, betrag = zahl(m["menge"]), zahl(m["ep"]), zahl(m["betrag"])
        if menge is None or ep is None or betrag is None or menge == 0:
            continue
        rechnerisch = (menge * ep).quantize(CENT)
        stimmt = abs(rechnerisch - betrag) <= CENT
        # Ohne rechnerische Bestätigung nur, wenn die Zeile eindeutig tabellarisch aussieht
        if not stimmt and not m["pos"]:
            continue
        satz = m["satz"] or m["satz2"]
        ergebnis.append({
            "menge": menge, "einheit": (m["einheit"] or "").rstrip("."), "beschreibung": m["text"].strip(" .-"),
            "einzelpreis": ep, "betrag": betrag, "steuersatz": zahl(satz) if satz else None,
            "konfidenz": 0.95 if stimmt else 0.6,
        })
    return ergebnis


def aus_text(text, eigene=None):
    """Regelbasierte Extraktion aus dem Volltext → (Rohwerte, Konfidenzen)."""
    eigene = eigene or {}
    zeilen = [z for z in (text or "").splitlines() if z.strip()]
    r, k = {}, {}

    m = RE_RECHNUNGSNR.search(text)
    if m:
        r["rechnungsnummer"], k["rechnungsnummer"] = m.group(1).rstrip(".,"), 0.95

    m = RE_RECHNUNGSDATUM.search(text)
    if m and datum(m.group(1)):
        r["rechnungsdatum"], k["rechnungsdatum"] = datum(m.group(1)), 0.95
    else:
        alle = [datum(d) for d in re.findall(DATUM, text)]
        alle = [d for d in alle if d]
        if alle:
            r["rechnungsdatum"], k["rechnungsdatum"] = alle[0], 0.5

    # Zahlungsziel: längste Tagesfrist außerhalb einer Skontoangabe („2 % Skonto innerhalb 10 Tagen“)
    bereiche = skonto_bereiche(text)

    def im_skonto(pos):
        return any(a <= pos < e for a, e in bereiche)

    tage = [int(m.group(1) or m.group(2)) for m in RE_TAGE.finditer(text) if not im_skonto(m.start())]
    if tage:
        r["zahlungsziel_tage"], k["zahlungsziel_tage"] = max(tage), 0.9
    m = next((m for m in RE_FAELLIG.finditer(text) if datum(m.group(1)) and not im_skonto(m.start(1))), None)
    if m:
        r["faellig_am"], k["faellig_am"] = datum(m.group(1)), 0.9
    elif r.get("zahlungsziel_tage") is not None and r.get("rechnungsdatum"):
        r["faellig_am"] = r["rechnungsdatum"] + timedelta(days=r["zahlungsziel_tage"])
        k["faellig_am"] = 0.8
    bedingung = []
    sk = skonto_aus_text(text)
    if sk:
        r["skonto"] = sk
        bedingung.append(skonto_bedingung(sk))
    if r.get("zahlungsziel_tage") is not None:
        bedingung.append(f"{r['zahlungsziel_tage']} Tage netto")
    if bedingung:
        r["zahlungsbedingung"], k["zahlungsbedingung"] = ", ".join(bedingung), 0.85

    for m in RE_BESTELLNR.finditer(text):
        if re.search(r"\d", m.group(1)) and not datum(m.group(1)):
            r["bestellnummer"], k["bestellnummer"] = m.group(1).rstrip(".,"), 0.9
            break

    eigene_ust = ust_id_normal(eigene.get("ust_id"))
    for kandidat in RE_UST_ID.findall(text):
        u = ust_id_normal(kandidat)
        if u and u != eigene_ust:
            r["ust_id"], k["ust_id"] = u, 0.95
            break
    eigene_iban = re.sub(r"\s", "", eigene.get("iban") or "").upper()
    for iban in _ibans(text):
        if iban != eigene_iban:
            r["iban"], k["iban"] = iban, 0.95
            break

    # Lieferant: erste Kopfzeile (Briefkopf) – nur grober Anhaltspunkt für die Namenssuche
    eigener_name = (eigene.get("name") or "").lower()
    for z in zeilen[:5]:
        z = z.strip()
        if len(z) > 3 and not re.search(r"\d{4,}|rechnung", z, re.I) and z.lower() != eigener_name:
            r["lieferant_name"], k["lieferant_name"] = z, 0.5
            break

    # Summen: letzter Betrag der beschrifteten Zeile; bei mehreren Treffern zählt der letzte
    steuersaetze = {}
    for z in zeilen:
        betrag = _letzter_betrag(z)
        if betrag is None:
            continue
        if RE_BRUTTO.search(z):
            r["brutto"], k["brutto"] = betrag, 0.9
        elif RE_NETTO.search(z):
            r["netto"], k["netto"] = betrag, 0.9
        elif RE_STEUER_SUMME.search(z):
            r["steuer"], k["steuer"] = betrag, 0.9
        else:
            m = RE_STEUER.search(z)
            if m and not RE_POSITION.match(z):
                satz = zahl(m.group(1))
                betraege = [zahl(b) for b in re.findall(BETRAG, z[m.end():])]
                basis = betraege[0] if len(betraege) >= 2 else None
                steuersaetze[satz] = {"satz": satz, "basis": basis, "betrag": betraege[-1] if betraege else betrag}
    r["steuersaetze"] = sorted(steuersaetze.values(), key=lambda s: -s["satz"])
    if "steuer" not in r and r["steuersaetze"]:
        r["steuer"], k["steuer"] = sum((s["betrag"] for s in r["steuersaetze"]), NULL), 0.85
    # Fehlende Summe rechnerisch ergänzen (geringere Konfidenz)
    n, s, b = r.get("netto"), r.get("steuer"), r.get("brutto")
    if b is None and n is not None and s is not None:
        r["brutto"], k["brutto"] = n + s, 0.6
    elif n is None and b is not None and s is not None:
        r["netto"], k["netto"] = b - s, 0.6
    elif s is None and n is not None and b is not None:
        r["steuer"], k["steuer"] = b - n, 0.6

    r["positionen"] = positionen_aus_text(text)
    return r, k


def zahlungsziel(text):
    """Längste Tagesfrist außerhalb von Skontoangaben („14 Tage 2 % Skonto, 30 Tage netto“ → 30) oder None."""
    bereiche = skonto_bereiche(text)
    tage = [int(m.group(1) or m.group(2)) for m in RE_TAGE.finditer(text or "")
            if not any(a <= m.start() < e for a, e in bereiche)]
    return max(tage) if tage else None


def skonto_berechnen(prozent, tage=None, bis=None, rechnungsdatum=None, brutto=None, basis=None, betrag=None):
    """Skontofrist und -betrag ergänzen → (bis, betrag); Basis ist der Bruttobetrag (bzw. Skontobasis lt. XML)."""
    if bis is None and tage is not None and rechnungsdatum:
        bis = rechnungsdatum + timedelta(days=int(tage))
    if not betrag and prozent and (basis if basis is not None else brutto) is not None:
        betrag = (D(basis if basis is not None else brutto) * D(prozent) / 100).quantize(CENT)
    return bis, betrag


def _skonto_uebernehmen(roh, konf, quelle):
    """Erkannten Skonto (roh["skonto"]) in die Kopffelder skonto_prozent/_tage/_bis/_betrag übertragen."""
    sk = roh.get("skonto")
    if not sk or not sk.get("prozent"):
        return
    bis, betrag = skonto_berechnen(sk["prozent"], sk.get("tage"), sk.get("bis"), roh.get("rechnungsdatum"),
                                   roh.get("brutto"), sk.get("basis"), sk.get("betrag"))
    roh.update(skonto_prozent=sk["prozent"], skonto_tage=sk.get("tage"), skonto_bis=bis, skonto_betrag=betrag)
    if quelle != "xml":
        konf.update(skonto_prozent=0.85, skonto_tage=0.85, skonto_bis=0.85 if bis else 0.0,
                    skonto_betrag=0.8 if betrag is not None else 0.0)


def skonto_ergaenzen(felder):
    """Nach einer Korrektur: fehlende Skontofrist/-betrag aus Satz, Tagen, Rechnungsdatum und Brutto ableiten;
    ohne Skontosatz werden die abhängigen Felder entfernt."""
    prozent = feldwert(felder, "skonto_prozent", "betrag")
    if not prozent:
        for name in ("skonto_prozent", "skonto_tage", "skonto_bis", "skonto_betrag"):
            felder.pop(name, None)
        return felder
    bis, betrag = skonto_berechnen(prozent, feldwert(felder, "skonto_tage", "int"),
                                   feldwert(felder, "skonto_bis", "datum"),
                                   feldwert(felder, "rechnungsdatum", "datum"), feldwert(felder, "brutto", "betrag"),
                                   betrag=feldwert(felder, "skonto_betrag", "betrag"))
    if bis and "skonto_bis" not in felder:
        felder["skonto_bis"] = _feld(bis, 0.8)
    if betrag is not None and "skonto_betrag" not in felder:
        felder["skonto_betrag"] = _feld(betrag, 0.8)
    return felder


def extrahieren(text="", xml=None, eigene=None):
    """Text und/oder E-Rechnungs-XML → {"quelle", "felder", "positionen", "steuersaetze"} (ohne Datenbank)."""
    roh, konf, quelle = None, {}, "text"
    if xml:
        roh = aus_xml(xml)
        if roh:
            quelle = "xml"
            konf = {f: 1.0 for f in FELDER_KOPF}
            tage = zahlungsziel(roh.get("zahlungsbedingung"))
            if tage is not None:
                roh["zahlungsziel_tage"] = tage
                if roh.get("faellig_am") is None and roh.get("rechnungsdatum"):
                    roh["faellig_am"] = roh["rechnungsdatum"] + timedelta(days=tage)
            sk = roh.get("skonto") or skonto_aus_text(roh.get("zahlungsbedingung"))
            if sk:
                roh["skonto"] = sk
                roh["zahlungsbedingung"] = roh.get("zahlungsbedingung") or skonto_bedingung(sk)
            for p in roh["positionen"]:
                p["konfidenz"] = 1.0
    if roh is None:
        roh, konf = aus_text(text or "", eigene)
    _skonto_uebernehmen(roh, konf, quelle)
    felder = {}
    for name in FELDER_KOPF:
        wert = roh.get(name)
        if wert not in (None, "", []):
            felder[name] = _feld(wert, konf.get(name, 0.9))
    if "ust_id" in felder:
        felder["ust_id"]["wert"] = ust_id_normal(felder["ust_id"]["wert"])
    if "iban" in felder:
        felder["iban"]["wert"] = re.sub(r"\s", "", felder["iban"]["wert"]).upper()
    # Steuersatz für Positionen ohne eigenen Satz: einziger Satz der Rechnung
    saetze = [s["satz"] for s in roh.get("steuersaetze", []) if s.get("satz") is not None]
    positionen = []
    for p in roh.get("positionen", []):
        satz = p.get("steuersatz")
        if satz is None and len(saetze) == 1:
            satz = saetze[0]
        positionen.append({
            "menge": _num(p["menge"], D("0.001")), "einheit": p.get("einheit", ""),
            "beschreibung": (p.get("beschreibung") or "")[:200],
            "einzelpreis": _num(p.get("einzelpreis") if p.get("einzelpreis") is not None else p.get("betrag"),
                                D("0.0001")),
            "betrag": _num(p.get("betrag")), "steuersatz": _num(satz, D("1")) if satz is not None else None,
            "konfidenz": p.get("konfidenz", 0.9),
        })
    steuersaetze = [{"satz": _num(s["satz"], D("1")), "basis": _num(s.get("basis")), "betrag": _num(s.get("betrag"))}
                    for s in roh.get("steuersaetze", []) if s.get("satz") is not None]
    return {"quelle": quelle, "felder": felder, "positionen": positionen, "steuersaetze": steuersaetze}


# ------------------------------------------------------------------ Datenbankbezogene Schritte

def _eigene_firma():
    from apps.core.models import Mandant

    m = Mandant.aktuell()
    return {"name": m.name, "ust_id": m.ust_id, "iban": m.iban} if m else {}


def lieferant_zuordnen(felder, text=""):
    """Lieferant per USt-IdNr. > IBAN > Name → {"partner_id", "name", "methode", "konfidenz"} oder {}."""
    from apps.stammdaten.models import Geschaeftspartner

    lieferanten = Geschaeftspartner.objects.filter(ist_lieferant=True, aktiv=True)
    ust = ust_id_normal(feldwert(felder, "ust_id"))
    if ust:
        p = lieferanten.filter(ust_id__iexact=ust).first()
        if p:
            return {"partner_id": p.pk, "name": p.name, "methode": "USt-IdNr.", "konfidenz": 0.99}
    iban = (feldwert(felder, "iban") or "").replace(" ", "").upper()
    if iban:
        for p in lieferanten.exclude(iban=""):
            if p.iban.replace(" ", "").upper() == iban:
                return {"partner_id": p.pk, "name": p.name, "methode": "IBAN", "konfidenz": 0.95}
    name = (feldwert(felder, "lieferant_name") or "").lower()
    kopf = "\n".join((text or "").splitlines()[:15]).lower()
    bester, quote = None, 0.0
    for p in lieferanten.only("pk", "name"):
        pn = p.name.lower()
        q = difflib.SequenceMatcher(None, pn, name).ratio() if name else 0.0
        if len(pn) >= 5 and pn in kopf:
            q = max(q, 0.9)
        if q > quote:
            bester, quote = p, q
    if bester and quote >= 0.8:
        return {"partner_id": bester.pk, "name": bester.name, "methode": "Name",
                "konfidenz": round(min(quote, 0.9) * 0.9, 2)}
    return {}


def kontierung_vorschlagen(partner, positionen):
    """Kontierungsvorschlag je Position: Lernregel (Stichwort) > Lernregel (Lieferant) > frühere Buchung
    des Lieferanten > allgemeine Kontierungsregel AUFWAND. Ergänzt konto, steuerschluessel, kontoquelle."""
    from apps.fibu import services as F
    from apps.fibu.models import ERPosition

    from .models import Kontierungsregel

    regeln = list(Kontierungsregel.objects.filter(partner=partner).select_related("konto")) if partner else []
    frueher = None
    if partner:
        frueher = (ERPosition.objects.filter(rechnung__lieferant=partner, rechnung__status="GEBUCHT",
                                             konto__isnull=False)
                   .values("konto__nummer").annotate(n=Count("pk")).order_by("-n").first())
    for p in positionen:
        try:
            code = F.schluessel_fuer_satz(p.get("steuersatz") or 0, einkauf=True)
        except F.FibuFehler:
            code = ""
        text = (p.get("beschreibung") or "").lower()
        passend = [r for r in regeln if r.stichwort and r.stichwort.lower() in text]
        allgemein = [r for r in regeln if not r.stichwort]
        regel = max(passend or allgemein, key=lambda r: (r.anzahl, r.zuletzt), default=None)
        if regel:
            p["konto"], p["kontoquelle"] = regel.konto.nummer, (
                f"Lernregel „{regel.stichwort}“" if regel.stichwort else "Lernregel Lieferant")
            if regel.steuerschluessel and not code:
                code = regel.steuerschluessel
        elif frueher:
            p["konto"], p["kontoquelle"] = frueher["konto__nummer"], "frühere Buchungen"
        else:
            try:
                p["konto"], p["kontoquelle"] = F.regel_konto("AUFWAND", "", code).nummer, "Standard-Aufwand"
            except F.FibuFehler:
                p["konto"], p["kontoquelle"] = "", ""
        p["steuerschluessel"] = code
    return positionen


def _dubletten(dokument, partner_id, rechnungsnummer):
    from apps.fibu.models import Eingangsrechnung

    from .models import Dokument

    treffer = []
    if not (partner_id and rechnungsnummer):
        return treffer
    for er in Eingangsrechnung.objects.filter(lieferant_id=partner_id, rechnungsnummer__iexact=rechnungsnummer):
        if not (dokument and dokument.eingangsrechnung_id == er.pk):
            treffer.append(f"Eingangsrechnung {er.nummer}")
    andere = (Dokument.objects.filter(partner_id=partner_id, erkennung__felder__rechnungsnummer__wert=rechnungsnummer)
              .exclude(status=Dokument.Status.FEHLER).exclude(aufgeteilt=True))
    if dokument and dokument.pk:
        andere = andere.exclude(pk=dokument.pk)
    treffer += [f"Dokument {d.nummer}" for d in andere[:5]]
    return treffer


def plausibilitaet(erg, dokument=None):
    """Prüfungen → Liste {"stufe": "fehler"|"warnung"|"ok", "text"}. Fehler verhindern die Verbuchung."""
    from apps.stammdaten.models import Geschaeftspartner

    f, meldungen = erg.get("felder", {}), []

    def melden(stufe, text):
        meldungen.append({"stufe": stufe, "text": text})

    partner_id = (erg.get("lieferant") or {}).get("partner_id")
    if not partner_id:
        melden("fehler", "Kein Lieferant zugeordnet (USt-IdNr./IBAN/Name nicht gefunden) – bitte auswählen.")
    for name, titel in (("rechnungsnummer", "Rechnungsnummer"), ("rechnungsdatum", "Rechnungsdatum"),
                        ("brutto", "Bruttobetrag")):
        if feldwert(f, name) in (None, ""):
            melden("fehler", f"{titel} fehlt.")
    netto, steuer, brutto = (feldwert(f, n, "betrag") for n in ("netto", "steuer", "brutto"))
    if None not in (netto, steuer, brutto):
        if abs(netto + steuer - brutto) > CENT:
            melden("fehler", f"Summen stimmen nicht: Netto {netto} + Steuer {steuer} ≠ Brutto {brutto}.")
        else:
            melden("ok", "Netto + Steuer = Brutto.")
    positionen = erg.get("positionen") or []
    if not positionen:
        melden("fehler", "Keine Positionen erkannt – bitte mindestens eine Position erfassen.")
    elif netto is not None:
        summe = sum((zahl(p.get("betrag")) or NULL for p in positionen), NULL)
        if abs(summe - netto) > CENT:
            melden("fehler", f"Summe der Positionen {summe} weicht vom Nettobetrag {netto} ab.")
        else:
            melden("ok", "Summe der Positionen = Nettobetrag.")
    for p_nr, p in enumerate(positionen, 1):
        menge, ep, betrag = zahl(p.get("menge")), zahl(p.get("einzelpreis")), zahl(p.get("betrag"))
        if None not in (menge, ep, betrag) and abs((menge * ep).quantize(CENT) - betrag) > CENT:
            melden("warnung", f"Position {p_nr}: Menge × Einzelpreis ≠ Betrag.")
        if p.get("steuersatz") is None:
            melden("fehler", f"Position {p_nr}: Steuersatz fehlt.")
        elif not p.get("steuerschluessel") and "steuerschluessel" in p:
            melden("fehler", f"Position {p_nr}: kein Steuerschlüssel für {p.get('steuersatz')} % – bitte wählen.")
    # Steuer je Satz: aus Positionen berechnet vs. ausgewiesen
    if positionen and steuer is not None:
        je_satz = {}
        for p in positionen:
            if p.get("steuersatz") is not None:
                s = zahl(p["steuersatz"])
                je_satz[s] = je_satz.get(s, NULL) + (zahl(p.get("betrag")) or NULL)
        berechnet = sum(((b * s / 100).quantize(CENT) for s, b in je_satz.items()), NULL)
        if abs(berechnet - steuer) > D("0.05"):
            melden("warnung", f"Ausgewiesene Steuer {steuer} weicht von der berechneten Steuer {berechnet} ab.")
    rd, fa = feldwert(f, "rechnungsdatum", "datum"), feldwert(f, "faellig_am", "datum")
    if rd and rd > timezone.localdate():
        melden("warnung", "Rechnungsdatum liegt in der Zukunft.")
    if rd and fa and fa < rd:
        melden("warnung", "Fälligkeit liegt vor dem Rechnungsdatum.")
    sp, sb = feldwert(f, "skonto_prozent", "betrag"), feldwert(f, "skonto_bis", "datum")
    if sp:
        if sp > 10:
            melden("warnung", f"Skontosatz {sp} % ist ungewöhnlich hoch – bitte prüfen.")
        if not sb:
            melden("warnung", "Skontosatz ohne Skontofrist – der Skonto wird nicht in den offenen Posten übernommen.")
        elif rd and sb < rd:
            melden("warnung", "Skontofrist liegt vor dem Rechnungsdatum.")
        elif fa and sb > fa:
            melden("warnung", "Skontofrist endet nach der Fälligkeit.")
        else:
            sbetrag = feldwert(f, "skonto_betrag", "betrag")
            melden("ok", f"Skonto {_proz_text(sp)} % bis {sb:%d.%m.%Y}"
                         + (f" ({sbetrag} €)" if sbetrag is not None else "") + " wird in den offenen Posten übernommen.")
    if partner_id:
        p = Geschaeftspartner.objects.filter(pk=partner_id).first()
        iban = feldwert(f, "iban")
        if p and p.iban and iban and p.iban.replace(" ", "").upper() != iban:
            melden("warnung", f"IBAN der Rechnung ({iban}) weicht von der hinterlegten IBAN des Lieferanten ab – "
                              "bitte prüfen (Betrugsschutz).")
        if p and not p.ist_lieferant:
            melden("warnung", f"{p.name} ist nicht als Lieferant gekennzeichnet.")
    dubletten = _dubletten(dokument, partner_id, feldwert(f, "rechnungsnummer"))
    if dubletten:
        melden("fehler", "Mögliche Dublette (gleicher Lieferant und Rechnungsnummer): " + ", ".join(dubletten) + ".")
    return meldungen


def gesamtkonfidenz(erg):
    f = erg.get("felder", {})
    werte = [(erg.get("lieferant") or {}).get("konfidenz", 0.0)]
    werte += [(f.get(n) or {}).get("konfidenz", 0.0) for n in ("rechnungsnummer", "rechnungsdatum", "netto",
                                                               "steuer", "brutto")]
    pos = erg.get("positionen") or []
    werte.append(min((p.get("konfidenz", 0.0) for p in pos), default=0.0))
    k = sum(werte) / len(werte)
    if any(m["stufe"] == "fehler" for m in erg.get("plausibilitaet", [])):
        k *= 0.5
    return D(str(round(k, 2)))


def bewerten(erg, dokument=None):
    """Kontierungsvorschlag, Plausibilität und Konfidenz (neu) berechnen – auch nach Korrekturen."""
    from apps.stammdaten.models import Geschaeftspartner

    partner_id = (erg.get("lieferant") or {}).get("partner_id")
    partner = Geschaeftspartner.objects.filter(pk=partner_id).first() if partner_id else None
    erg["positionen"] = kontierung_vorschlagen(partner, erg.get("positionen") or []) if not erg.get("korrigiert") \
        else erg.get("positionen") or []
    erg["plausibilitaet"] = plausibilitaet(erg, dokument)
    # Dreifach-Abgleich Bestellung/Wareneingang (nur Hinweise; die Sperre greift bei der Verbuchung)
    from . import abgleich as A

    try:
        erg["plausibilitaet"] += A.meldungen(A.berechnen(erg, dokument, partner))
    except Exception as exc:  # noqa: BLE001 – Abgleich darf die Erkennung nie abbrechen
        log.exception("Dreifach-Abgleich fehlgeschlagen")
        erg["plausibilitaet"].append({"stufe": "warnung", "text": f"Dreifach-Abgleich nicht möglich: {exc}"})
    erg["gesamtkonfidenz"] = str(gesamtkonfidenz(erg))
    return erg


def erkennen(dokument, ocr_ergebnis=None, benutzer=None):
    """Belegerkennung für ein Dokument ausführen, Ergebnis speichern und Status ERKANNT/PRUEFEN setzen."""
    from . import services as S
    from .models import Dokument

    xml = getattr(ocr_ergebnis, "xml", None)
    erg = extrahieren(dokument.ocr_text, xml, _eigene_firma())
    erg["lieferant"] = lieferant_zuordnen(erg["felder"], dokument.ocr_text)
    if not erg["lieferant"] and dokument.partner_id:
        erg["lieferant"] = {"partner_id": dokument.partner_id, "name": dokument.partner.name,
                            "methode": "manuell (Upload)", "konfidenz": 1.0}
    erg["erkannt_am"] = timezone.now().isoformat()
    bewerten(erg, dokument)
    konfidenz = D(erg["gesamtkonfidenz"])
    fehler = [m for m in erg["plausibilitaet"] if m["stufe"] == "fehler"]
    status = Dokument.Status.ERKANNT if konfidenz >= SCHWELLE_ERKANNT and not fehler else Dokument.Status.PRUEFEN
    aenderung = {"erkennung": erg, "konfidenz": konfidenz, "status": status}
    if erg["lieferant"].get("partner_id") and not dokument.partner_id:
        aenderung["partner_id"] = erg["lieferant"]["partner_id"]
    rd = feldwert(erg["felder"], "rechnungsdatum", "datum")
    if rd and not dokument.belegdatum:
        aenderung["belegdatum"] = rd
    Dokument.objects.filter(pk=dokument.pk).update(**aenderung)
    dokument.refresh_from_db()
    f = erg["felder"]
    S.protokollieren(dokument, "Belegerkennung",
                     f"Quelle {erg['quelle']}, Lieferant: {erg['lieferant'].get('name') or '–'}"
                     f" ({erg['lieferant'].get('methode') or 'nicht zugeordnet'}), "
                     f"Rechnung {feldwert(f, 'rechnungsnummer') or '–'}, Brutto {feldwert(f, 'brutto') or '–'}, "
                     f"{len(erg['positionen'])} Position(en), Konfidenz {konfidenz}, "
                     f"{len(fehler)} Plausibilitätsfehler → {dokument.get_status_display()}.", benutzer)
    return erg

"""Erzeugung von SEPA-XML (pain.001.001.09 Überweisung, pain.008.001.08 Lastschrift) inkl. Zeichensatz-
Konvertierung, Betrags-/Längenprüfung und XSD-Validierung gegen die gebündelten ISO-20022-Schemata."""
import re
from decimal import Decimal
from pathlib import Path

from django.utils import timezone
from lxml import etree

XSD_VERZEICHNIS = Path(__file__).resolve().parent / "xsd"
NS = {"pain.001": "urn:iso:std:iso:20022:tech:xsd:pain.001.001.09",
      "pain.008": "urn:iso:std:iso:20022:tech:xsd:pain.008.001.08"}
XSD = {"pain.001": "pain.001.001.09.xsd", "pain.008": "pain.008.001.08.xsd"}
ERSETZEN = {"Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "&": "+", "é": "e",
            "è": "e", "á": "a", "à": "a", "ó": "o", "ç": "c", "ñ": "n", "_": "-", "*": ".", "\n": " "}
UNERLAUBT = re.compile(r"[^A-Za-z0-9 /\-?:().,'+]")
BETRAG_MIN, BETRAG_MAX = Decimal("0.01"), Decimal("999999999.99")


class SepaFehler(Exception):
    """Ungültige Daten für die SEPA-Datei."""


def sepa_text(text, laenge=140):
    """Konvertiert in den SEPA-Zeichensatz (DK-Anlage 3, Basic Latin) und kürzt auf die Maximallänge."""
    text = "".join(ERSETZEN.get(z, z) for z in str(text or ""))
    return re.sub(r"\s+", " ", UNERLAUBT.sub(".", text)).strip()[:laenge]


def sepa_id(text, laenge=35):
    """IDs: SEPA-Zeichensatz, ohne führenden/abschließenden '/' und ohne '//'."""
    t = sepa_text(text, laenge).replace("//", "/").strip("/ ")
    if not t:
        raise SepaFehler("Leere Referenz/ID ist nicht zulässig.")
    return t


def betrag(wert):
    wert = Decimal(wert).quantize(Decimal("0.01"))
    if not BETRAG_MIN <= wert <= BETRAG_MAX:
        raise SepaFehler(f"Betrag {wert} außerhalb der SEPA-Grenzen (0,01 – 999.999.999,99 EUR).")
    return f"{wert:.2f}"


def _e(eltern, tag, text=None, **attr):
    el = etree.SubElement(eltern, tag, **attr)
    if text is not None:
        el.text = str(text)
    return el


def _agent(eltern, tag, bic):
    fin = _e(_e(eltern, tag), "FinInstnId")
    if bic:
        _e(fin, "BICFI", bic)
    else:
        _e(_e(fin, "Othr"), "Id", "NOTPROVIDED")


def _konto(eltern, tag, iban):
    _e(_e(_e(eltern, tag), "Id"), "IBAN", iban.replace(" ", "").upper())


def _kopf(art, msg_id, initiator, transaktionen):
    root = etree.Element("Document", nsmap={None: NS[art]})
    doc = _e(root, "CstmrCdtTrfInitn" if art == "pain.001" else "CstmrDrctDbtInitn")
    kopf = _e(doc, "GrpHdr")
    _e(kopf, "MsgId", sepa_id(msg_id))
    _e(kopf, "CreDtTm", timezone.localtime().replace(microsecond=0).isoformat())
    _e(kopf, "NbOfTxs", len(transaktionen))
    _e(kopf, "CtrlSum", betrag(sum(Decimal(t["betrag"]) for t in transaktionen)))
    _e(_e(kopf, "InitgPty"), "Nm", sepa_text(initiator, 70))
    return root, doc


def pain001(msg_id, pmtinf_id, auftraggeber, iban, bic, datum, transaktionen, sammelbuchung=True):
    """transaktionen: dicts mit e2e, betrag, name, iban, bic, zweck."""
    if not transaktionen:
        raise SepaFehler("Keine Zahlungen ausgewählt.")
    root, doc = _kopf("pain.001", msg_id, auftraggeber, transaktionen)
    pi = _e(doc, "PmtInf")
    _e(pi, "PmtInfId", sepa_id(pmtinf_id))
    _e(pi, "PmtMtd", "TRF")
    _e(pi, "BtchBookg", "true" if sammelbuchung else "false")
    _e(pi, "NbOfTxs", len(transaktionen))
    _e(pi, "CtrlSum", betrag(sum(Decimal(t["betrag"]) for t in transaktionen)))
    _e(_e(_e(pi, "PmtTpInf"), "SvcLvl"), "Cd", "SEPA")
    _e(_e(pi, "ReqdExctnDt"), "Dt", datum.isoformat())
    _e(_e(pi, "Dbtr"), "Nm", sepa_text(auftraggeber, 70))
    _konto(pi, "DbtrAcct", iban)
    _agent(pi, "DbtrAgt", bic)
    _e(pi, "ChrgBr", "SLEV")
    for t in transaktionen:
        tx = _e(pi, "CdtTrfTxInf")
        _e(_e(tx, "PmtId"), "EndToEndId", sepa_id(t["e2e"]))
        _e(_e(tx, "Amt"), "InstdAmt", betrag(t["betrag"]), Ccy="EUR")
        if t.get("bic"):
            _agent(tx, "CdtrAgt", t["bic"])
        _e(_e(tx, "Cdtr"), "Nm", sepa_text(t["name"], 70))
        _konto(tx, "CdtrAcct", t["iban"])
        _e(_e(tx, "RmtInf"), "Ustrd", sepa_text(t["zweck"], 140))
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def pain008(msg_id, glaeubiger, glaeubiger_id, iban, bic, datum, transaktionen, sammelbuchung=True):
    """transaktionen: dicts mit e2e, betrag, name, iban, bic, zweck, mandat, mandat_datum, typ (CORE/B2B),
    sequenz (FRST/RCUR/OOFF/FNAL). Je Kombination Verfahren/Sequenz entsteht ein PmtInf-Block."""
    if not transaktionen:
        raise SepaFehler("Keine Lastschriften ausgewählt.")
    root, doc = _kopf("pain.008", msg_id, glaeubiger, transaktionen)
    gruppen = {}
    for t in transaktionen:
        gruppen.setdefault((t["typ"], t["sequenz"]), []).append(t)
    for (typ, seq), liste in sorted(gruppen.items()):
        pi = _e(doc, "PmtInf")
        _e(pi, "PmtInfId", sepa_id(f"{msg_id}-{typ}-{seq}"))
        _e(pi, "PmtMtd", "DD")
        _e(pi, "BtchBookg", "true" if sammelbuchung else "false")
        _e(pi, "NbOfTxs", len(liste))
        _e(pi, "CtrlSum", betrag(sum(Decimal(t["betrag"]) for t in liste)))
        tp = _e(pi, "PmtTpInf")
        _e(_e(tp, "SvcLvl"), "Cd", "SEPA")
        _e(_e(tp, "LclInstrm"), "Cd", typ)
        _e(tp, "SeqTp", seq)
        _e(pi, "ReqdColltnDt", datum.isoformat())
        _e(_e(pi, "Cdtr"), "Nm", sepa_text(glaeubiger, 70))
        _konto(pi, "CdtrAcct", iban)
        _agent(pi, "CdtrAgt", bic)
        _e(pi, "ChrgBr", "SLEV")
        othr = _e(_e(_e(_e(pi, "CdtrSchmeId"), "Id"), "PrvtId"), "Othr")
        _e(othr, "Id", glaeubiger_id)
        _e(_e(othr, "SchmeNm"), "Prtry", "SEPA")
        for t in liste:
            tx = _e(pi, "DrctDbtTxInf")
            _e(_e(tx, "PmtId"), "EndToEndId", sepa_id(t["e2e"]))
            _e(tx, "InstdAmt", betrag(t["betrag"]), Ccy="EUR")
            mi = _e(_e(tx, "DrctDbtTx"), "MndtRltdInf")
            _e(mi, "MndtId", sepa_id(t["mandat"]))
            _e(mi, "DtOfSgntr", t["mandat_datum"].isoformat())
            _agent(tx, "DbtrAgt", t.get("bic"))
            _e(_e(tx, "Dbtr"), "Nm", sepa_text(t["name"], 70))
            _konto(tx, "DbtrAcct", t["iban"])
            _e(_e(tx, "RmtInf"), "Ustrd", sepa_text(t["zweck"], 140))
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def xsd_pruefen(xml, art):
    """Validiert gegen das gebündelte ISO-20022-Schema. Liefert Liste der Fehler (leer = gültig)."""
    schema = etree.XMLSchema(etree.parse(str(XSD_VERZEICHNIS / XSD[art])))
    ok = schema.validate(etree.fromstring(xml))
    return [] if ok else [f"Zeile {f.line}: {f.message}" for f in schema.error_log]

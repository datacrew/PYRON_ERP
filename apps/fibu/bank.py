"""Bankimport (CAMT.053 / MT940), Dublettenerkennung, Zuordnungsvorschläge und Buchung.

Ablauf: Datei hochladen → Umsätze anlegen (Dubletten werden übersprungen) → Vorschläge prüfen →
zuordnen (OP-Ausgleich inkl. Skonto) oder auf Sachkonto / Verrechnungskonto „ungeklärt“ buchen.
Vom Verrechnungskonto aus kann später noch zugeordnet werden (Umbuchung).
"""
import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.nummernkreise import naechste_nummer

from .models import Bankimport, Bankkonto, Bankumsatz, OffenerPosten
from .services import FibuFehler, automatikkonto, buchen, konto, op_ausgleichen, q


class ParserFehler(FibuFehler):
    pass


# ------------------------------------------------------------------ Parser

def _lokal(tag):
    return tag.rsplit("}", 1)[-1]


def _kind(el, *pfad):
    """Namespace-unabhängige Pfadsuche (erstes Element)."""
    aktuell = [el]
    for name in pfad:
        naechste = []
        for e in aktuell:
            naechste.extend(c for c in e if _lokal(c.tag) == name)
        if not naechste:
            return None
        aktuell = naechste
    return aktuell[0]


def _alle(el, name):
    return [c for c in el.iter() if _lokal(c.tag) == name]


def _text(el, *pfad):
    e = _kind(el, *pfad) if pfad else el
    return (e.text or "").strip() if e is not None else ""


def _datum(el, *pfad):
    t = _text(el, *pfad)
    if not t:
        e = _kind(el, *pfad)
        t = _text(e, "Dt") or _text(e, "DtTm") if e is not None else ""
    return date.fromisoformat(t[:10]) if t else None


def parse_camt053(daten):
    """CAMT.053 (ISO 20022, Versionen 02–08) → {iban, anfangssaldo, endsaldo, von, bis, umsaetze[]}."""
    try:
        wurzel = ET.fromstring(daten)
    except ET.ParseError as exc:
        raise ParserFehler(f"Keine gültige CAMT-XML-Datei: {exc}") from exc
    stmt = next(iter(_alle(wurzel, "Stmt")), None)
    if stmt is None:
        raise ParserFehler("CAMT.053: Kein Kontoauszug (Stmt) gefunden.")
    erg = {"iban": _text(stmt, "Acct", "Id", "IBAN"), "anfangssaldo": None, "endsaldo": None, "umsaetze": []}
    for bal in [c for c in stmt if _lokal(c.tag) == "Bal"]:
        code = _text(bal, "Tp", "CdOrPrtry", "Cd")
        betrag = Decimal(_text(bal, "Amt") or "0")
        if _text(bal, "CdtDbtInd") == "DBIT":
            betrag = -betrag
        if code in ("OPBD", "PRCD"):
            erg["anfangssaldo"] = betrag
        elif code in ("CLBD",):
            erg["endsaldo"] = betrag
    for ntry in [c for c in stmt if _lokal(c.tag) == "Ntry"]:
        betrag = Decimal(_text(ntry, "Amt") or "0")
        haben = _text(ntry, "CdtDbtInd") == "CRDT"
        if not haben:
            betrag = -betrag
        tx = _kind(ntry, "NtryDtls", "TxDtls")
        gegen = "Dbtr" if haben else "Cdtr"
        zweck, name, iban, e2e = "", "", "", ""
        if tx is not None:
            zweck = " ".join(e.text.strip() for e in _alle(tx, "Ustrd") if e.text)
            rp = _kind(tx, "RltdPties")
            if rp is not None:
                name = _text(rp, gegen, "Nm") or _text(rp, gegen, "Pty", "Nm")
                iban = _text(rp, gegen + "Acct", "Id", "IBAN")
            e2e = _text(tx, "Refs", "EndToEndId")
            if e2e == "NOTPROVIDED":
                e2e = ""
        zweck = zweck or _text(ntry, "AddtlNtryInf")
        gvc = _text(ntry, "BkTxCd", "Prtry", "Cd") or _text(ntry, "BkTxCd", "Domn", "Fmly", "SubFmlyCd")
        erg["umsaetze"].append({
            "buchungsdatum": _datum(ntry, "BookgDt"), "valuta": _datum(ntry, "ValDt"),
            "betrag": betrag, "waehrung": (_kind(ntry, "Amt").get("Ccy") if _kind(ntry, "Amt") is not None else "EUR"),
            "name": name, "iban": iban, "verwendungszweck": zweck,
            "referenz": e2e or _text(ntry, "AcctSvcrRef"), "gvc": gvc})
    daten_liste = [u["buchungsdatum"] for u in erg["umsaetze"] if u["buchungsdatum"]]
    erg["von"], erg["bis"] = (min(daten_liste), max(daten_liste)) if daten_liste else (None, None)
    return erg


def _mt_datum(jjmmtt):
    return datetime.strptime(jjmmtt, "%y%m%d").date()


def _mt_betrag(text):
    return Decimal(text.replace(",", "."))


def parse_mt940(daten):
    """MT940 (SWIFT, deutsche Ausprägung mit strukturiertem :86:) → gleiches Format wie parse_camt053."""
    if isinstance(daten, bytes):
        for enc in ("utf-8", "cp1252", "latin-1"):
            try:
                daten = daten.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    felder, aktuell = [], None
    for zeile in daten.replace("\r", "").split("\n"):
        m = re.match(r"^:(\d{2}[A-Z]?):(.*)$", zeile)
        if m:
            aktuell = [m.group(1), m.group(2)]
            felder.append(aktuell)
        elif aktuell is not None and zeile.strip() and zeile.strip() != "-":
            aktuell[1] += "\n" + zeile
    if not any(f[0] == "61" for f in felder) and not any(f[0] == "25" for f in felder):
        raise ParserFehler("Keine gültige MT940-Datei (Felder :25:/:61: fehlen).")
    erg = {"iban": "", "anfangssaldo": None, "endsaldo": None, "umsaetze": []}
    for tag, inhalt in felder:
        if tag == "25":
            konto_id = inhalt.strip()
            erg["iban"] = konto_id if konto_id[:2].isalpha() else konto_id.replace("/", "")
        elif tag in ("60F", "60M", "62F", "62M"):
            m = re.match(r"([CD])(\d{6})([A-Z]{3})([\d,]+)", inhalt.strip())
            if m:
                betrag = _mt_betrag(m.group(4)) * (-1 if m.group(1) == "D" else 1)
                erg["anfangssaldo" if tag.startswith("60") else "endsaldo"] = betrag
        elif tag == "61":
            m = re.match(r"(\d{6})(\d{4})?(R?[CD])([A-Z])?([\d,]+)(N\w{3}|F\w{3}|S\d{3})?([^\n/]*)(//[^\n]*)?",
                         inhalt.strip())
            if not m:
                raise ParserFehler(f"MT940: Feld :61: nicht lesbar: {inhalt[:40]}")
            valuta = _mt_datum(m.group(1))
            buchung = valuta
            if m.group(2):
                buchung = date(valuta.year, int(m.group(2)[:2]), int(m.group(2)[2:]))
                if (buchung - valuta).days > 180:
                    buchung = buchung.replace(year=buchung.year - 1)
                elif (valuta - buchung).days > 180:
                    buchung = buchung.replace(year=buchung.year + 1)
            betrag = _mt_betrag(m.group(5))
            if m.group(3) in ("D", "RC"):
                betrag = -betrag
            referenz = (m.group(7) or "").strip()
            if referenz == "NONREF":
                referenz = ""
            erg["umsaetze"].append({"buchungsdatum": buchung, "valuta": valuta, "betrag": betrag, "waehrung": "EUR",
                                    "name": "", "iban": "", "verwendungszweck": "", "referenz": referenz,
                                    "gvc": (m.group(6) or "")})
        elif tag == "86" and erg["umsaetze"]:
            u = erg["umsaetze"][-1]
            roh = inhalt.replace("\n", "")
            if len(roh) > 3 and roh[3] in "?":
                u["gvc"] = roh[:3]
                teile = {}
                for teil in roh[3:].split("?")[1:]:
                    teile.setdefault(teil[:2], []).append(teil[2:])
                zweck = "".join("".join(teile.get(str(k), [])) for k in list(range(20, 30)) + list(range(60, 64)))
                u["verwendungszweck"] = re.sub(r"\s+", " ", zweck).strip()
                u["name"] = ("".join(teile.get("32", [])) + "".join(teile.get("33", []))).strip()
                u["iban"] = "".join(teile.get("31", [])).strip()
                m = re.search(r"EREF\+(\S+)", u["verwendungszweck"])
                if m and m.group(1) != "NOTPROVIDED" and not u["referenz"]:
                    u["referenz"] = m.group(1)
            else:
                u["verwendungszweck"] = roh.strip()
    daten_liste = [u["buchungsdatum"] for u in erg["umsaetze"]]
    erg["von"], erg["bis"] = (min(daten_liste), max(daten_liste)) if daten_liste else (None, None)
    return erg


def format_erkennen(daten, dateiname=""):
    kopf = daten[:400].lstrip()
    if kopf.startswith(b"<?xml") or kopf.startswith(b"<Document") or b"camt.053" in daten[:2000]:
        return "CAMT053"
    if b":20:" in daten[:500] or b":25:" in daten[:800] or dateiname.lower().endswith((".sta", ".mt940", ".940")):
        return "MT940"
    raise ParserFehler("Dateiformat nicht erkannt – bitte CAMT.053 (XML) oder MT940 hochladen.")


# ------------------------------------------------------------------ Import

def dubletten_schluessel(iban, u):
    roh = "|".join([iban, str(u["buchungsdatum"]), str(q(u["betrag"])), u.get("referenz", ""),
                    re.sub(r"\s+", " ", u.get("verwendungszweck", ""))[:140], u.get("name", "")])
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()


def importieren(daten, dateiname, benutzer=None):
    """Kontoauszug importieren → Bankimport. Bereits importierte Umsätze werden als Dubletten gezählt."""
    if not daten:
        raise ParserFehler("Die Datei ist leer.")
    datei_hash = hashlib.sha256(daten).hexdigest()
    if Bankimport.objects.filter(datei_hash=datei_hash).exists():
        raise FibuFehler(f"Die Datei „{dateiname}“ wurde bereits importiert.")
    fmt = format_erkennen(daten, dateiname)
    erg = parse_camt053(daten) if fmt == "CAMT053" else parse_mt940(daten)
    iban = erg["iban"].replace(" ", "").upper()
    bk = Bankkonto.objects.filter(iban=iban, aktiv=True).first()
    if not bk:
        raise FibuFehler(f"Für die IBAN {iban or '(unbekannt)'} ist kein Bankkonto angelegt.")
    with transaction.atomic():
        imp = Bankimport.objects.create(
            nummer=naechste_nummer("FIBU_BANKIMPORT", timezone.localdate()), bankkonto=bk, format=fmt,
            dateiname=dateiname[:200], datei_hash=datei_hash, von=erg["von"], bis=erg["bis"],
            anfangssaldo=erg["anfangssaldo"], endsaldo=erg["endsaldo"], importiert_von=benutzer
            if getattr(benutzer, "pk", None) else None)
        neu = dubl = 0
        for u in erg["umsaetze"]:
            schl = dubletten_schluessel(iban, u)
            if Bankumsatz.objects.filter(dubletten_schluessel=schl).exists():
                dubl += 1
                continue
            Bankumsatz.objects.create(
                bankimport=imp, bankkonto=bk, buchungsdatum=u["buchungsdatum"], valuta=u["valuta"],
                betrag=q(u["betrag"]), waehrung=u.get("waehrung") or "EUR", name=u["name"][:200],
                iban=u["iban"].replace(" ", "")[:34], verwendungszweck=u["verwendungszweck"],
                referenz=u["referenz"][:100], gvc=u["gvc"][:40], dubletten_schluessel=schl)
            neu += 1
        imp.anzahl_neu, imp.anzahl_dubletten = neu, dubl
        imp.save(update_fields=["anzahl_neu", "anzahl_dubletten"])
    return imp


# ------------------------------------------------------------------ Vorschläge / Zuordnung

GEBUEHR_WORTE = ("entgelt", "gebühr", "gebuehr", "kontoführung", "kontofuehrung", "abschluss", "porto")


def vorschlaege(umsatz, limit=5):
    """Bewertete OP-Vorschläge: Rechnungsnummer im Verwendungszweck, Betrag (inkl. Skonto), IBAN, Name."""
    art = "D" if umsatz.betrag > 0 else "K"
    betrag = abs(umsatz.betrag)
    zweck = (umsatz.verwendungszweck or "").upper().replace(" ", "")
    kandidaten = OffenerPosten.objects.filter(art=art, offen__gt=0).select_related("partner")
    ergebnis = []
    for op in kandidaten:
        punkte, gruende = 0, []
        for nr in filter(None, [op.belegnr, op.fremdbelegnr]):
            if nr.upper().replace(" ", "") in zweck:
                punkte += 60
                gruende.append(f"Belegnummer {nr} im Verwendungszweck")
                break
        sk = op.skonto_betrag(umsatz.buchungsdatum)
        if betrag == op.offen:
            punkte += 30
            gruende.append("Betrag stimmt")
        elif sk and betrag == op.offen - sk:
            punkte += 30
            gruende.append(f"Betrag abzgl. Skonto {sk} €")
        if umsatz.iban and op.partner.iban and umsatz.iban.upper() == op.partner.iban.replace(" ", "").upper():
            punkte += 20
            gruende.append("IBAN des Partners")
        elif umsatz.name and op.partner.name and (
                op.partner.name.split()[0].lower() in umsatz.name.lower()):
            punkte += 10
            gruende.append("Name ähnlich")
        if punkte >= 30:
            ergebnis.append({"op": op, "punkte": punkte, "gruende": gruende, "skonto": sk})
    ergebnis.sort(key=lambda e: (-e["punkte"], e["op"].faellig_am))
    return ergebnis[:limit]


def ist_gebuehr(umsatz):
    t = f"{umsatz.verwendungszweck} {umsatz.name}".lower()
    return umsatz.betrag < 0 and any(w in t for w in GEBUEHR_WORTE)


def _bankkonto_zeile(umsatz):
    return umsatz.bankkonto.konto


def zuordnen(umsatz, ops, benutzer=None, skonto=True):
    """Umsatz offenen Posten zuordnen und buchen (Bank an Debitor bzw. Kreditor an Bank)."""
    umsatz = Bankumsatz.objects.select_related("bankkonto__konto").get(pk=umsatz.pk)
    if umsatz.status == Bankumsatz.Status.GEBUCHT:
        raise FibuFehler("Der Umsatz ist bereits zugeordnet.")
    ops = list(ops)
    art = "D" if umsatz.betrag > 0 else "K"
    if any(o.art != art for o in ops):
        raise FibuFehler("Zahlungseingänge nur Debitoren-, Zahlungsausgänge nur Kreditoren-OPs zuordnen.")
    with transaction.atomic():
        if umsatz.status == Bankumsatz.Status.VERRECHNUNG:
            # Umbuchung vom Verrechnungskonto „ungeklärt“ auf den OP
            b = op_ausgleichen(ops, abs(umsatz.betrag), umsatz.buchungsdatum, automatikkonto("UNGEKLAERT"),
                               benutzer, belegnr=_belegnr(umsatz), text=f"Zuordnung {_text_kurz(umsatz)}",
                               quelle="bank_zuordnung", quelle_id=umsatz.pk, skonto=skonto)
            umsatz.zuordnung_buchung = b
        else:
            b = op_ausgleichen(ops, abs(umsatz.betrag), umsatz.buchungsdatum, umsatz.bankkonto.konto, benutzer,
                               belegnr=_belegnr(umsatz), text=f"Bank {_text_kurz(umsatz)}", quelle="bank",
                               quelle_id=umsatz.pk, skonto=skonto)
            umsatz.buchung = b
        umsatz.status = Bankumsatz.Status.GEBUCHT
        umsatz.save()
    return b


def _belegnr(umsatz):
    return f"BU{umsatz.pk}"


def _text_kurz(umsatz):
    return f"{umsatz.name or ''} {umsatz.verwendungszweck or ''}".strip()[:150] or "Bankumsatz"


def auf_konto_buchen(umsatz, gegenkonto, benutzer=None, steuerschluessel=None, text=""):
    """Umsatz direkt auf ein Sachkonto buchen (z. B. Gebühren, Zinsen, Steuerzahlungen)."""
    umsatz = Bankumsatz.objects.select_related("bankkonto__konto").get(pk=umsatz.pk)
    if umsatz.status != Bankumsatz.Status.OFFEN:
        raise FibuFehler("Nur nicht zugeordnete Umsätze können auf ein Sachkonto gebucht werden.")
    betrag = abs(umsatz.betrag)
    bank_seite, gegen_seite = ("soll", "haben") if umsatz.betrag > 0 else ("haben", "soll")
    zeilen = [{"konto": umsatz.bankkonto.konto, bank_seite: betrag}]
    gk = konto(gegenkonto)
    if steuerschluessel:
        zeilen.append({"konto": gk, gegen_seite: betrag, "steuerschluessel": steuerschluessel, "brutto": True})
    else:
        zeilen.append({"konto": gk, gegen_seite: betrag})
    with transaction.atomic():
        b = buchen(umsatz.buchungsdatum, _belegnr(umsatz), text or f"Bank {_text_kurz(umsatz)}", zeilen,
                   quelle="bank", quelle_id=umsatz.pk, benutzer=benutzer)
        umsatz.buchung, umsatz.status = b, Bankumsatz.Status.GEBUCHT
        umsatz.save()
    return b


def gebuehr_buchen(umsatz, benutzer=None):
    return auf_konto_buchen(umsatz, automatikkonto("GELDVERKEHR"), benutzer, text=f"Kontoführung/Entgelt "
                            f"{umsatz.buchungsdatum:%m/%Y}")


def auf_verrechnung(umsatz, benutzer=None):
    """Nicht zuordenbarer Umsatz → Bank an/von Verrechnungskonto „ungeklärte Zahlungen“."""
    umsatz = Bankumsatz.objects.select_related("bankkonto__konto").get(pk=umsatz.pk)
    if umsatz.status != Bankumsatz.Status.OFFEN:
        raise FibuFehler("Der Umsatz ist bereits gebucht.")
    betrag = abs(umsatz.betrag)
    bank_seite, gegen_seite = ("soll", "haben") if umsatz.betrag > 0 else ("haben", "soll")
    with transaction.atomic():
        b = buchen(umsatz.buchungsdatum, _belegnr(umsatz), f"Ungeklärt: {_text_kurz(umsatz)}",
                   [{"konto": umsatz.bankkonto.konto, bank_seite: betrag},
                    {"konto": automatikkonto("UNGEKLAERT"), gegen_seite: betrag}],
                   quelle="bank", quelle_id=umsatz.pk, benutzer=benutzer)
        umsatz.buchung, umsatz.status = b, Bankumsatz.Status.VERRECHNUNG
        umsatz.save()
    return b


def automatisch_zuordnen(umsaetze, benutzer=None, mindestpunkte=90):
    """Eindeutige Treffer (Belegnummer + Betrag) automatisch zuordnen, Gebühren auf Geldverkehr buchen.

    Rückgabe: (zugeordnet, gebuehren, offen).
    """
    zugeordnet = gebuehren = offen = 0
    for u in umsaetze:
        if u.status != Bankumsatz.Status.OFFEN:
            continue
        if ist_gebuehr(u):
            gebuehr_buchen(u, benutzer)
            gebuehren += 1
            continue
        v = vorschlaege(u, limit=2)
        if v and v[0]["punkte"] >= mindestpunkte and (len(v) == 1 or v[1]["punkte"] < v[0]["punkte"]):
            zuordnen(u, [v[0]["op"]], benutzer)
            zugeordnet += 1
        else:
            offen += 1
    return zugeordnet, gebuehren, offen


def bankstand(bankkonto, bis=None):
    from .services import saldo

    return saldo(bankkonto.konto, bis=bis)


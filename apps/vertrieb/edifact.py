"""EDI/EDIFACT (UN/EDIFACT D.96A, EANCOM-Subset).

* Import:  ORDERS → Kundenauftrag (Partner über GLN, Artikel über Kundenartikelnummer/EAN/Artikelnummer)
* Export:  ORDRSP (aus Auftrag), DESADV (aus Lieferschein), INVOIC (aus Rechnung/Gutschrift)

Jede Nachricht wird als `EdiNachricht` mit Status und Fehlerprotokoll gespeichert. Die Übertragung
(AS2, X.400, SFTP, VAN) ist nicht Teil des ERP: `Uebertragung` ist der vorbereitete Adapter,
Standard ist Datei-Upload/-Download über die Weboberfläche.
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.belege.models import Beleg
from apps.core.nummernkreise import naechste_nummer
from apps.stammdaten.models import Artikel

from .models import Auftrag, EdiArtikelZuordnung, EdiNachricht, EdiPartner, Lieferung, VertriebFehler


class EdiFehler(Exception):
    pass


# ---------------------------------------------------------------- Syntax (Tokenizer / Writer)

@dataclass
class Trennzeichen:
    komponente: str = ":"
    element: str = "+"
    dezimal: str = "."
    freigabe: str = "?"
    segment: str = "'"


def segmente(text):
    """Zerlegt eine EDIFACT-Übertragung in [(Tag, [[Komponente, ...], ...])] – beachtet UNA und Freigabezeichen."""
    text = (text or "").lstrip("\ufeff").strip()
    tz = Trennzeichen()
    if text.startswith("UNA"):
        k = text[3:9]
        if len(k) < 6:
            raise EdiFehler("UNA-Segment unvollständig.")
        tz = Trennzeichen(k[0], k[1], k[2], k[3], k[5])
        text = text[9:]
    ergebnis, seg, elem, komp = [], [], [], []
    puffer, i = [], 0

    def komp_ende():
        komp.append("".join(puffer))
        puffer.clear()

    while i < len(text):
        c = text[i]
        if c == tz.freigabe and i + 1 < len(text):
            puffer.append(text[i + 1])
            i += 2
            continue
        if c == tz.komponente:
            komp_ende()
        elif c == tz.element:
            komp_ende()
            elem.append(list(komp))
            komp.clear()
        elif c == tz.segment:
            komp_ende()
            elem.append(list(komp))
            komp.clear()
            if elem and elem[0] and elem[0][0].strip():
                ergebnis.append((elem[0][0].strip(), elem[1:]))
            elem.clear()
        elif c not in "\r\n":
            puffer.append(c)
        i += 1
    if "".join(puffer).strip() or elem:
        raise EdiFehler("Übertragung endet ohne Segment-Endezeichen.")
    return ergebnis


def _w(segs, tag, *elemente):
    """Hängt ein Segment an; Elemente sind Strings oder Tupel (Komponenten). Sonderzeichen werden maskiert."""

    def esc(x):
        x = "" if x is None else str(x)
        for z in "?:+'":
            x = x.replace(z, "?" + z)
        return x.replace("\r", " ").replace("\n", " ")

    teile = [tag]
    for e in elemente:
        if isinstance(e, (tuple, list)):
            komp = [esc(k) for k in e]
            while komp and komp[-1] == "":
                komp.pop()
            teile.append(":".join(komp))
        else:
            teile.append(esc(e))
    while len(teile) > 1 and teile[-1] == "":
        teile.pop()
    segs.append("+".join(teile) + "'")


def _wert(elemente, i, k=0, standard=""):
    try:
        return elemente[i][k]
    except IndexError:
        return standard


def _datum(text, fmt="102"):
    try:
        return datetime.strptime(text[:12] if fmt == "203" else text[:8], "%Y%m%d%H%M" if fmt == "203"
                                 else "%Y%m%d").date()
    except ValueError as exc:
        raise EdiFehler(f"Ungültiges Datum „{text}“ (Format {fmt}).") from exc


def _dez(text):
    try:
        return Decimal(text.replace(",", "."))
    except (InvalidOperation, AttributeError) as exc:
        raise EdiFehler(f"Ungültige Zahl „{text}“.") from exc


# ---------------------------------------------------------------- ORDERS-Import

@dataclass
class Bestellung:
    absender: str = ""
    empfaenger: str = ""
    referenz: str = ""
    bestellnummer: str = ""
    datum: object = None
    liefertermin: object = None
    kaeufer_gln: str = ""
    lieferort_gln: str = ""
    lieferadresse: dict = field(default_factory=dict)
    positionen: list = field(default_factory=list)


def orders_parsen(text):
    """Liest eine ORDERS-Nachricht (D.96A) in eine `Bestellung` (genau eine Nachricht je Übertragung)."""
    segs = segmente(text)
    if not segs or segs[0][0] != "UNB":
        raise EdiFehler("Übertragung beginnt nicht mit UNB.")
    b = Bestellung()
    unh = 0
    pos = None
    nad = None
    for tag, e in segs:
        if tag == "UNB":
            b.absender, b.empfaenger, b.referenz = _wert(e, 1), _wert(e, 2), _wert(e, 4)
        elif tag == "UNH":
            unh += 1
            if _wert(e, 1, 0) != "ORDERS":
                raise EdiFehler(f"Nachrichtentyp {_wert(e, 1, 0)} wird nicht als Bestellung verarbeitet.")
            if (_wert(e, 1, 1), _wert(e, 1, 2)) != ("D", "96A"):
                raise EdiFehler(f"Version {_wert(e, 1, 1)}:{_wert(e, 1, 2)} nicht unterstützt (erwartet D:96A).")
        elif tag == "BGM":
            b.bestellnummer = _wert(e, 1)
            if _wert(e, 0) not in ("220", "105", "221", "226", ""):
                raise EdiFehler(f"BGM-Dokumentart {_wert(e, 0)} ist keine Bestellung.")
        elif tag == "DTM":
            q, wert, fmt = _wert(e, 0, 0), _wert(e, 0, 1), _wert(e, 0, 2, "102")
            ziel = pos if pos is not None else None
            if q == "137" and ziel is None:
                b.datum = _datum(wert, fmt)
            elif q in ("2", "64", "69"):
                if ziel is not None:
                    ziel["liefertermin"] = _datum(wert, fmt)
                else:
                    b.liefertermin = _datum(wert, fmt)
        elif tag == "NAD":
            nad = _wert(e, 0)
            gln = _wert(e, 1, 0)
            if nad == "BY":
                b.kaeufer_gln = gln
            elif nad in ("DP", "ST"):
                b.lieferort_gln = gln
                if _wert(e, 3) or _wert(e, 4):
                    b.lieferadresse = {"name": _wert(e, 3) or _wert(e, 2), "strasse": _wert(e, 4),
                                       "ort": _wert(e, 5), "plz": _wert(e, 7), "land": _wert(e, 8) or "DE"}
        elif tag == "LIN":
            pos = {"zeile": _wert(e, 0), "ean": _wert(e, 2, 0) if _wert(e, 2, 1) in ("EN", "SRV") else "",
                   "kundenartikelnummer": "", "lieferantenartikelnummer": "", "menge": None, "preis": None,
                   "liefertermin": None}
            b.positionen.append(pos)
        elif tag == "PIA" and pos is not None:
            for k in e[1:]:
                nr, art = (k + ["", ""])[:2]
                if art == "IN":
                    pos["kundenartikelnummer"] = nr
                elif art in ("SA", "MF"):
                    pos["lieferantenartikelnummer"] = nr
        elif tag == "QTY" and pos is not None and _wert(e, 0, 0) in ("21", "1"):
            pos["menge"] = _dez(_wert(e, 0, 1))
        elif tag == "PRI" and pos is not None and _wert(e, 0, 0) in ("AAA", "AAB"):
            pos["preis"] = _dez(_wert(e, 0, 1))
        elif tag == "UNS":
            pos = None
    if unh != 1:
        raise EdiFehler(f"Erwartet genau eine ORDERS-Nachricht, gefunden {unh}.")
    if not b.bestellnummer:
        raise EdiFehler("BGM: Bestellnummer fehlt.")
    if not b.positionen:
        raise EdiFehler("Die Bestellung enthält keine Positionen (LIN).")
    for p in b.positionen:
        if not p["menge"] or p["menge"] <= 0:
            raise EdiFehler(f"Position {p['zeile']}: Menge (QTY+21) fehlt.")
    return b


def _artikel_finden(edi_partner, p):
    if p["kundenartikelnummer"]:
        z = EdiArtikelZuordnung.objects.filter(edi_partner=edi_partner,
                                               kundenartikelnummer=p["kundenartikelnummer"]).first()
        if z:
            return z.artikel
    if p["ean"]:
        a = Artikel.objects.filter(ean=p["ean"]).first()
        if a:
            return a
    if p["lieferantenartikelnummer"]:
        a = Artikel.objects.filter(artikelnummer=p["lieferantenartikelnummer"]).first()
        if a:
            return a
    raise EdiFehler(f"Position {p['zeile']}: Artikel nicht gefunden (Kd.-Art.-Nr. „{p['kundenartikelnummer']}“, "
                    f"EAN „{p['ean']}“, Art.-Nr. „{p['lieferantenartikelnummer']}“).")


def orders_importieren(text, dateiname="", benutzer=None, preise_uebernehmen=False):
    """Speichert die Nachricht und legt den Auftrag an. Bei Fehlern: Nachricht mit Status FEHLER und Protokoll
    (kein Auftrag). Doppelte Bestellnummern desselben Partners werden abgewiesen. Rückgabe: EdiNachricht."""
    from .services import auftrag_anlegen

    n = EdiNachricht.objects.create(richtung=EdiNachricht.Richtung.EIN, typ=EdiNachricht.Typ.ORDERS,
                                    inhalt=text, dateiname=dateiname[:200])
    try:
        b = orders_parsen(text)
        n.referenz, n.kontrollnummer = b.bestellnummer[:100], b.referenz[:14]
        gln = b.kaeufer_gln or b.absender
        ep = EdiPartner.objects.filter(gln=gln, aktiv=True).select_related("partner").first()
        if not ep:
            raise EdiFehler(f"Kein aktiver EDI-Partner mit GLN {gln} (NAD+BY bzw. UNB-Absender).")
        n.edi_partner = ep
        if Auftrag.objects.filter(partner=ep.partner, ihre_referenz=b.bestellnummer, quelle=Auftrag.Quelle.EDI) \
                .exclude(status=Auftrag.Status.STORNIERT).exists():
            raise EdiFehler(f"Bestellung {b.bestellnummer} wurde bereits importiert.")
        positionen = []
        for p in b.positionen:
            zeile = {"artikel": _artikel_finden(ep, p), "menge": p["menge"],
                     "kundenartikelnummer": p["kundenartikelnummer"][:40]}
            if p["liefertermin"]:
                zeile["liefertermin"] = p["liefertermin"]
            if preise_uebernehmen and p["preis"] is not None:
                zeile["einzelpreis"] = p["preis"]
            positionen.append(zeile)
        felder = {"ihre_referenz": b.bestellnummer[:100], "liefertermin": b.liefertermin,
                  "bemerkung": f"EDIFACT ORDERS {dateiname or ''} (UNB {b.referenz})".strip()}
        if b.datum:
            felder["bestelldatum"] = b.datum
        if b.lieferadresse.get("plz"):
            felder.update({f"liefer_{k}": v[:200] for k, v in b.lieferadresse.items()})
        with transaction.atomic():
            a = auftrag_anlegen(ep.partner, positionen, benutzer, quelle=Auftrag.Quelle.EDI, **felder)
        n.auftrag, n.status, n.fehler = a, EdiNachricht.Status.VERARBEITET, ""
    except (EdiFehler, VertriebFehler) as exc:
        n.status, n.fehler = EdiNachricht.Status.FEHLER, str(exc)
    n.verarbeitet_am = timezone.now()
    n.save()
    return n


# ---------------------------------------------------------------- Export

def _rahmen(ep, typ, ref, inhalt_fn):
    """UNA/UNB/UNH … UNT/UNZ um die Nutzsegmente; Kontrollnummer aus dem Nummernkreis „EDI“."""
    kontroll = naechste_nummer("EDI")
    jetzt = timezone.localtime()
    segs = ["UNA:+.? '"]
    unb = [("UNOC", "3"), (settings.ERP_EIGENE_GLN, "14"), (ep.gln, "14"),
           (jetzt.strftime("%y%m%d"), jetzt.strftime("%H%M")), kontroll]
    if ep.testbetrieb:
        unb += ["", "", "", "", "", "1"]
    _w(segs, "UNB", *unb)
    start = len(segs)
    version = {"ORDRSP": "EAN005", "DESADV": "EAN007", "INVOIC": "EAN008"}[typ]
    _w(segs, "UNH", "1", (typ, "D", "96A", "UN", version))
    inhalt_fn(segs)
    _w(segs, "UNT", str(len(segs) - start + 1), "1")
    _w(segs, "UNZ", "1", kontroll)
    return kontroll, "\n".join(segs) + "\n"


def _nad(segs, q, gln, name="", strasse="", ort="", plz="", land=""):
    if name or strasse:
        _w(segs, "NAD", q, (gln, "", "9"), "", name[:35], strasse[:35], ort[:35], "", plz, land)
    else:
        _w(segs, "NAD", q, (gln, "", "9"))


def _edi_partner(partner):
    ep = EdiPartner.objects.filter(partner=partner, aktiv=True).first()
    if not ep:
        raise EdiFehler(f"{partner} ist kein aktiver EDI-Partner.")
    return ep


def _speichern(ep, typ, ref, kontroll, inhalt, auftrag=None, beleg=None):
    return EdiNachricht.objects.create(
        richtung=EdiNachricht.Richtung.AUS, typ=typ, status=EdiNachricht.Status.ERZEUGT, edi_partner=ep,
        referenz=ref[:100], kontrollnummer=kontroll, inhalt=inhalt, auftrag=auftrag, beleg=beleg,
        dateiname=f"{typ}_{ref}_{kontroll}.edi".replace("/", "-")[:200], verarbeitet_am=timezone.now())


def _artikel_segmente(segs, i, artikel, kdnr):
    _w(segs, "LIN", str(i), "", (artikel.ean, "EN") if artikel.ean else "")
    pia = [("" if not kdnr else kdnr, "IN")] if kdnr else []
    _w(segs, "PIA", "1", *(pia + [(artikel.artikelnummer, "SA")]))
    _w(segs, "IMD", "F", "", ("", "", "", artikel.bezeichnung[:35]))


def ordrsp_erzeugen(auftrag):
    """Auftragsbestätigung (ORDRSP) – BGM 231, Positionen mit bestätigter Menge und Liefertermin."""
    ep = _edi_partner(auftrag.partner)

    def inhalt(segs):
        _w(segs, "BGM", "231", auftrag.nummer, "29" if auftrag.status != "STORNIERT" else "27")
        _w(segs, "DTM", ("137", timezone.localdate().strftime("%Y%m%d"), "102"))
        if auftrag.liefertermin:
            _w(segs, "DTM", ("2", auftrag.liefertermin.strftime("%Y%m%d"), "102"))
        _w(segs, "RFF", ("ON", auftrag.ihre_referenz))
        _nad(segs, "BY", ep.gln)
        _nad(segs, "SU", settings.ERP_EIGENE_GLN)
        for i, p in enumerate(auftrag.positionen.select_related("artikel"), start=1):
            _artikel_segmente(segs, i, p.artikel, p.kundenartikelnummer)
            _w(segs, "QTY", ("113", f"{p.menge.normalize():f}"))
            if p.liefertermin:
                _w(segs, "DTM", ("2", p.liefertermin.strftime("%Y%m%d"), "102"))
            _w(segs, "PRI", ("AAA", f"{p.einzelpreis:.2f}"))
        _w(segs, "UNS", "S")

    kontroll, text = _rahmen(ep, "ORDRSP", auftrag.nummer, inhalt)
    return _speichern(ep, "ORDRSP", auftrag.nummer, kontroll, text, auftrag=auftrag, beleg=auftrag.ab_beleg)


def desadv_erzeugen(lieferung):
    """Lieferavis (DESADV) aus einem gebuchten Lieferschein – mit Chargen (PIA … NB) und Packstücken."""
    a = lieferung.auftrag
    ep = _edi_partner(a.partner)
    if not lieferung.beleg.nummer:
        raise EdiFehler("Der Lieferschein ist noch nicht gebucht.")

    def inhalt(segs):
        _w(segs, "BGM", "351", lieferung.beleg.nummer, "9")
        _w(segs, "DTM", ("137", timezone.localdate().strftime("%Y%m%d"), "102"))
        _w(segs, "DTM", ("11", lieferung.lieferdatum.strftime("%Y%m%d"), "102"))
        _w(segs, "RFF", ("ON", a.ihre_referenz))
        _nad(segs, "BY", ep.gln)
        _nad(segs, "SU", settings.ERP_EIGENE_GLN)
        _nad(segs, "DP", ep.gln, lieferung.name, lieferung.strasse, lieferung.ort, lieferung.plz, lieferung.land)
        _w(segs, "CPS", "1")
        if lieferung.packstuecke:
            _w(segs, "PAC", str(lieferung.packstuecke), "", "CT")
        _w(segs, "MEA", "AAX", "AAB", ("KGM", f"{lieferung.gewicht:.2f}"))
        for i, lp in enumerate(lieferung.positionen.select_related("auftrag_position__artikel", "charge"), 1):
            ap = lp.auftrag_position
            _artikel_segmente(segs, i, ap.artikel, ap.kundenartikelnummer)
            if lp.charge_id:
                _w(segs, "PIA", "1", (lp.charge.chargennummer, "NB"))
            _w(segs, "QTY", ("12", f"{lp.menge.normalize():f}"))
        _w(segs, "CNT", ("2", str(lieferung.positionen.count())))

    kontroll, text = _rahmen(ep, "DESADV", lieferung.beleg.nummer, inhalt)
    return _speichern(ep, "DESADV", lieferung.beleg.nummer, kontroll, text, auftrag=a, beleg=lieferung.beleg)


def invoic_erzeugen(beleg):
    """Rechnung (INVOIC, BGM 380) bzw. Gutschrift (BGM 381) aus einem freigegebenen/festgeschriebenen Beleg."""
    if beleg.belegart not in (Beleg.Belegart.RECHNUNG, Beleg.Belegart.GUTSCHRIFT) or not beleg.nummer:
        raise EdiFehler("INVOIC nur für freigegebene Rechnungen/Gutschriften.")
    from .erechnung import rechnungsdaten

    ep = _edi_partner(beleg.partner)
    d = rechnungsdaten(beleg)
    lieferungen = list(Lieferung.objects.filter(rechnung=beleg).select_related("beleg"))

    def inhalt(segs):
        _w(segs, "BGM", d["typ"], d["nummer"], "9")
        _w(segs, "DTM", ("137", d["datum"].strftime("%Y%m%d"), "102"))
        _w(segs, "DTM", ("35", d["lieferdatum"].strftime("%Y%m%d"), "102"))
        if d["bestellnummer"]:
            _w(segs, "RFF", ("ON", d["bestellnummer"]))
        for lf in lieferungen:
            _w(segs, "RFF", ("DQ", lf.beleg.nummer))
        if d["vorrechnung"]:
            _w(segs, "RFF", ("IV", d["vorrechnung"]))
        v, k = d["verkaeufer"], d["kaeufer"]
        _nad(segs, "SU", settings.ERP_EIGENE_GLN, v["name"], v["strasse"], v["ort"], v["plz"], v["land"])
        _w(segs, "RFF", ("VA", v["ust_id"]))
        _nad(segs, "BY", ep.gln, k["name"], k["strasse"], k["ort"], k["plz"], k["land"])
        if k["ust_id"]:
            _w(segs, "RFF", ("VA", k["ust_id"]))
        if ep.rechnungsempfaenger_gln:
            _nad(segs, "IV", ep.rechnungsempfaenger_gln)
        _w(segs, "CUX", ("2", d["waehrung"], "4"))
        _w(segs, "PAT", "1")
        _w(segs, "DTM", ("13", d["faellig"].strftime("%Y%m%d"), "102"))
        for i, p in enumerate(d["positionen"], start=1):
            _w(segs, "LIN", str(i), "", (p["ean"], "EN") if p["ean"] else "")
            if p["artikelnummer"]:
                _w(segs, "PIA", "1", (p["artikelnummer"], "SA"))
            _w(segs, "IMD", "F", "", ("", "", "", p["name"][:35]))
            _w(segs, "QTY", ("47", f"{p['menge'].normalize():f}", p["einheit"]))
            _w(segs, "MOA", ("203", f"{p['betrag']:.2f}"))
            _w(segs, "PRI", ("AAA", f"{p['nettopreis']:.4f}"))
            _w(segs, "TAX", "7", "VAT", "", "", ("", "", "", f"{p['satz']:.2f}"), p["kategorie"])
        _w(segs, "UNS", "S")
        _w(segs, "CNT", ("2", str(len(d["positionen"]))))
        _w(segs, "MOA", ("77", f"{d['brutto']:.2f}"))
        _w(segs, "MOA", ("79", f"{sum((p['betrag'] for p in d['positionen']), Decimal(0)):.2f}"))
        _w(segs, "MOA", ("125", f"{d['netto']:.2f}"))
        _w(segs, "MOA", ("176", f"{d['steuer']:.2f}"))
        for s in d["steuern"]:
            _w(segs, "TAX", "7", "VAT", "", "", ("", "", "", f"{s['satz']:.2f}"), s["kategorie"])
            _w(segs, "MOA", ("125", f"{s['netto']:.2f}"))
            _w(segs, "MOA", ("124", f"{s['steuer']:.2f}"))

    kontroll, text = _rahmen(ep, "INVOIC", beleg.nummer, inhalt)
    auftrag = lieferungen[0].auftrag if lieferungen else None
    return _speichern(ep, "INVOIC", beleg.nummer, kontroll, text, auftrag=auftrag, beleg=beleg)


# ---------------------------------------------------------------- Übertragungs-Adapter (vorbereitet)

class Uebertragung:
    """Schnittstelle für eine Übertragungsstrecke (AS2, X.400, SFTP, VAN-Provider).
    Standard-Implementierung: keine Übertragung – Dateien werden über die Oberfläche heruntergeladen
    und mit „als übertragen markieren“ quittiert."""

    name = "Datei-Download (manuell)"

    def senden(self, nachricht):
        raise NotImplementedError("Keine automatische Übertragungsstrecke konfiguriert.")

    def abholen(self):
        return []


def als_uebertragen_markieren(nachricht):
    if nachricht.richtung != EdiNachricht.Richtung.AUS:
        raise EdiFehler("Nur ausgehende Nachrichten können als übertragen markiert werden.")
    nachricht.status = EdiNachricht.Status.UEBERTRAGEN
    nachricht.save(update_fields=["status"])
    return nachricht

"""Prüf- und Korrekturmaske für erkannte Eingangsrechnungen.

Korrekturen überschreiben die erkannten Werte im Dokument (Konfidenz 1,0 = manuell bestätigt), werden mit den
geänderten Feldern protokolliert und anschließend erneut plausibilisiert. Eine bereits verbuchte oder
archivierte Rechnung ist nicht mehr korrigierbar.
"""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from . import erkennung as E
from . import services as S
from .models import Dokument, Dokumentart

KOPF_FELDER = ("rechnungsnummer", "rechnungsdatum", "faellig_am", "zahlungsbedingung", "iban", "ust_id",
               "netto", "steuer", "brutto", "skonto_prozent", "skonto_bis", "skonto_betrag")
DATUMSFELDER = ("rechnungsdatum", "faellig_am", "skonto_bis")
BETRAGSFELDER = ("netto", "steuer", "brutto", "skonto_prozent", "skonto_betrag")


def bearbeitbar(dokument):
    return (dokument.art == Dokumentart.EINGANGSRECHNUNG and not dokument.archiviert
            and not dokument.eingangsrechnung_id
            and dokument.status in (Dokument.Status.ERKANNT, Dokument.Status.PRUEFEN))


def fehler(erg):
    return [m["text"] for m in (erg or {}).get("plausibilitaet", []) if m["stufe"] == "fehler"]


def formular_daten(dokument):
    """Erkennung → (Kopf-Initialwerte, Positions-Initialwerte) für die Formulare."""
    erg = dokument.erkennung or {}
    f = erg.get("felder", {})
    abgleich = erg.get("abgleich") or {}
    kopf = {"lieferant": (erg.get("lieferant") or {}).get("partner_id") or dokument.partner_id,
            "bestellung": ("ohne" if abgleich.get("auswahl") == "ohne" else
                           str(abgleich["bestellung_id"]) if abgleich.get("auswahl") == "manuell"
                           and abgleich.get("bestellung_id") else "")}
    for name in KOPF_FELDER:
        typ = "datum" if name in DATUMSFELDER else ("betrag" if name in BETRAGSFELDER else None)
        kopf[name] = E.feldwert(f, name, typ)
    positionen = []
    for p in erg.get("positionen", []):
        positionen.append({
            "beschreibung": p.get("beschreibung", ""), "menge": E.zahl(p.get("menge")),
            "einzelpreis": E.zahl(p.get("einzelpreis")), "betrag": E.zahl(p.get("betrag")),
            "steuersatz": E.zahl(p.get("steuersatz")), "steuerschluessel": p.get("steuerschluessel", ""),
            "konto": p.get("konto") or None})
    return kopf, positionen


def _wert(v):
    if v is None or v == "":
        return None
    if isinstance(v, Decimal):
        return str(v.quantize(E.CENT))
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v).strip()


@transaction.atomic
def korrektur_speichern(dokument, kopf, positionen, benutzer=None):
    """Übernimmt korrigierte Kopfdaten/Positionen (bereinigte Formulardaten) und plausibilisiert neu."""
    dokument = Dokument.objects.select_for_update().get(pk=dokument.pk)
    if not bearbeitbar(dokument):
        raise S.DmsFehler(f"{dokument.nummer} kann nicht mehr korrigiert werden ({dokument.get_status_display()}).")
    erg = dict(dokument.erkennung or {})
    alt = erg.get("felder", {})
    felder, geaendert = {}, []
    for name in KOPF_FELDER:
        neu = _wert(kopf.get(name))
        if name in ("iban", "ust_id") and neu:
            neu = neu.replace(" ", "").upper()
        if neu is None:
            if name in alt:
                geaendert.append(name)
            continue
        vorher = (alt.get(name) or {}).get("wert")
        if str(vorher) != neu:
            geaendert.append(name)
            felder[name] = {"wert": neu, "konfidenz": 1.0, "manuell": True}
        else:
            felder[name] = dict(alt[name], konfidenz=max(alt[name].get("konfidenz", 0), 0.99))
    # Zahlungsziel/Lieferantenname aus der Erkennung beibehalten (nicht Teil der Maske)
    for name in ("zahlungsziel_tage", "lieferant_name", "bestellnummer", "skonto_tage"):
        if name in alt:
            felder[name] = alt[name]
    # Skonto: Betrag neu rechnen, wenn Satz/Brutto geändert wurden (und der Betrag nicht selbst korrigiert);
    # eine manuell geänderte Frist ersetzt die erkannte Tagesfrist
    if {"skonto_prozent", "brutto"} & set(geaendert) and "skonto_betrag" not in geaendert:
        felder.pop("skonto_betrag", None)
    if "skonto_bis" in geaendert:
        felder.pop("skonto_tage", None)
    E.skonto_ergaenzen(felder)
    lieferant = kopf.get("lieferant")
    alt_partner = (erg.get("lieferant") or {}).get("partner_id")
    if lieferant:
        if lieferant.pk != alt_partner:
            geaendert.append("lieferant")
            erg["lieferant"] = {"partner_id": lieferant.pk, "name": lieferant.name, "methode": "manuell",
                                "konfidenz": 1.0}
        else:
            erg["lieferant"] = dict(erg["lieferant"], konfidenz=1.0)
    else:
        if alt_partner:
            geaendert.append("lieferant")
        erg["lieferant"] = {}
    neue_pos, alte_pos = [], erg.get("positionen", [])
    for i, p in enumerate(positionen):
        konto = p.get("konto")
        nummer = getattr(konto, "nummer", konto) or ""
        vorher = alte_pos[i] if i < len(alte_pos) else {}
        # Quelle des Kontos: unverändert übernommener Vorschlag behält seine Herkunft
        quelle = vorher.get("kontoquelle", "") if nummer and nummer == vorher.get("konto") else (
            "manuell" if nummer else "")
        neue_pos.append({
            "menge": str(Decimal(p["menge"]).quantize(Decimal("0.001"))),
            "einheit": p.get("einheit", ""), "beschreibung": (p.get("beschreibung") or "")[:200],
            "einzelpreis": str(Decimal(p["einzelpreis"]).quantize(Decimal("0.0001"))),
            "betrag": str(Decimal(p["betrag"]).quantize(E.CENT)),
            "steuersatz": str(Decimal(p["steuersatz"]).normalize()) if p.get("steuersatz") is not None else None,
            "steuerschluessel": p.get("steuerschluessel") or "",
            "konto": nummer, "kontoquelle": quelle,
            "konfidenz": 1.0})
    if [(_p.get("betrag"), _p.get("konto"), _p.get("steuerschluessel"), _p.get("beschreibung"))
            for _p in erg.get("positionen", [])] != [(_p["betrag"], _p["konto"], _p["steuerschluessel"],
                                                      _p["beschreibung"]) for _p in neue_pos]:
        geaendert.append("positionen")
    for p in neue_pos:
        # Fehlenden Steuerschlüssel aus dem Satz ableiten (Vorsteuer)
        if not p["steuerschluessel"] and p["steuersatz"] is not None:
            from apps.fibu import services as F

            try:
                p["steuerschluessel"] = F.schluessel_fuer_satz(p["steuersatz"], einkauf=True)
            except F.FibuFehler:
                pass
    erg.update(felder=felder, positionen=neue_pos, korrigiert=True,
               korrigiert_am=timezone.now().isoformat(),
               korrigiert_von=getattr(benutzer, "username", "") or "")
    if kopf.get("bestellung") is not None:
        from . import abgleich as A

        alt_auswahl = (erg.get("abgleich") or {}).get("auswahl"), (erg.get("abgleich") or {}).get("bestellung_id")
        neu_auswahl = A.auswahl_setzen(erg, kopf["bestellung"])
        if (neu_auswahl.get("auswahl"), neu_auswahl.get("bestellung_id")) != alt_auswahl:
            geaendert.append("bestellbezug")
    E.bewerten(erg, dokument)
    status = Dokument.Status.PRUEFEN if fehler(erg) else Dokument.Status.ERKANNT
    aenderung = {"erkennung": erg, "konfidenz": Decimal(erg["gesamtkonfidenz"]), "status": status}
    if lieferant:
        aenderung["partner"] = lieferant
    rd = kopf.get("rechnungsdatum")
    if rd:
        aenderung["belegdatum"] = rd
    for k, v in aenderung.items():
        setattr(dokument, k, v)
    dokument.save(update_fields=list(aenderung))
    S.protokollieren(dokument, "Korrektur",
                     ("Geändert: " + ", ".join(geaendert) if geaendert else "Erkannte Werte bestätigt")
                     + f" → {dokument.get_status_display()}"
                     + (f" ({len(fehler(erg))} Plausibilitätsfehler)" if fehler(erg) else ""), benutzer)
    return erg


def kontierung_neu(dokument, benutzer=None):
    """Kontierungsvorschlag (Lernregeln) für alle Positionen neu ermitteln."""
    if not bearbeitbar(dokument):
        raise S.DmsFehler(f"{dokument.nummer} kann nicht mehr korrigiert werden.")
    erg = dict(dokument.erkennung or {})
    erg["korrigiert"] = False
    E.bewerten(erg, dokument)
    erg["korrigiert"] = True
    Dokument.objects.filter(pk=dokument.pk).update(erkennung=erg, konfidenz=Decimal(erg["gesamtkonfidenz"]))
    dokument.refresh_from_db()
    S.protokollieren(dokument, "Kontierung", "Kontierungsvorschlag neu ermittelt.", benutzer)
    return erg

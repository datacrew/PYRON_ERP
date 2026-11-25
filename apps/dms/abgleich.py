"""Dreifach-Abgleich Bestellung ↔ Wareneingang ↔ Eingangsrechnung (Rechnungsprüfung im DMS).

Ablauf:
1. Bestellung bestimmen – manuell gewählt, sonst Bestellnummer aus der E-Rechnung (BT-13) bzw. dem Text
   („Ihre Bestellung EK-2026-00012“) oder eine Nummer einer offenen Bestellung des Lieferanten, die im Volltext
   vorkommt. Offene Bestellungen des Lieferanten werden immer als Vorschläge angeboten.
2. Rechnungspositionen den Bestellpositionen zuordnen (Artikel-/Lieferantenartikelnummer im Text, Bezeichnung,
   sonst Reihenfolge). Nicht zuordenbare Positionen (Fracht, Verpackung …) bleiben ohne Bestellbezug und werden
   wie bisher über das Aufwandskonto gebucht.
3. Je Position Menge gegen gelieferte (Wareneingang) und bereits berechnete Menge sowie Preis gegen Lieferpreis
   des Wareneingangs bzw. Nettopreis der Bestellung prüfen – mit konfigurierbaren Toleranzen
   (`ERP_DMS_TOLERANZ_MENGE_PROZENT`, `ERP_DMS_TOLERANZ_PREIS_PROZENT`, `ERP_DMS_TOLERANZ_BETRAG`).

Das Ergebnis steht in `Dokument.erkennung["abgleich"]`. Abweichungen außerhalb der Toleranz verhindern die
Verbuchung, bis jemand mit Freigaberecht (`ERP_DMS_ABWEICHUNG_RECHTE`, Standard Einkauf oder Fibu „freigeben“)
sie mit Begründung freigibt. Die Fibu verknüpft anschließend Eingangsrechnung ↔ Bestellung bzw.
Rechnungsposition ↔ Bestellposition und bucht bestellbezogene Positionen gegen das WE/RE-Verrechnungskonto.
"""
import difflib
import re
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from apps.core.permissions import hat_recht

D = Decimal
NULL = D("0")

AUSWAHL_AUTO, AUSWAHL_MANUELL, AUSWAHL_OHNE = "auto", "manuell", "ohne"


def toleranzen():
    """Toleranzen aus den Settings (Prozent bzw. Euro je Position)."""
    preis_standard = getattr(settings, "ERP_RECHNUNGSPRUEFUNG_TOLERANZ_PROZENT", 1)
    return {"menge_prozent": D(str(getattr(settings, "ERP_DMS_TOLERANZ_MENGE_PROZENT", 0))),
            "preis_prozent": D(str(getattr(settings, "ERP_DMS_TOLERANZ_PREIS_PROZENT", preis_standard))),
            "betrag": D(str(getattr(settings, "ERP_DMS_TOLERANZ_BETRAG", "0.50")))}


def darf_abweichung_freigeben(benutzer):
    """Freigabe einer Abweichung: Superuser oder eines der Rechte aus ERP_DMS_ABWEICHUNG_RECHTE."""
    if benutzer is None or not getattr(benutzer, "is_authenticated", False):
        return False
    rechte = getattr(settings, "ERP_DMS_ABWEICHUNG_RECHTE", (("einkauf", "freigeben"), ("fibu", "freigeben")))
    return benutzer.is_superuser or any(hat_recht(benutzer, modul, aktion) for modul, aktion in rechte)


def _zahl(w):
    try:
        return D(str(w)) if w not in (None, "") else None
    except Exception:  # noqa: BLE001 – unlesbare Werte gelten als fehlend
        return None


def kandidaten(partner):
    """Bestellungen des Lieferanten, die noch berechnet werden können (bestellt, teil-/vollständig geliefert)."""
    from apps.einkauf.models import Bestellung

    if not partner:
        return Bestellung.objects.none()
    return (Bestellung.objects.filter(lieferant=partner, typ=Bestellung.Typ.BESTELLUNG,
                                      status__in=[Bestellung.Status.BESTELLT, Bestellung.Status.TEILGELIEFERT,
                                                  Bestellung.Status.GELIEFERT])
            .exclude(nummer="").order_by("-datum", "-pk"))


def _normal(nr):
    return re.sub(r"[\s\-/_.]", "", (nr or "").upper())


def bestellung_erkennen(partner, bestellnummer="", text=""):
    """(Bestellung, Quelle) aus erkannter Bestellnummer bzw. Volltext – oder (None, "")."""
    from apps.einkauf.models import Bestellung

    if bestellnummer:
        qs = Bestellung.objects.filter(typ=Bestellung.Typ.BESTELLUNG).exclude(nummer="")
        treffer = qs.filter(nummer__iexact=bestellnummer.strip()).first()
        if not treffer:
            gesucht = _normal(bestellnummer)
            treffer = next((b for b in kandidaten(partner)[:200] if _normal(b.nummer) == gesucht), None)
        if treffer and (not partner or treffer.lieferant_id == partner.pk):
            return treffer, "Bestellnummer auf der Rechnung"
    text_norm = _normal(text)
    for b in kandidaten(partner)[:200]:
        if b.nummer and (b.nummer.upper() in (text or "").upper() or (len(_normal(b.nummer)) >= 6
                                                                       and _normal(b.nummer) in text_norm)):
            return b, "Bestellnummer im Rechnungstext"
    return None, ""


def _bezeichnung(bp):
    return (bp.bezeichnung or bp.artikel.bezeichnung or "").strip()


def zuordnen(positionen, bestellpositionen):
    """Rechnungspositionen → {Index: (Bestellposition, Methode)}."""
    frei = list(bestellpositionen)
    zuordnung = {}
    # 1. Artikel- bzw. Lieferantenartikelnummer im Positionstext
    for i, p in enumerate(positionen):
        text = (p.get("beschreibung") or "").upper()
        for bp in frei:
            nummern = [n for n in (bp.lieferanten_artikelnummer, bp.artikel.artikelnummer) if n and len(n) >= 3]
            if any(re.search(rf"(?<![A-Z0-9]){re.escape(n.upper())}(?![A-Z0-9])", text) for n in nummern):
                zuordnung[i] = (bp, "Artikelnummer")
                frei.remove(bp)
                break
    # 2. Ähnliche Bezeichnung
    for i, p in enumerate(positionen):
        if i in zuordnung or not frei:
            continue
        text = (p.get("beschreibung") or "").lower()
        bewertet = sorted(((difflib.SequenceMatcher(None, text, _bezeichnung(bp).lower()).ratio(), bp) for bp in frei),
                          key=lambda x: -x[0])
        if bewertet and bewertet[0][0] >= 0.6:
            zuordnung[i] = (bewertet[0][1], "Bezeichnung")
            frei.remove(bewertet[0][1])
    # 3. Reihenfolge – nur wenn gar nichts über Nummer/Bezeichnung zugeordnet wurde und die Anzahl genau aufgeht
    #    (sonst würden Fracht-/Nebenkostenzeilen fälschlich einer Bestellposition zugeschlagen)
    offen = [i for i in range(len(positionen)) if i not in zuordnung]
    if offen and not zuordnung and len(offen) == len(frei):
        for i, bp in zip(offen, frei):
            zuordnung[i] = (bp, "Reihenfolge")
    return zuordnung


def _bereits_berechnet(bp, ausser_er=None):
    from apps.fibu.models import ERPosition

    qs = ERPosition.objects.filter(bestellposition=bp, rechnung__status="GEBUCHT")
    if ausser_er:
        qs = qs.exclude(rechnung_id=ausser_er)
    return qs.aggregate(m=Sum("menge"))["m"] or NULL


def _prozent(ist, soll):
    return (ist - soll) / soll * 100 if soll else (D(100) if ist else NULL)


def vergleichen(positionen, bestellung, tol=None):
    """Vergleichstabelle Rechnung ↔ Bestellung ↔ Wareneingang (ohne Datenbankänderung)."""
    from apps.fibu.services import _we_preis

    tol = tol or toleranzen()
    bps = list(bestellung.positionen.select_related("artikel"))
    zuordnung = zuordnen(positionen, bps)
    zeilen, ohne_bezug = [], []
    for i, p in enumerate(positionen):
        if i not in zuordnung:
            ohne_bezug.append({"index": i, "beschreibung": p.get("beschreibung") or f"Position {i + 1}",
                               "betrag": str(_zahl(p.get("betrag")) or NULL)})
            continue
        bp, methode = zuordnung[i]
        menge = _zahl(p.get("menge")) or D(1)
        preis = _zahl(p.get("einzelpreis")) or NULL
        geliefert = D(bp.geliefert_menge or 0)
        berechnet = D(_bereits_berechnet(bp))
        berechenbar = geliefert - berechnet
        preis_we = D(_we_preis(bp))
        abw = []
        if geliefert <= 0:
            abw.append({"art": "KEIN_WE", "text": "Noch kein Wareneingang zu dieser Bestellposition gebucht."})
        elif menge > berechenbar and _prozent(menge, berechenbar or D("0.001")) > tol["menge_prozent"]:
            abw.append({"art": "MENGE", "text": f"Menge {menge:g} übersteigt die noch berechenbare Liefermenge "
                                                f"{berechenbar:g} (geliefert {geliefert:g}, bereits berechnet "
                                                f"{berechnet:g})."})
        diff_proz = _prozent(preis, preis_we)
        diff_betrag = ((preis - preis_we) * menge).quantize(D("0.01"))
        if preis_we and abs(diff_proz) > tol["preis_prozent"] and abs(diff_betrag) > tol["betrag"]:
            quelle = "Lieferpreis laut Wareneingang" if preis_we != bp.nettopreis else "Bestellpreis"
            abw.append({"art": "PREIS", "text": f"Preis {preis:.4f} € weicht vom {quelle} {preis_we:.4f} € ab "
                                                f"({diff_proz:+.1f} %, {diff_betrag:+.2f} € auf die Position)."})
        hinweis = ""
        if not abw and menge < berechenbar:
            hinweis = f"Teilberechnung – {berechenbar - menge:g} geliefert, aber noch nicht berechnet."
        zeilen.append({
            "index": i, "beschreibung": p.get("beschreibung") or "", "zuordnung": methode,
            "bestellposition_id": bp.pk, "bp_position": bp.position, "artikel": bp.artikel.artikelnummer,
            "bp_bezeichnung": _bezeichnung(bp),
            "menge_rechnung": str(menge), "menge_bestellt": str(bp.menge), "menge_geliefert": str(geliefert),
            "menge_berechnet": str(berechnet), "preis_rechnung": str(preis), "preis_bestellung": str(bp.nettopreis),
            "preis_we": str(preis_we), "preis_diff_prozent": str(diff_proz.quantize(D("0.1"))),
            "abweichungen": abw, "hinweis": hinweis})
    zugeordnet = {z["bestellposition_id"] for z in zeilen}
    nicht_berechnet = [{"position": bp.position, "artikel": bp.artikel.artikelnummer, "bezeichnung": _bezeichnung(bp),
                        "offen": str(D(bp.geliefert_menge or 0) - D(_bereits_berechnet(bp)))}
                       for bp in bps if bp.pk not in zugeordnet
                       and D(bp.geliefert_menge or 0) - D(_bereits_berechnet(bp)) > 0]
    return zeilen, ohne_bezug, nicht_berechnet


def berechnen(erg, dokument=None, partner=None):
    """`erg["abgleich"]` (neu) berechnen; manuelle Auswahl/Freigabe bleibt erhalten. Gibt den Abgleich zurück."""
    from apps.einkauf.models import Bestellung
    from apps.stammdaten.models import Geschaeftspartner

    from .erkennung import feldwert

    alt = dict(erg.get("abgleich") or {})
    auswahl = alt.get("auswahl") or AUSWAHL_AUTO
    if partner is None:
        partner_id = (erg.get("lieferant") or {}).get("partner_id")
        partner = Geschaeftspartner.objects.filter(pk=partner_id).first() if partner_id else None
    tol = toleranzen()
    neu = {"auswahl": auswahl, "berechnet_am": timezone.now().isoformat(),
           "toleranzen": {k: str(v) for k, v in tol.items()},
           "vorschlaege": [{"id": b.pk, "nummer": b.nummer, "datum": b.datum.isoformat(), "status": b.get_status_display(),
                            "summe_netto": str(b.summe_netto)} for b in kandidaten(partner)[:30]],
           "bestellung_id": None, "bestellnummer": "", "quelle": "", "positionen": [], "ohne_bezug": [],
           "nicht_berechnet": [], "abweichung": False, "hinweise": []}
    bestellung = None
    if auswahl == AUSWAHL_MANUELL and alt.get("bestellung_id"):
        bestellung = Bestellung.objects.filter(pk=alt["bestellung_id"]).first()
        neu["quelle"] = "manuell gewählt"
        if bestellung and partner and bestellung.lieferant_id != partner.pk:
            neu["hinweise"].append(f"Bestellung {bestellung.nummer} gehört zu {bestellung.lieferant.name}, nicht zum "
                                   "Rechnungssteller.")
            neu["abweichung"] = True
    elif auswahl == AUSWAHL_AUTO:
        text = getattr(dokument, "ocr_text", "") if dokument is not None else ""
        bestellung, neu["quelle"] = bestellung_erkennen(partner, feldwert(erg.get("felder") or {}, "bestellnummer")
                                                        or "", text)
    if bestellung is None:
        nr = feldwert(erg.get("felder") or {}, "bestellnummer")
        if auswahl == AUSWAHL_AUTO and nr:
            neu["hinweise"].append(f"Bestellnummer „{nr}“ erkannt, aber keine passende Bestellung des Lieferanten "
                                   "gefunden – bitte manuell wählen.")
        if alt.get("freigabe"):
            neu["freigabe"] = alt["freigabe"]
        erg["abgleich"] = neu
        return neu
    zeilen, ohne_bezug, nicht_berechnet = vergleichen(erg.get("positionen") or [], bestellung, tol)
    neu.update(bestellung_id=bestellung.pk, bestellnummer=bestellung.nummer, positionen=zeilen,
               ohne_bezug=ohne_bezug, nicht_berechnet=nicht_berechnet)
    if not zeilen:
        neu["hinweise"].append("Keine Rechnungsposition konnte einer Bestellposition zugeordnet werden.")
        neu["abweichung"] = True
    neu["abweichung"] = neu["abweichung"] or any(z["abweichungen"] for z in zeilen)
    # Eine erteilte Freigabe gilt nur für genau diesen Abweichungsstand
    if alt.get("freigabe") and alt["freigabe"].get("signatur") == signatur(neu):
        neu["freigabe"] = alt["freigabe"]
    erg["abgleich"] = neu
    return neu


def signatur(abgleich):
    """Kurzer Fingerabdruck der Abweichungen – ändert sich die Lage, wird eine Freigabe ungültig."""
    teile = [str(abgleich.get("bestellung_id"))]
    teile += [f'{z["bestellposition_id"]}:{a["art"]}:{z["menge_rechnung"]}:{z["preis_rechnung"]}'
              for z in abgleich.get("positionen", []) for a in z["abweichungen"]]
    teile += abgleich.get("hinweise", [])
    return "|".join(teile)


def meldungen(abgleich):
    """Plausibilitätsmeldungen (Stufe warnung/info) aus dem Abgleich."""
    if not abgleich.get("bestellung_id"):
        if abgleich.get("vorschlaege") and abgleich.get("auswahl") != AUSWAHL_OHNE:
            return [{"stufe": "info", "text": f"{len(abgleich['vorschlaege'])} offene Bestellung(en) des Lieferanten "
                                              "– Bestellbezug in der Prüfmaske wählen (Dreifach-Abgleich)."}]
        return [{"stufe": "warnung", "text": h} for h in abgleich.get("hinweise", [])]
    m = [{"stufe": "info", "text": f"Bestellung {abgleich['bestellnummer']} ({abgleich['quelle']}): "
                                   f"{len(abgleich['positionen'])} Position(en) zugeordnet."}]
    for z in abgleich["positionen"]:
        for a in z["abweichungen"]:
            m.append({"stufe": "warnung", "text": f"Dreifach-Abgleich Pos. {z['index'] + 1}: {a['text']}"})
    m += [{"stufe": "warnung", "text": h} for h in abgleich.get("hinweise", [])]
    if abgleich["abweichung"]:
        if abgleich.get("freigabe"):
            m.append({"stufe": "info", "text": f"Abweichung freigegeben von {abgleich['freigabe']['von']}: "
                                               f"{abgleich['freigabe']['begruendung']}"})
        else:
            m.append({"stufe": "warnung", "text": "Abweichung außerhalb der Toleranz – Verbuchung nur mit Begründung "
                                                  "und Freigaberecht."})
    return m


def auswahl_setzen(erg, wert):
    """Bestellbezug aus der Prüfmaske/API übernehmen: "" = automatisch, "ohne" = ohne Bestellbezug, sonst ID."""
    abgleich = dict(erg.get("abgleich") or {})
    wert = str(wert or "").strip()
    if wert == AUSWAHL_OHNE:
        abgleich.update(auswahl=AUSWAHL_OHNE, bestellung_id=None)
    elif wert:
        abgleich.update(auswahl=AUSWAHL_MANUELL, bestellung_id=int(wert))
    else:
        abgleich.update(auswahl=AUSWAHL_AUTO)
    erg["abgleich"] = abgleich
    return abgleich


def freigabe_erteilen(abgleich, benutzer, begruendung):
    """Freigabe einer Abweichung mit Begründung im Abgleich vermerken (Rechteprüfung beim Aufrufer)."""
    abgleich["freigabe"] = {"begruendung": begruendung.strip()[:500], "am": timezone.now().isoformat(),
                            "von": getattr(benutzer, "username", "") or "System", "signatur": signatur(abgleich)}
    return abgleich

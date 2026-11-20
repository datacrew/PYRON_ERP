"""Auswertungen: SuSa, Kontoblatt, Journal, OP-Liste, Bilanz/GuV (HGB), UStVA, DATEV-Export.

Alle tabellarischen Auswertungen liefern eine einheitliche Struktur, die Web-Ansicht, PDF und CSV teilen:
    {"titel", "untertitel", "spalten": [(Name, Ausrichtung)], "zeilen": [{"werte": [...], "stil": ""}],
     "hinweise": [...]}
Stil: "" normal, "kopf" Gruppenüberschrift, "summe" Zwischensumme, "gesamt" Endsumme.
"""
import csv
import io
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from apps.stammdaten.models import Konto

from . import gliederung as G
from .models import NULL, Buchung, Buchungszeile, Geschaeftsjahr, OffenerPosten
from .services import ist_bilanzkonto, q, rahmen

R, L = "r", "l"


def gj_beginn(stichtag):
    gj = Geschaeftsjahr.objects.filter(beginn__lte=stichtag, ende__gte=stichtag).first()
    return gj.beginn if gj else date(stichtag.year, 1, 1)


def _salden(von, bis, konten=None):
    qs = Buchungszeile.objects.filter(buchung__belegdatum__gte=von, buchung__belegdatum__lte=bis)
    if konten is not None:
        qs = qs.filter(konto__in=konten)
    return {e["konto"]: ((e["s"] or NULL), (e["h"] or NULL))
            for e in qs.values("konto").annotate(s=Sum("soll"), h=Sum("haben"))}


def _z(werte, stil=""):
    return {"werte": werte, "stil": stil}


# ------------------------------------------------------------------ Summen- und Saldenliste

def susa(von, bis):
    beginn = gj_beginn(von)
    vortrag = _salden(beginn, von.fromordinal(von.toordinal() - 1)) if von > beginn else {}
    zeitraum = _salden(von, bis)
    ids = set(vortrag) | set(zeitraum)
    konten = Konto.objects.filter(pk__in=ids).order_by("kontenrahmen", "nummer")
    zeilen, summe = [], [NULL] * 4
    for k in konten:
        vs, vh = vortrag.get(k.pk, (NULL, NULL))
        s, h = zeitraum.get(k.pk, (NULL, NULL))
        v = vs - vh
        werte = [v, s, h, v + s - h]
        summe = [a + b for a, b in zip(summe, werte)]
        zeilen.append(_z([k.nummer, k.bezeichnung] + werte))
    zeilen.append(_z(["", "Summe"] + summe, "gesamt"))
    return {"titel": "Summen- und Saldenliste", "untertitel": f"{von:%d.%m.%Y} – {bis:%d.%m.%Y}",
            "spalten": [("Konto", L), ("Bezeichnung", L), ("Saldo Vortrag", R), ("Soll", R), ("Haben", R),
                        ("Saldo", R)],
            "zeilen": zeilen, "hinweise": ["Saldo positiv = Sollsaldo, negativ = Habensaldo."]}


# ------------------------------------------------------------------ Kontoblatt

def kontoblatt(konto, von, bis):
    beginn = gj_beginn(von)
    anfang = NULL
    if von > beginn:
        s, h = _salden(beginn, von.fromordinal(von.toordinal() - 1), [konto]).get(konto.pk, (NULL, NULL))
        anfang = s - h
    zeilen = [_z(["", "", "", "Anfangssaldo", "", "", "", anfang], "summe")]
    laufend, ss, sh = anfang, NULL, NULL
    qs = (Buchungszeile.objects.filter(konto=konto, buchung__belegdatum__gte=von, buchung__belegdatum__lte=bis)
          .select_related("buchung", "steuerschluessel").order_by("buchung__belegdatum", "buchung__pk", "position"))
    for z in qs:
        gegen = Buchungszeile.objects.filter(buchung_id=z.buchung_id, ist_steuer=False).exclude(pk=z.pk)
        gegen = (gegen.filter(haben__gt=0) if z.soll else gegen.filter(soll__gt=0)).values_list("konto__nummer",
                                                                                              flat=True)
        gegen = sorted(set(gegen))
        laufend += z.soll - z.haben
        ss += z.soll
        sh += z.haben
        zeilen.append(_z([z.buchung.belegdatum.strftime("%d.%m.%Y"), z.buchung.belegnr,
                          ", ".join(gegen[:3]) + ("…" if len(gegen) > 3 else ""),
                          z.text or z.buchung.text, z.steuerschluessel.code if z.steuerschluessel_id else "",
                          z.soll or "", z.haben or "", laufend]))
    zeilen.append(_z(["", "", "", "Summe / Endsaldo", "", ss, sh, laufend], "gesamt"))
    return {"titel": f"Kontoblatt {konto.nummer} {konto.bezeichnung}", "untertitel": f"{von:%d.%m.%Y} – {bis:%d.%m.%Y}",
            "spalten": [("Datum", L), ("Beleg", L), ("Gegenkonto", L), ("Text", L), ("St.", L), ("Soll", R),
                        ("Haben", R), ("Saldo", R)], "zeilen": zeilen, "hinweise": []}


# ------------------------------------------------------------------ Journal

def journal(von, bis):
    zeilen, ss, sh = [], NULL, NULL
    qs = (Buchung.objects.filter(belegdatum__gte=von, belegdatum__lte=bis).order_by("belegdatum", "pk")
          .prefetch_related("zeilen__konto", "zeilen__steuerschluessel"))
    for b in qs:
        status = "F" if b.festgeschrieben else ""
        zeilen.append(_z([b.belegdatum.strftime("%d.%m.%Y"), b.nummer, b.belegnr, b.text, status, "", "", ""],
                         "kopf"))
        for z in b.zeilen.all():
            ss += z.soll
            sh += z.haben
            zeilen.append(_z(["", "", "", f"{z.konto.nummer} {z.konto.bezeichnung}"[:70],
                              z.steuerschluessel.code if z.steuerschluessel_id else "", "", z.soll or "",
                              z.haben or ""]))
    zeilen.append(_z(["", "", "", "Summe", "", "", ss, sh], "gesamt"))
    return {"titel": "Buchungsjournal", "untertitel": f"{von:%d.%m.%Y} – {bis:%d.%m.%Y}",
            "spalten": [("Datum", L), ("Buchung", L), ("Beleg", L), ("Text / Konto", L), ("St./F", L), ("", L),
                        ("Soll", R), ("Haben", R)], "zeilen": zeilen,
            "hinweise": ["F = festgeschrieben (GoBD)."]}


# ------------------------------------------------------------------ OP-Liste mit Fälligkeitsstruktur

RASTER = [("nicht fällig", None, 0), ("1–30", 1, 30), ("31–60", 31, 60), ("61–90", 61, 90), ("> 90", 91, None)]


def op_liste(art="D", stichtag=None):
    stichtag = stichtag or timezone.localdate()
    qs = (OffenerPosten.objects.filter(art=art, belegdatum__lte=stichtag).exclude(offen=0)
          .select_related("partner").order_by("partner__name", "faellig_am"))
    zeilen, gesamt, partner, psum = [], [NULL] * 6, None, None

    def abschluss():
        if partner is not None:
            zeilen.append(_z(["", f"Summe {partner.name}", "", ""] + psum, "summe"))

    for op in qs:
        if op.partner != partner:
            abschluss()
            partner, psum = op.partner, [NULL] * 6
            zeilen.append(_z([op.konto.nummer, op.partner.name, "", "", "", "", "", "", "", ""], "kopf"))
        tage = op.tage_ueberfaellig(stichtag)
        werte = [op.offen] + [NULL] * 5
        for i, (_, ab, bis_) in enumerate(RASTER, start=1):
            if (ab is None and tage <= 0) or (ab is not None and tage >= ab and (bis_ is None or tage <= bis_)):
                werte[i] = op.offen
        psum = [a + b for a, b in zip(psum, werte)]
        gesamt = [a + b for a, b in zip(gesamt, werte)]
        zeilen.append(_z([op.belegnr, op.fremdbelegnr or op.belegdatum.strftime("%d.%m.%Y"),
                          op.faellig_am.strftime("%d.%m.%Y"), f"Stufe {op.mahnstufe}" if op.mahnstufe else ""]
                         + [w or "" for w in werte]))
    abschluss()
    zeilen.append(_z(["", "Gesamt", "", ""] + gesamt, "gesamt"))
    titel = "Offene Posten Debitoren" if art == "D" else "Offene Posten Kreditoren"
    return {"titel": titel, "untertitel": f"Stichtag {stichtag:%d.%m.%Y} – Fälligkeitsstruktur in Tagen",
            "spalten": [("Beleg", L), ("Fremdbeleg / Datum", L), ("Fällig", L), ("Mahnung", L), ("Offen", R)]
            + [(r[0], R) for r in RASTER], "zeilen": zeilen, "hinweise": []}


# ------------------------------------------------------------------ Bilanz / GuV

def _position(k, saldo):
    pos = k.bilanz_position
    if not pos:
        pos = {"debitor": "B.II.1", "kreditor": "P.C.4", "aktiv": "B.II.4", "passiv": "P.C.8"}.get(
            k.kontoart, "B.II.4|P.C.8")
    if "|" in pos:
        a, p = pos.split("|", 1)
        return a if saldo > 0 else p
    return pos


def bilanz_daten(bis):
    """Bilanz zum Stichtag (Bestände ab Beginn des Geschäftsjahres inkl. Saldenvortrag/EB)."""
    beginn = gj_beginn(bis)
    salden = _salden(beginn, bis)
    konten = {k.pk: k for k in Konto.objects.filter(pk__in=salden)}
    werte, hinweise, ergebnis = {}, [], NULL
    for pk, (s, h) in salden.items():
        k, saldo = konten[pk], s - h
        if not saldo:
            continue
        if k.guv_position:
            ergebnis -= saldo
            continue
        if not ist_bilanzkonto(k):
            hinweise.append(f"Konto {k.nummer} {k.bezeichnung} ohne Bilanz-/GuV-Zuordnung (Saldo {q(saldo)} €) – "
                            f"als sonstige Position ausgewiesen.")
        pos = _position(k, saldo)
        betrag = saldo if pos in G.AKTIV_CODES else -saldo
        werte[pos] = werte.get(pos, NULL) + betrag
    werte["P.A.V"] = werte.get("P.A.V", NULL) + ergebnis
    aktiva = sum((v for c, v in werte.items() if c in G.AKTIV_CODES), NULL)
    passiva = sum((v for c, v in werte.items() if c in G.PASSIV_CODES), NULL)
    return {"werte": werte, "aktiva": aktiva, "passiva": passiva, "ergebnis": ergebnis, "beginn": beginn,
            "bis": bis, "hinweise": hinweise, "ausgeglichen": aktiva == passiva}


def bilanz(bis):
    d = bilanz_daten(bis)
    zeilen = []
    for seite, gliederung, summe in (("AKTIVA", G.AKTIVA, d["aktiva"]), ("PASSIVA", G.PASSIVA, d["passiva"])):
        zeilen.append(_z([seite, ""], "kopf"))
        for _, text, positionen in gliederung:
            gruppe = sum((d["werte"].get(c, NULL) for c, _ in positionen), NULL)
            zeilen.append(_z([text, gruppe], "summe"))
            for c, t in positionen:
                if d["werte"].get(c) or c == "P.A.V":
                    zeilen.append(_z([f"    {t}", d["werte"].get(c, NULL)]))
        zeilen.append(_z([f"Summe {seite.capitalize()}", summe], "gesamt"))
    hinweise = list(d["hinweise"])
    hinweise.append("Aktiva = Passiva ✓" if d["ausgeglichen"] else
                    f"ACHTUNG: Differenz Aktiva/Passiva {q(d['aktiva'] - d['passiva'])} €")
    return {"titel": "Bilanz (§ 266 HGB, vereinfacht)",
            "untertitel": f"Stichtag {bis:%d.%m.%Y} (Geschäftsjahr ab {d['beginn']:%d.%m.%Y})",
            "spalten": [("Position", L), ("EUR", R)], "zeilen": zeilen, "hinweise": hinweise, "daten": d}


def guv_daten(von, bis):
    salden = _salden(von, bis)
    konten = {k.pk: k for k in Konto.objects.filter(pk__in=salden).exclude(guv_position="")}
    werte = {}
    for pk, k in konten.items():
        s, h = salden[pk]
        werte[k.guv_position] = werte.get(k.guv_position, NULL) + (h - s)
    return {"werte": werte, "ergebnis": sum(werte.values(), NULL)}


def guv(von, bis):
    d = guv_daten(von, bis)
    zeilen, lauf = [], NULL
    for code, text, vorzeichen in G.GUV:
        wert = d["werte"].get(code, NULL)
        lauf += wert
        zeilen.append(_z([text, wert * vorzeichen if vorzeichen < 0 else wert]))
        if code in G.GUV_ZWISCHENSUMMEN:
            zeilen.append(_z([G.GUV_ZWISCHENSUMMEN[code], lauf], "summe"))
    zeilen.append(_z(["17. Jahresüberschuss / Jahresfehlbetrag (−)", d["ergebnis"]], "gesamt"))
    return {"titel": "Gewinn- und Verlustrechnung (§ 275 Abs. 2 HGB, Gesamtkostenverfahren)",
            "untertitel": f"{von:%d.%m.%Y} – {bis:%d.%m.%Y}", "spalten": [("Position", L), ("EUR", R)],
            "zeilen": zeilen, "hinweise": ["Aufwendungen als positive Beträge, Zwischensummen vorzeichenrichtig."],
            "daten": d}


# ------------------------------------------------------------------ UStVA

UST_KZ_TEXT = {
    "81": "Steuerpflichtige Umsätze 19 %", "86": "Steuerpflichtige Umsätze 7 %",
    "41": "Innergemeinschaftliche Lieferungen an Abnehmer mit USt-IdNr.", "43": "Weitere steuerfreie Umsätze "
    "mit Vorsteuerabzug (Ausfuhr)", "89": "Innergemeinschaftliche Erwerbe 19 %", "93": "Innergemeinschaftliche "
    "Erwerbe 7 %", "84": "Leistungen nach § 13b UStG (Leistungsempfänger)", "85": "Steuer zu Kz 84",
    "66": "Vorsteuerbeträge aus Rechnungen anderer Unternehmer", "61": "Vorsteuer aus innergemeinschaftlichem "
    "Erwerb", "67": "Vorsteuer aus Leistungen nach § 13b UStG",
}
UST_REIHENFOLGE = ["81", "86", "41", "43", "89", "93", "84", "85", "66", "61", "67"]


def ustva_daten(von, bis):
    bem, ust, vst = {}, {}, {}
    qs = (Buchungszeile.objects.filter(buchung__belegdatum__gte=von, buchung__belegdatum__lte=bis)
          .exclude(steuerschluessel=None).select_related("steuerschluessel"))
    for z in qs:
        st = z.steuerschluessel
        if not z.ist_steuer:
            if st.kz_bemessung:
                ausgang = st.art in ("UST", "IGL", "FREI")
                b = (z.haben - z.soll) if ausgang else (z.soll - z.haben)
                bem[st.kz_bemessung] = bem.get(st.kz_bemessung, NULL) + b
            continue
        if st.art == "UST" or (st.zwei_seitig and z.konto_id == st.gegenkonto_id):
            kz = st.kz_steuer or st.kz_bemessung
            ust[kz] = ust.get(kz, NULL) + (z.haben - z.soll)
        else:
            kz = st.kz_vorsteuer or "66"
            vst[kz] = vst.get(kz, NULL) + (z.soll - z.haben)
    # Bemessungsgrundlagen sind in der UStVA auf volle Euro abzurunden – hier zusätzlich exakt ausgewiesen
    summe_ust, summe_vst = sum(ust.values(), NULL), sum(vst.values(), NULL)
    return {"bemessung": bem, "ust": ust, "vst": vst, "summe_ust": summe_ust, "summe_vst": summe_vst,
            "zahllast": summe_ust - summe_vst}


def ustva(von, bis):
    d = ustva_daten(von, bis)
    zeilen = [_z(["", "Lieferungen und sonstige Leistungen / Erwerbe", "", ""], "kopf")]
    for kz in UST_REIHENFOLGE:
        b, u, v = d["bemessung"].get(kz), d["ust"].get(kz), d["vst"].get(kz)
        if b is None and u is None and v is None and kz not in ("81", "86", "66"):
            continue
        if kz == "66":
            zeilen.append(_z(["", "Abziehbare Vorsteuerbeträge", "", ""], "kopf"))
        zeilen.append(_z([kz, UST_KZ_TEXT.get(kz, ""), "" if b is None else int(b), u if u is not None else
                          (v if v is not None else "")]))
    zeilen.append(_z(["", "Summe Umsatzsteuer", "", d["summe_ust"]], "summe"))
    zeilen.append(_z(["", "Summe Vorsteuer", "", d["summe_vst"]], "summe"))
    zeilen.append(_z(["83", "Verbleibende Umsatzsteuer-Vorauszahlung (− = Überschuss)", "", d["zahllast"]],
                     "gesamt"))
    return {"titel": "Umsatzsteuer-Voranmeldung (Kennziffern-Übersicht)",
            "untertitel": f"Voranmeldungszeitraum {von:%d.%m.%Y} – {bis:%d.%m.%Y}",
            "spalten": [("Kz", L), ("Bezeichnung", L), ("Bemessungsgrundlage (volle €)", R), ("Steuer", R)],
            "zeilen": zeilen, "daten": d,
            "hinweise": ["Kein ELSTER-Versand. Werte vor Übermittlung durch Steuerberater prüfen lassen."]}


# ------------------------------------------------------------------ DATEV-Buchungsstapel

DATEV_SPALTEN = ["Umsatz (ohne Soll/Haben-Kz)", "Soll/Haben-Kennzeichen", "WKZ Umsatz", "Kurs", "Basis-Umsatz",
                 "WKZ Basis-Umsatz", "Konto", "Gegenkonto (ohne BU-Schlüssel)", "BU-Schlüssel", "Belegdatum",
                 "Belegfeld 1", "Belegfeld 2", "Skonto", "Buchungstext"]


def _datev_betrag(d):
    return f"{q(d):.2f}".replace(".", ",")


def _datev_text(t, laenge):
    return (t or "").replace('"', "'").replace(";", ",")[:laenge]


def datev_zeilen(b):
    """Buchung → DATEV-Zeilen (Konto/Gegenkonto). Steuerzeilen gehen über den BU-Schlüssel in den Bruttobetrag."""
    zeilen = list(b.zeilen.all())
    haupt_kandidaten = [z for z in zeilen if not z.ist_steuer]
    steuer_zu = {}
    for z in zeilen:
        if z.ist_steuer and z.zu_zeile_id:
            steuer_zu.setdefault(z.zu_zeile_id, []).append(z)
    soll = [z for z in haupt_kandidaten if z.soll]
    haben = [z for z in haupt_kandidaten if z.haben]
    if len(soll) == 1 and not steuer_zu.get(soll[0].pk):
        haupt = soll[0]
    elif len(haben) == 1 and not steuer_zu.get(haben[0].pk):
        haupt = haben[0]
    else:
        ohne = [z for z in haupt_kandidaten if not steuer_zu.get(z.pk)] or haupt_kandidaten
        haupt = max(ohne, key=lambda z: z.soll + z.haben)
    ergebnis = []
    for z in haupt_kandidaten:
        if z.pk == haupt.pk:
            continue
        betrag = z.soll - z.haben + sum((s.soll - s.haben for s in steuer_zu.get(z.pk, [])), NULL)
        st = z.steuerschluessel
        bu = st.datev_bu if st and st.datev_bu and not z.konto.steuer_automatik else ""
        ergebnis.append((abs(betrag), "S" if betrag > 0 else "H", z.konto.nummer, haupt.konto.nummer, bu,
                         z.text or b.text))
    return ergebnis


def datev_csv(von, bis):
    """DATEV-Format (EXTF, Version 700, Buchungsstapel Formatversion 13) – Encoding Windows-1252."""
    jetzt = timezone.localtime()
    beginn = gj_beginn(von)
    r = rahmen()
    kopf = ['"EXTF"', "700", "21", '"Buchungsstapel"', "13", jetzt.strftime("%Y%m%d%H%M%S") + "000", "", '"RE"',
            '"ERP"', '""', str(getattr(settings, "ERP_DATEV_BERATERNUMMER", "1001")),
            str(getattr(settings, "ERP_DATEV_MANDANTENNUMMER", "1")), beginn.strftime("%Y%m%d"), "4",
            von.strftime("%Y%m%d"), bis.strftime("%Y%m%d"), f'"Buchungsstapel {von:%m/%Y}"', '""', "1", "0", "0",
            '"EUR"', "", '""', "", "", f'"{r[-2:]}"', "", "", '""', '""']
    puffer = io.StringIO()
    puffer.write(";".join(kopf) + "\r\n")
    puffer.write(";".join(DATEV_SPALTEN) + "\r\n")
    qs = (Buchung.objects.filter(belegdatum__gte=von, belegdatum__lte=bis).order_by("belegdatum", "pk")
          .prefetch_related("zeilen__konto", "zeilen__steuerschluessel"))
    anzahl = 0
    for b in qs:
        for betrag, sh, konto_nr, gegen, bu, text in datev_zeilen(b):
            if not betrag:
                continue
            werte = [_datev_betrag(betrag), f'"{sh}"', '"EUR"', "", "", '""', konto_nr, gegen, f'"{bu}"',
                     b.belegdatum.strftime("%d%m"), f'"{_datev_text(b.belegnr, 36)}"', '""', "",
                     f'"{_datev_text(text, 60)}"']
            puffer.write(";".join(werte) + "\r\n")
            anzahl += 1
    return puffer.getvalue().encode("cp1252", errors="replace"), anzahl


# ------------------------------------------------------------------ Export

def _format(w):
    if isinstance(w, Decimal):
        return f"{w:.2f}".replace(".", ",")
    return "" if w is None else str(w)


def als_csv(auswertung):
    puffer = io.StringIO()
    schreiber = csv.writer(puffer, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    schreiber.writerow([auswertung["titel"], auswertung.get("untertitel", "")])
    schreiber.writerow([s for s, _ in auswertung["spalten"]])
    for z in auswertung["zeilen"]:
        schreiber.writerow([_format(w) for w in z["werte"]])
    return ("\ufeff" + puffer.getvalue()).encode("utf-8")


def als_pdf(auswertung):
    from weasyprint import HTML
    from django.template.loader import get_template

    from apps.core.models import Mandant

    html = get_template("fibu/pdf/auswertung.html").render({
        "a": auswertung, "mandant": Mandant.aktuell(), "jetzt": timezone.localtime(),
        "quer": len(auswertung["spalten"]) > 7})
    return HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf()


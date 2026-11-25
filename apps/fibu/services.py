"""Buchungslogik der Finanzbuchhaltung (Hauptbuch, Nebenbücher, Automatik, Abschluss).

Grundsätze:
* Jede Buchung ist ausgeglichen (Summe Soll = Summe Haben) – sonst FibuFehler.
* Steuerzeilen entstehen automatisch aus dem Steuerschlüssel der Netto-Zeile:
  USt/VSt auf derselben Seite wie die Netto-Zeile; igE/§ 13b zweiseitig (VSt auf derselben Seite,
  geschuldete USt auf der Gegenseite → saldoneutral).
* Festschreibung (GoBD) per Hash-Kette; danach nur noch Storno (Gegenbuchung).
* Automatikbuchungen sind über (quelle, quelle_id) idempotent.
* Buchungsnummern laufen über den Nummernkreis FIBU_BUCHUNG (Jahr = Erfassungsjahr, damit Nachbuchungen
  in Vorperioden nicht an der Jahreslogik des Nummernkreises scheitern).
"""
import calendar
import hashlib
import json
from datetime import date, timedelta
from datetime import timezone as dt_timezone
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Max, Q, Sum
from django.utils import timezone

from apps.core.models import Mandant
from apps.core.nummernkreise import naechste_nummer
from apps.stammdaten.models import Konto

from .models import (NULL, Ausgleich, Buchung, Buchungszeile, Eingangsrechnung, FibuGesperrt, Geschaeftsjahr,
                     Kontierungsregel, OffenerPosten, Periode, Steuerschluessel)

FIBU_SPERRE = 470816  # Advisory-Lock für die Hash-Kette des Journals (Belege nutzen 470815)
CENT = Decimal("0.01")


class FibuFehler(Exception):
    """Fachlicher Fehler bei einer Buchung (wird dem Benutzer angezeigt)."""


def q(wert):
    return Decimal(wert or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def euro(wert):
    return f"{q(wert):,.2f} €".replace(",", "§").replace(".", ",").replace("§", ".")


# ------------------------------------------------------------------ Kontenrahmen

def rahmen():
    m = Mandant.aktuell()
    return (m.kontenrahmen if m else "") or "SKR03"


def konto(nr):
    if isinstance(nr, Konto):
        return nr
    try:
        return Konto.objects.get(kontenrahmen=rahmen(), nummer=str(nr))
    except Konto.DoesNotExist as exc:
        raise FibuFehler(f"Konto {nr} existiert im Kontenrahmen {rahmen()} nicht.") from exc


def automatikkonto(code):
    k = Konto.objects.filter(kontenrahmen=rahmen(), automatik=code).order_by("nummer").first()
    if not k:
        raise FibuFehler(f"Kein Automatikkonto „{code}“ im Kontenrahmen {rahmen()} hinterlegt.")
    return k


def steuerschluessel(code):
    if not code:
        return None
    if isinstance(code, Steuerschluessel):
        return code
    try:
        return Steuerschluessel.objects.select_related("steuerkonto", "gegenkonto").get(
            kontenrahmen=rahmen(), code=code)
    except Steuerschluessel.DoesNotExist as exc:
        raise FibuFehler(f"Steuerschlüssel {code} existiert nicht.") from exc


def regel_konto(vorgang, artikelart="", schluessel=""):
    """Kontierungsregel mit Fallback: Artikelart+Schlüssel → Artikelart → Schlüssel → allgemein."""
    regeln = Kontierungsregel.objects.filter(kontenrahmen=rahmen(), vorgang=vorgang,
                                             artikelart__in=[artikelart or "", ""],
                                             steuerschluessel__in=[schluessel or "", ""]).select_related("konto")
    beste = max(regeln, key=lambda r: (2 if r.artikelart else 0) + (1 if r.steuerschluessel else 0), default=None)
    if not beste:
        raise FibuFehler(f"Keine Kontierungsregel für {vorgang} ({artikelart or '*'}/{schluessel or '*'}).")
    return beste.konto


def schluessel_fuer_satz(satz, einkauf=False, partner=None):
    """Steuersatz eines Vorsystems → Steuerschlüssel (0 % Verkauf: igL bei EU-USt-IdNr., sonst Ausfuhr)."""
    satz = Decimal(satz or 0)
    if satz == 19:
        return "V19" if einkauf else "U19"
    if satz == 7:
        return "V7" if einkauf else "U7"
    if satz == 0:
        if einkauf:
            return ""
        ustid = (partner.ust_id if partner else "") or ""
        return "IGL" if ustid and not ustid.upper().startswith("DE") else "AUS"
    raise FibuFehler(f"Für den Steuersatz {satz} % gibt es keinen Steuerschlüssel.")


# ------------------------------------------------------------------ Geschäftsjahre / Perioden

def geschaeftsjahr_anlegen(jahr):
    gj, angelegt = Geschaeftsjahr.objects.get_or_create(
        jahr=jahr, defaults={"beginn": date(jahr, 1, 1), "ende": date(jahr, 12, 31)})
    if angelegt or not gj.perioden.exists():
        for m in range(1, 13):
            Periode.objects.get_or_create(geschaeftsjahr=gj, nummer=m, defaults={
                "beginn": date(jahr, m, 1), "ende": date(jahr, m, calendar.monthrange(jahr, m)[1])})
    return gj


def periode_fuer(datum):
    p = Periode.objects.select_related("geschaeftsjahr").filter(beginn__lte=datum, ende__gte=datum).first()
    if not p:
        raise FibuFehler(f"Für den {datum:%d.%m.%Y} ist kein Geschäftsjahr angelegt.")
    if p.gesperrt:
        raise FibuGesperrt(f"Die Periode {p} ist gesperrt – Buchungen nicht mehr möglich.")
    return p


def periode_sperren(periode, benutzer):
    """Periodenabschluss: offene Buchungen der Periode festschreiben, dann sperren."""
    with transaction.atomic():
        festschreiben(Buchung.objects.filter(periode=periode, status=Buchung.Status.ERFASST), benutzer)
        periode.gesperrt, periode.gesperrt_von, periode.gesperrt_am = True, benutzer, timezone.now()
        periode.save()
    return periode


def periode_entsperren(periode):
    if periode.geschaeftsjahr.abgeschlossen:
        raise FibuFehler("Das Geschäftsjahr ist abgeschlossen – Perioden können nicht mehr geöffnet werden.")
    periode.gesperrt, periode.gesperrt_von, periode.gesperrt_am = False, None, None
    periode.save()
    return periode


# ------------------------------------------------------------------ Buchen

def _zeilen_aufbereiten(zeilen):
    """Eingabezeilen → fertige Zeilen inkl. automatischer Steuerzeilen.

    Eingabe je Zeile: konto (Konto/Nummer), soll oder haben (netto; mit brutto=True brutto), optional
    steuerschluessel (Code/Objekt), steuer (expliziter Steuerbetrag), partner, text.
    """
    fertig = []
    for z in zeilen:
        soll, haben = q(z.get("soll")), q(z.get("haben"))
        if soll < 0:  # negative Beträge auf die Gegenseite
            soll, haben = NULL, haben - soll
        if haben < 0:
            soll, haben = soll - haben, NULL
        if soll and haben:
            raise FibuFehler("Eine Buchungszeile darf nicht gleichzeitig Soll und Haben enthalten.")
        if not soll and not haben:
            continue
        st = steuerschluessel(z.get("steuerschluessel"))
        k = konto(z["konto"])
        betrag = soll or haben
        steuer = NULL
        if st and st.satz and st.art in ("UST", "VST", "IGE", "RC"):
            if z.get("steuer") is not None:
                steuer = q(z["steuer"])
            elif z.get("brutto") and not st.zwei_seitig:
                netto = q(betrag * 100 / (100 + st.satz))
                steuer, betrag = betrag - netto, netto
            else:
                steuer = q(betrag * st.satz / 100)
            if not st.steuerkonto or (st.zwei_seitig and not st.gegenkonto):
                raise FibuFehler(f"Steuerschlüssel {st.code}: Steuerkonto fehlt.")
        seite = "soll" if soll else "haben"
        gegen = "haben" if seite == "soll" else "soll"
        haupt = {"konto": k, seite: betrag, "steuerschluessel": st, "partner": z.get("partner"),
                 "text": z.get("text", ""), "ist_steuer": False, "steuer": []}
        if steuer:
            haupt["steuer"].append({"konto": st.steuerkonto, seite: steuer, "steuerschluessel": st,
                                    "ist_steuer": True, "text": f"{st.code} {st.satz:g} %"})
            if st.zwei_seitig:
                haupt["steuer"].append({"konto": st.gegenkonto, gegen: steuer, "steuerschluessel": st,
                                        "ist_steuer": True, "text": f"{st.code} USt {st.satz:g} %"})
        fertig.append(haupt)
    return fertig


def buchen(belegdatum, belegnr, text, zeilen, quelle="", quelle_id="", benutzer=None, roh=False, storno_von=None):
    """Legt eine Buchung an. `roh=True`: Zeilen werden 1:1 übernommen (Storno), ohne Steuerautomatik."""
    quelle_id = str(quelle_id or "")
    if quelle:
        vorhanden = Buchung.objects.filter(quelle=quelle, quelle_id=quelle_id).first()
        if vorhanden:
            return vorhanden
    if not (belegnr or "").strip():
        raise FibuFehler("Bitte eine Belegnummer angeben.")
    if not (text or "").strip():
        raise FibuFehler("Bitte einen Buchungstext angeben.")
    fertig = zeilen if roh else _zeilen_aufbereiten(zeilen)
    alle = []
    for z in fertig:
        alle.append(z)
        alle.extend(z.get("steuer", []))
    if len(alle) < 2:
        raise FibuFehler("Eine Buchung braucht mindestens zwei Zeilen (Soll und Haben).")
    s_soll = sum((q(z.get("soll")) for z in alle), NULL)
    s_haben = sum((q(z.get("haben")) for z in alle), NULL)
    if s_soll != s_haben:
        raise FibuFehler(f"Buchung nicht ausgeglichen: Soll {euro(s_soll)} ≠ Haben {euro(s_haben)}.")
    periode = periode_fuer(belegdatum)
    with transaction.atomic():
        b = Buchung.objects.create(
            nummer=naechste_nummer("FIBU_BUCHUNG", timezone.localdate()), periode=periode, belegdatum=belegdatum,
            belegnr=belegnr.strip()[:40], text=text.strip()[:200], quelle=quelle, quelle_id=quelle_id,
            erfasst_von=benutzer if getattr(benutzer, "pk", None) else None, storno_von=storno_von)
        pos = 0
        for z in fertig:
            pos += 1
            haupt = Buchungszeile.objects.create(
                buchung=b, position=pos, konto=konto(z["konto"]), soll=q(z.get("soll")), haben=q(z.get("haben")),
                text=(z.get("text") or "")[:200], steuerschluessel=z.get("steuerschluessel"),
                ist_steuer=z.get("ist_steuer", False), partner=z.get("partner"))
            for s in z.get("steuer", []):
                pos += 1
                Buchungszeile.objects.create(
                    buchung=b, position=pos, konto=s["konto"], soll=q(s.get("soll")), haben=q(s.get("haben")),
                    text=s.get("text", "")[:200], steuerschluessel=s.get("steuerschluessel"), ist_steuer=True,
                    zu_zeile=haupt)
    return b


def pruefsumme_berechnen(b):
    inhalt = {
        "nummer": b.nummer, "belegdatum": b.belegdatum.isoformat(), "belegnr": b.belegnr, "text": b.text,
        "quelle": b.quelle, "quelle_id": b.quelle_id, "storno_von": b.storno_von_id,
        "festgeschrieben_am": b.festgeschrieben_am.astimezone(dt_timezone.utc).isoformat(),
        "ketten_index": b.ketten_index, "vorgaenger": b.vorgaenger_pruefsumme,
        "zeilen": [[z.position, z.konto.kontenrahmen, z.konto.nummer, str(z.soll), str(z.haben), z.text,
                    z.steuerschluessel.code if z.steuerschluessel_id else "", z.ist_steuer, z.partner_id]
                   for z in b.zeilen.select_related("konto", "steuerschluessel").order_by("position", "pk")],
    }
    roh = json.dumps(inhalt, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()


def festschreiben(buchungen, benutzer=None):
    """GoBD-Festschreibung einer Buchung oder eines QuerySets; liefert die Anzahl."""
    if isinstance(buchungen, Buchung):
        buchungen = [buchungen]
    anzahl = 0
    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", [FIBU_SPERRE])
        ids = sorted(b.pk for b in buchungen)
        for pk in ids:
            b = Buchung.objects.select_for_update().get(pk=pk)
            if b.festgeschrieben:
                continue
            letzter = Buchung.objects.exclude(ketten_index=None).aggregate(m=Max("ketten_index"))["m"] or 0
            b.vorgaenger_pruefsumme = (Buchung.objects.filter(ketten_index=letzter)
                                       .values_list("pruefsumme", flat=True).first() or "")
            b.ketten_index = letzter + 1
            b.festgeschrieben_von = benutzer if getattr(benutzer, "pk", None) else None
            b.festgeschrieben_am = timezone.now()
            b.status = Buchung.Status.FESTGESCHRIEBEN
            b.pruefsumme = pruefsumme_berechnen(b)
            b._festschreibung = True
            b.save()
            anzahl += 1
    return anzahl


def kette_pruefen():
    """Prüft die Hash-Kette aller festgeschriebenen Buchungen → (anzahl, [Fehler])."""
    fehler, vorher, anzahl = [], "", 0
    for b in Buchung.objects.exclude(ketten_index=None).order_by("ketten_index"):
        anzahl += 1
        if b.vorgaenger_pruefsumme != vorher:
            fehler.append(f"{b.nummer}: Verkettung unterbrochen.")
        if pruefsumme_berechnen(b) != b.pruefsumme:
            fehler.append(f"{b.nummer}: Inhalt nach Festschreibung verändert (Prüfsumme abweichend).")
        vorher = b.pruefsumme
    return anzahl, fehler


def stornieren(buchung, benutzer=None, datum=None, grund=""):
    """Storno per Gegenbuchung (Soll/Haben vertauscht); OPs und Ausgleiche werden zurückgenommen."""
    b = Buchung.objects.get(pk=buchung.pk)
    if b.storno_von_id:
        raise FibuFehler("Eine Stornobuchung kann nicht storniert werden.")
    if Buchung.objects.filter(storno_von=b).exists():
        raise FibuFehler(f"Buchung {b.nummer} ist bereits storniert.")
    for op in b.offene_posten.all():
        if op.offen != op.betrag:
            raise FibuFehler(f"OP {op.belegnr} ist (teil-)ausgeglichen – zuerst die Zahlung stornieren.")
    datum = datum or b.belegdatum
    if Periode.objects.filter(beginn__lte=datum, ende__gte=datum, gesperrt=True).exists():
        datum = timezone.localdate()
    zeilen = []
    for z in b.zeilen.select_related("konto", "steuerschluessel").order_by("position"):
        zeilen.append({"konto": z.konto, "soll": z.haben, "haben": z.soll, "steuerschluessel": z.steuerschluessel,
                       "ist_steuer": z.ist_steuer, "partner": z.partner, "text": z.text})
    with transaction.atomic():
        st = buchen(datum, b.belegnr, f"Storno {b.nummer}: {grund or b.text}"[:200], zeilen, roh=True,
                    benutzer=benutzer, storno_von=b)
        for op in b.offene_posten.select_for_update():
            Ausgleich.objects.create(op=op, buchung=st, datum=datum, art=Ausgleich.Art.STORNO, betrag=op.offen,
                                     benutzer=_user(benutzer))
            op.offen = NULL
            op.save(update_fields=["offen"])
        for a in Ausgleich.objects.filter(buchung=b).select_related("op"):
            op = OffenerPosten.objects.select_for_update().get(pk=a.op_id)
            op.offen += a.betrag
            op.save(update_fields=["offen"])
            Ausgleich.objects.create(op=op, buchung=st, datum=datum, art=Ausgleich.Art.STORNO, betrag=-a.betrag,
                                     benutzer=_user(benutzer))
        Eingangsrechnung.objects.filter(buchung=b).update(status=Eingangsrechnung.Status.STORNIERT)
        from .models import Bankumsatz

        for u in Bankumsatz.objects.filter(Q(buchung=b) | Q(zuordnung_buchung=b)):
            if u.zuordnung_buchung_id == b.pk:
                u.zuordnung_buchung, u.status = None, Bankumsatz.Status.VERRECHNUNG
            else:
                u.buchung, u.zuordnung_buchung, u.status = None, None, Bankumsatz.Status.OFFEN
            u.save()
    return st


def _user(benutzer):
    return benutzer if getattr(benutzer, "pk", None) else None


def saldo(k, von=None, bis=None):
    qs = Buchungszeile.objects.filter(konto=k)
    if von:
        qs = qs.filter(buchung__belegdatum__gte=von)
    if bis:
        qs = qs.filter(buchung__belegdatum__lte=bis)
    s = qs.aggregate(s=Sum("soll"), h=Sum("haben"))
    return (s["s"] or NULL) - (s["h"] or NULL)


# ------------------------------------------------------------------ Personenkonten / OP

def personenkonto(partner, art):
    """Debitoren- (10000 ff.) bzw. Kreditorenkonto (70000 ff.) des Partners – bei Bedarf angelegt."""
    feld = "debitorenkonto" if art == "D" else "kreditorenkonto"
    kontoart = "debitor" if art == "D" else "kreditor"
    r = rahmen()
    nr = getattr(partner, feld)
    if nr:
        k = Konto.objects.filter(kontenrahmen=r, nummer=nr).first()
        if k:
            return k
    else:
        start = 10000 if art == "D" else 70000
        vorhanden = {int(n) for n in Konto.objects.filter(kontenrahmen=r, kontoart=kontoart)
                     .values_list("nummer", flat=True) if n.isdigit()}
        vorhanden |= {int(n) for n in type(partner).objects.exclude(**{feld: ""}).values_list(feld, flat=True)
                      if n.isdigit()}
        nr = str(max([start - 1] + [n for n in vorhanden if start <= n < start + 60000]) + 1)
    k = Konto.objects.create(kontenrahmen=r, nummer=nr, bezeichnung=partner.name[:200], kontoart=kontoart,
                             bilanz_position="B.II.1" if art == "D" else "P.C.4", partner=partner)
    if getattr(partner, feld) != nr:
        setattr(partner, feld, nr)
        partner.save(update_fields=[feld])
    return k


def _faelligkeit(belegdatum, zb):
    faellig = belegdatum + timedelta(days=zb.tage_netto if zb else 30)
    if zb and zb.skonto_prozent and zb.skonto_tage:
        return faellig, belegdatum + timedelta(days=zb.skonto_tage), zb.skonto_prozent
    return faellig, None, NULL


def op_anlegen(art, partner, buchung, belegnr, belegdatum, betrag, zahlungsbedingung=None, steuer_aufteilung=(),
               beleg=None, eingangsrechnung=None, fremdbelegnr="", faellig_am=None, skonto=None):
    """Offenen Posten anlegen. `skonto` = (prozent, bis, betrag) laut Rechnung hat Vorrang vor der
    Zahlungsbedingung; betrag 0/None → Satz × Bruttobetrag."""
    faellig, skonto_bis, skonto_proz = _faelligkeit(belegdatum, zahlungsbedingung)
    skonto_betrag = NULL
    if skonto and skonto[0] and skonto[1]:
        skonto_proz, skonto_bis, skonto_betrag = Decimal(skonto[0]), skonto[1], q(skonto[2] or 0)
    if skonto_proz and not skonto_betrag:
        skonto_betrag = q(Decimal(betrag) * skonto_proz / 100)
    return OffenerPosten.objects.create(
        art=art, partner=partner, konto=personenkonto(partner, art), buchung=buchung, beleg=beleg,
        eingangsrechnung=eingangsrechnung, belegnr=belegnr, fremdbelegnr=fremdbelegnr, belegdatum=belegdatum,
        faellig_am=faellig_am or faellig, skonto_bis=skonto_bis, skonto_prozent=skonto_proz,
        skontobetrag=skonto_betrag, betrag=q(betrag), offen=q(betrag),
        steuer_aufteilung=[[c, str(q(n)), str(q(s))] for c, n, s in steuer_aufteilung])


def _skonto_zeilen(op, skonto):
    """Skonto anteilig auf die Steuerschlüssel des OP verteilen → Zeilen mit Steuerkorrektur."""
    vorgang = "SKONTO_G" if op.art == "D" else "SKONTO_E"
    seite = "soll" if op.art == "D" else "haben"
    aufteilung = [(c, Decimal(n), Decimal(s)) for c, n, s in (op.steuer_aufteilung or [])]
    gesamt = sum((n + s for _, n, s in aufteilung), NULL)
    if not aufteilung or not gesamt:
        return [{"konto": regel_konto(vorgang), seite: skonto, "text": f"Skonto {op.belegnr}"}]
    zeilen, rest = [], skonto
    for i, (code, n, s) in enumerate(aufteilung):
        anteil = rest if i == len(aufteilung) - 1 else q(skonto * (n + s) / gesamt)
        rest -= anteil
        st = steuerschluessel(code) if code else None
        z = {"konto": regel_konto(vorgang, "", code), seite: anteil, "text": f"Skonto {op.belegnr}"}
        if st and st.satz and st.art in ("UST", "VST"):
            netto = q(anteil * 100 / (100 + st.satz))
            z.update({seite: netto, "steuerschluessel": st, "steuer": anteil - netto})
        elif st:
            z["steuerschluessel"] = st  # igE/§ 13b: Steuer neu berechnet (saldoneutral); igL/Ausfuhr ohne Steuer
        zeilen.append(z)
    return zeilen


def op_ausgleichen(ops, zahlbetrag, datum, gegenkonto, benutzer=None, belegnr="", text="", quelle="", quelle_id="",
                   skonto=True, zusatzzeilen=(), rest_konto=None):
    """Zahlung (positiver Betrag) auf einen oder mehrere OPs gleicher Art ausgleichen.

    Reicht die Zahlung (ggf. abzüglich Skonto) für einen OP, wird er vollständig ausgeglichen und der
    Skonto mit Steuerkorrektur gebucht; sonst Teilausgleich. Überzahlung → rest_konto (z. B. ungeklärt).
    `zusatzzeilen` (z. B. Bankgebühren) werden mitgebucht.
    """
    if isinstance(ops, OffenerPosten):
        ops = [ops]
    if not ops:
        raise FibuFehler("Kein offener Posten ausgewählt.")
    art = ops[0].art
    if any(o.art != art for o in ops):
        raise FibuFehler("Debitoren- und Kreditoren-OPs können nicht gemeinsam ausgeglichen werden.")
    zahl = q(zahlbetrag)
    if zahl <= 0:
        raise FibuFehler("Der Zahlbetrag muss positiv sein.")
    rest = zahl
    zeilen, ausgleiche = [], []
    p_seite, g_seite = ("haben", "soll") if art == "D" else ("soll", "haben")
    for op in ops:
        if rest <= 0:
            break
        if op.offen <= 0:
            continue
        sk = op.skonto_betrag(datum) if skonto else NULL
        benoetigt = op.offen - sk
        if rest >= benoetigt:
            gezahlt = benoetigt
        else:
            gezahlt, sk = rest, NULL
        rest -= gezahlt
        zeilen.append({"konto": op.konto, p_seite: gezahlt + sk, "partner": op.partner, "text": f"Ausgleich {op.belegnr}"})
        if sk:
            zeilen.extend(_skonto_zeilen(op, sk))
        ausgleiche.append((op, gezahlt, sk))
    zeilen.insert(0, {"konto": gegenkonto, g_seite: zahl, "text": text or "Zahlung"})
    if rest > 0:
        zeilen.append({"konto": rest_konto or automatikkonto("UNGEKLAERT"), p_seite: rest,
                       "text": "Überzahlung / nicht zugeordnet"})
    zeilen.extend(zusatzzeilen)
    if not ausgleiche:
        raise FibuFehler("Die ausgewählten Posten sind bereits ausgeglichen.")
    with transaction.atomic():
        b = buchen(datum, belegnr or ops[0].belegnr, text or f"Zahlung {', '.join(o.belegnr for o, _, _ in ausgleiche)}",
                   zeilen, quelle=quelle, quelle_id=quelle_id, benutzer=benutzer)
        for op, gezahlt, sk in ausgleiche:
            op = OffenerPosten.objects.select_for_update().get(pk=op.pk)
            if gezahlt:
                Ausgleich.objects.create(op=op, buchung=b, datum=datum, art=Ausgleich.Art.ZAHLUNG, betrag=gezahlt,
                                         benutzer=_user(benutzer))
            if sk:
                Ausgleich.objects.create(op=op, buchung=b, datum=datum, art=Ausgleich.Art.SKONTO, betrag=sk,
                                         benutzer=_user(benutzer))
            op.offen -= gezahlt + sk
            op.save(update_fields=["offen"])
    return b


# ------------------------------------------------------------------ Automatikbuchungen

def _verteilen(gruppen, steuer_soll):
    """Steuer je Schlüssel (Summe aus Vorsystem) auf mehrere Konten verteilen – Rest auf die letzte Gruppe."""
    ergebnis = {}
    for code, eintraege in gruppen.items():
        st = steuerschluessel(code) if code else None
        satz = st.satz if st else NULL
        gesamt = steuer_soll.get(code, q(sum(eintraege.values(), NULL) * satz / 100))
        rest = gesamt
        keys = list(eintraege)
        for i, k in enumerate(keys):
            anteil = rest if i == len(keys) - 1 else q(eintraege[k] * satz / 100)
            rest -= anteil
            ergebnis[(code, k)] = anteil
    return ergebnis


def ar_buchen(beleg, benutzer=None):
    """Festgeschriebene Ausgangsrechnung/Gutschrift → Debitor an Erlöse und Umsatzsteuer + OP."""
    if beleg.belegart not in ("RECHNUNG", "GUTSCHRIFT"):
        return None
    vorhanden = Buchung.objects.filter(quelle="beleg", quelle_id=str(beleg.pk)).first()
    if vorhanden:
        return vorhanden
    gruppen, sap = {}, beleg.steuer_aufstellung()
    for p in beleg.positionen.select_related("artikel"):
        if not p.betrag_netto:
            continue
        code = schluessel_fuer_satz(p.steuersatz, partner=beleg.partner)
        k = regel_konto("ERLOES", p.artikel.art if p.artikel_id else "", code)
        gruppen.setdefault(code, {}).setdefault(k.pk, NULL)
        gruppen[code][k.pk] += p.betrag_netto
    steuer_soll = {schluessel_fuer_satz(satz, partner=beleg.partner): q(werte[1]) for satz, werte in sap.items()}
    steuern = _verteilen(gruppen, steuer_soll)
    gutschrift = beleg.belegart == "GUTSCHRIFT"
    e_seite, d_seite = ("soll", "haben") if gutschrift else ("haben", "soll")
    zeilen, aufteilung = [], []
    for code, eintraege in gruppen.items():
        for k_pk, netto in eintraege.items():
            zeilen.append({"konto": Konto.objects.get(pk=k_pk), e_seite: netto, "steuerschluessel": code,
                           "steuer": steuern[(code, k_pk)], "text": beleg.nummer})
        aufteilung.append((code, sum(eintraege.values(), NULL), steuer_soll.get(code, NULL)))
    brutto = sum((n + s for _, n, s in aufteilung), NULL)
    deb = personenkonto(beleg.partner, "D")
    zeilen.insert(0, {"konto": deb, d_seite: brutto, "partner": beleg.partner, "text": beleg.partner.name})
    art = "Gutschrift" if gutschrift else "Ausgangsrechnung"
    with transaction.atomic():
        b = buchen(beleg.belegdatum, beleg.nummer, f"{art} {beleg.nummer} {beleg.partner.name}", zeilen,
                   quelle="beleg", quelle_id=beleg.pk, benutzer=benutzer)
        faktor = -1 if gutschrift else 1
        op_anlegen("D", beleg.partner, b, beleg.nummer, beleg.belegdatum, brutto * faktor,
                   None if gutschrift else (beleg.zahlungsbedingung or beleg.partner.zahlungsbedingung),
                   [(c, n * faktor, s * faktor) for c, n, s in aufteilung], beleg=beleg,
                   faellig_am=beleg.belegdatum if gutschrift else None)
    return b


def ar_stornieren(beleg, benutzer=None):
    b = Buchung.objects.filter(quelle="beleg", quelle_id=str(beleg.pk)).first()
    if b and not Buchung.objects.filter(storno_von=b).exists():
        return stornieren(b, benutzer, grund=f"Beleg {beleg.nummer} storniert")
    return None


def _bestandskonto(artikel):
    return regel_konto("BESTAND", artikel.art if artikel else "")


def we_buchen(we, benutzer=None):
    """Wareneingang → Bestand (je Artikelart) an WE/RE-Verrechnungskonto."""
    vorhanden = Buchung.objects.filter(quelle="wareneingang", quelle_id=str(we.pk)).first()
    if vorhanden:
        return vorhanden
    werte = {}
    for p in we.positionen.select_related("artikel", "lagerbewegung"):
        w = abs(p.lagerbewegung.wert) if p.lagerbewegung_id else q(p.menge * (p.lieferpreis or p.bestellpreis or 0))
        k = _bestandskonto(p.artikel)
        werte[k] = werte.get(k, NULL) + w
    gesamt = sum(werte.values(), NULL)
    if not gesamt:
        raise FibuFehler(f"Wareneingang {we.nummer} hat keinen Warenwert.")
    zeilen = [{"konto": k, "soll": w, "text": we.nummer} for k, w in werte.items()]
    zeilen.append({"konto": automatikkonto("WERE"), "haben": gesamt, "partner": we.lieferant,
                   "text": f"WE/RE {we.lieferant.name}"})
    return buchen(we.datum, we.nummer, f"Wareneingang {we.nummer} {we.lieferant.name}", zeilen,
                  quelle="wareneingang", quelle_id=we.pk, benutzer=benutzer)


def material_buchen(auftrag, entnahmen, benutzer=None):
    """Materialentnahme für die Fertigung → Materialaufwand an Bestand (je Artikelart)."""
    ids = sorted(e.pk for e in entnahmen)
    if not ids:
        return None
    qid = hashlib.sha1(",".join(map(str, ids)).encode()).hexdigest()[:20]
    vorhanden = Buchung.objects.filter(quelle="material", quelle_id=qid).first()
    if vorhanden:
        return vorhanden
    zeilen = []
    for e in entnahmen:
        w = abs(q(e.wert))
        if not w:
            continue
        art = e.artikel.art
        zeilen += [{"konto": regel_konto("MATERIAL", art), "soll": w, "text": e.artikel.artikelnummer},
                   {"konto": _bestandskonto(e.artikel), "haben": w, "text": e.artikel.artikelnummer}]
    if not zeilen:
        return None
    return buchen(timezone.localdate(), auftrag.nummer, f"Materialentnahme {auftrag.nummer}", zeilen,
                  quelle="material", quelle_id=qid, benutzer=benutzer)


def fertigmeldung_buchen(auftrag, fertigmeldung, wert, benutzer=None):
    """Fertigmeldung → Bestand fertige/unfertige Erzeugnisse an Bestandsveränderungen."""
    vorhanden = Buchung.objects.filter(quelle="fertigmeldung", quelle_id=str(fertigmeldung.pk)).first()
    if vorhanden:
        return vorhanden
    w = abs(q(wert))
    if not w:
        return None
    art = auftrag.artikel.art
    zeilen = [{"konto": _bestandskonto(auftrag.artikel), "soll": w, "text": auftrag.artikel.artikelnummer},
              {"konto": regel_konto("BV", art), "haben": w, "text": auftrag.nummer}]
    datum = timezone.localtime(fertigmeldung.zeitpunkt).date() if fertigmeldung.zeitpunkt else timezone.localdate()
    return buchen(datum, auftrag.nummer, f"Fertigmeldung {auftrag.nummer}", zeilen, quelle="fertigmeldung",
                  quelle_id=fertigmeldung.pk, benutzer=benutzer)


# ------------------------------------------------------------------ Eingangsrechnungen

def er_summen(er):
    gruppen = {}
    for p in er.positionen.select_related("steuerschluessel"):
        gruppen.setdefault(p.steuerschluessel, NULL)
        gruppen[p.steuerschluessel] += p.betrag_netto
    netto = sum(gruppen.values(), NULL)
    steuer = sum((q(n * st.satz / 100) for st, n in gruppen.items() if st.art == "VST"), NULL)
    er.summe_netto, er.summe_steuer, er.summe_brutto = netto, steuer, netto + steuer
    er.save(update_fields=["summe_netto", "summe_steuer", "summe_brutto"])
    return gruppen


def _we_preis(bp):
    """Preis laut Wareneingang (Lieferpreis), sonst Nettopreis der Bestellung."""
    from apps.wareneingang.models import WEPosition

    wp = WEPosition.objects.filter(bestellposition=bp).exclude(lieferpreis=None).order_by("-pk").first()
    return Decimal(wp.lieferpreis) if wp else Decimal(bp.nettopreis)


def er_pruefen(er):
    """Rechnungsprüfung gegen Bestellung/Wareneingang (Menge und Preis) → Liste von Hinweisen."""
    toleranz = Decimal(str(getattr(settings, "ERP_RECHNUNGSPRUEFUNG_TOLERANZ_PROZENT", 1)))
    ergebnis = []
    for p in er.positionen.select_related("bestellposition", "artikel"):
        bp = p.bestellposition
        if not bp:
            if er.bestellung_id:
                ergebnis.append({"position": p.position, "art": "OHNE_BEZUG",
                                 "text": "Position ohne Bestellbezug – Kontierung über Aufwandskonto."})
            continue
        berechnet = (er.__class__.objects.filter(status="GEBUCHT").exclude(pk=er.pk)
                     .filter(positionen__bestellposition=bp).aggregate(m=Sum("positionen__menge"))["m"] or 0)
        geliefert = Decimal(bp.geliefert_menge or 0)
        if p.menge + berechnet > geliefert:
            ergebnis.append({"position": p.position, "art": "MENGE",
                             "text": f"Menge {p.menge:g} (bereits berechnet {Decimal(berechnet):g}) übersteigt "
                                     f"gelieferte Menge {geliefert:g}."})
        soll = _we_preis(bp)
        if soll and abs(p.einzelpreis - soll) / soll * 100 > toleranz:
            ergebnis.append({"position": p.position, "art": "PREIS",
                             "text": f"Preis {p.einzelpreis:.4f} € weicht vom Bestell-/WE-Preis {soll:.4f} € ab "
                                     f"({(p.einzelpreis - soll) / soll * 100:+.1f} %, Toleranz {toleranz} %)."})
    er.pruefung = ergebnis
    er.abweichung = any(e["art"] in ("MENGE", "PREIS") for e in ergebnis)
    er.save(update_fields=["pruefung", "abweichung"])
    return ergebnis


def er_skonto(er):
    """Skonto laut Eingangsrechnung → (prozent, bis, betrag) oder None (dann gilt die Zahlungsbedingung)."""
    if not er.skonto_prozent:
        return None
    bis = er.skonto_bis
    if bis is None and er.skonto_tage is not None:
        bis = er.rechnungsdatum + timedelta(days=er.skonto_tage)
    if bis is None:
        return None
    return er.skonto_prozent, bis, er.skontobetrag or q(er.summe_brutto * er.skonto_prozent / 100)


def er_buchen(er, benutzer=None):
    """Eingangsrechnung → WE/RE (bzw. Aufwand) + Vorsteuer an Kreditor; Preisdifferenz auf Aufwand; OP."""
    if er.status == Eingangsrechnung.Status.GEBUCHT and er.buchung_id:
        return er.buchung
    if er.status != Eingangsrechnung.Status.ERFASST:
        raise FibuFehler("Nur erfasste Eingangsrechnungen können gebucht werden.")
    if not er.positionen.exists():
        raise FibuFehler("Die Eingangsrechnung hat keine Positionen.")
    er_pruefen(er)
    if er.abweichung and not er.abweichung_akzeptiert:
        raise FibuFehler("Rechnungsprüfung mit Abweichung – bitte prüfen und die Abweichung freigeben.")
    gruppen = er_summen(er)
    # Netto je (Schlüssel, Konto) sammeln; Preisdifferenzen ohne Schlüssel, Steuer am Hauptkonto
    netto_je, differenzen = {}, {}
    were = automatikkonto("WERE")
    for p in er.positionen.select_related("bestellposition", "artikel", "steuerschluessel"):
        code = p.steuerschluessel.code
        art = p.artikel.art if p.artikel_id else ""
        if p.bestellposition_id:
            we_wert = q(p.menge * _we_preis(p.bestellposition))
            netto_je.setdefault(code, {}).setdefault(were.pk, NULL)
            netto_je[code][were.pk] += we_wert
            diff = p.betrag_netto - we_wert
            if diff:
                k = regel_konto("AUFWAND", art, code)
                differenzen[k.pk] = differenzen.get(k.pk, NULL) + diff
        else:
            k = p.konto or regel_konto("AUFWAND", art, code)
            netto_je.setdefault(code, {}).setdefault(k.pk, NULL)
            netto_je[code][k.pk] += p.betrag_netto
    steuer_soll = {st.code: q(n * st.satz / 100) for st, n in gruppen.items()}
    # Steuer auf Basis des Gesamtnettos je Schlüssel – inkl. Preisdifferenzen
    steuern = {}
    for code, eintraege in netto_je.items():
        rest, keys = steuer_soll.get(code, NULL), list(eintraege)
        satz = steuerschluessel(code).satz
        for i, k in enumerate(keys):
            anteil = rest if i == len(keys) - 1 else q(eintraege[k] * satz / 100)
            rest -= anteil
            steuern[(code, k)] = anteil
    zeilen = []
    for code, eintraege in netto_je.items():
        for k_pk, n in eintraege.items():
            zeilen.append({"konto": Konto.objects.get(pk=k_pk), "soll": n, "steuerschluessel": code,
                           "steuer": steuern[(code, k_pk)], "text": er.rechnungsnummer})
    for k_pk, d in differenzen.items():
        zeilen.append({"konto": Konto.objects.get(pk=k_pk), "soll" if d > 0 else "haben": abs(d),
                       "text": f"Preisdifferenz {er.rechnungsnummer}"})
    kred = personenkonto(er.lieferant, "K")
    zeilen.insert(0, {"konto": kred, "haben": er.summe_brutto, "partner": er.lieferant, "text": er.lieferant.name})
    aufteilung = [(st.code, n, steuer_soll[st.code] if st.art == "VST" else NULL) for st, n in gruppen.items()]
    with transaction.atomic():
        b = buchen(er.rechnungsdatum, er.nummer, f"ER {er.rechnungsnummer} {er.lieferant.name}", zeilen,
                   quelle="eingangsrechnung", quelle_id=er.pk, benutzer=benutzer)
        op_anlegen("K", er.lieferant, b, er.nummer, er.rechnungsdatum, er.summe_brutto,
                   er.zahlungsbedingung or er.lieferant.zahlungsbedingung, aufteilung, eingangsrechnung=er,
                   fremdbelegnr=er.rechnungsnummer, faellig_am=er.faellig_am, skonto=er_skonto(er))
        er.status, er.buchung = Eingangsrechnung.Status.GEBUCHT, b
        er.save(update_fields=["status", "buchung"])
    return b


def er_aus_wareneingang(we, rechnungsnummer, rechnungsdatum, benutzer=None, preise=None):
    """Hilfsfunktion: Eingangsrechnung mit den Positionen eines Wareneingangs vorbelegen."""
    with transaction.atomic():
        er = Eingangsrechnung.objects.create(
            nummer=naechste_nummer("FIBU_ER", timezone.localdate()), lieferant=we.lieferant,
            rechnungsnummer=rechnungsnummer, rechnungsdatum=rechnungsdatum, bestellung=we.bestellung,
            zahlungsbedingung=we.lieferant.zahlungsbedingung, erfasst_von=_user(benutzer))
        for i, p in enumerate(we.positionen.select_related("bestellposition", "artikel"), start=1):
            bp = p.bestellposition
            preis = (preise or {}).get(p.artikel.artikelnummer) or p.lieferpreis or bp.nettopreis
            er.positionen.create(position=i * 10, bestellposition=bp, artikel=p.artikel,
                                 bezeichnung=p.artikel.bezeichnung, menge=p.menge, einzelpreis=preis,
                                 steuerschluessel=steuerschluessel(schluessel_fuer_satz(bp.steuersatz, einkauf=True)
                                                                   or "V19"))
        er_summen(er)
    return er


# ------------------------------------------------------------------ Jahresabschluss

BILANZ_ARTEN = ("aktiv", "passiv", "steuer", "debitor", "kreditor")


def ist_bilanzkonto(k):
    return bool(k.bilanz_position) or k.kontoart in BILANZ_ARTEN


def saldenvortrag(jahr, benutzer=None):
    """Bilanzkonten des Jahres als Eröffnungsbuchung ins Folgejahr; GuV-Ergebnis → Gewinnvortrag."""
    gj = Geschaeftsjahr.objects.get(jahr=jahr)
    neu = geschaeftsjahr_anlegen(jahr + 1)
    vorhanden = Buchung.objects.filter(quelle="saldenvortrag", quelle_id=str(jahr)).first()
    if vorhanden:
        return vorhanden
    salden = (Buchungszeile.objects.filter(buchung__belegdatum__gte=gj.beginn, buchung__belegdatum__lte=gj.ende)
              .values("konto").annotate(s=Sum("soll"), h=Sum("haben")))
    sv = automatikkonto("SALDENVORTRAG")
    zeilen, ergebnis = [], NULL
    for e in salden:
        k = Konto.objects.get(pk=e["konto"])
        s = (e["s"] or NULL) - (e["h"] or NULL)
        if not s or k.pk == sv.pk:
            continue
        if ist_bilanzkonto(k):
            zeilen.append({"konto": k, "soll" if s > 0 else "haben": abs(s), "text": f"Saldenvortrag {jahr}",
                           "partner": k.partner})
        elif k.guv_position:
            ergebnis += s
    if ergebnis:
        zeilen.append({"konto": automatikkonto("GEWINNVORTRAG"), "haben" if ergebnis < 0 else "soll": abs(ergebnis),
                       "text": f"Jahresergebnis {jahr}"})
    differenz = sum((q(z.get("soll")) - q(z.get("haben")) for z in zeilen), NULL)
    if differenz:
        zeilen.append({"konto": sv, "haben" if differenz > 0 else "soll": abs(differenz), "text": "Saldenvortrag"})
    if len(zeilen) < 2:
        raise FibuFehler(f"Im Geschäftsjahr {jahr} gibt es keine Salden vorzutragen.")
    b = buchen(neu.beginn, f"SV{jahr}", f"Saldenvortrag aus Geschäftsjahr {jahr}", zeilen,
               quelle="saldenvortrag", quelle_id=jahr, benutzer=benutzer)
    gj.saldenvortrag_am = timezone.now()
    gj.save(update_fields=["saldenvortrag_am"])
    return b


def gj_abschliessen(jahr, benutzer=None):
    """Alle Perioden sperren (offene Buchungen werden festgeschrieben), Saldenvortrag, GJ abschließen."""
    gj = Geschaeftsjahr.objects.get(jahr=jahr)
    with transaction.atomic():
        sv = saldenvortrag(jahr, benutzer)
        for p in gj.perioden.filter(gesperrt=False):
            periode_sperren(p, benutzer)
        gj.abgeschlossen = True
        gj.save(update_fields=["abgeschlossen"])
    return sv

"""Verkaufsprozess: Lead → Kunde, Preisfindung, Angebot → Auftrag (AB) → Lieferschein (Teillieferungen,
chargengenauer Lagerabgang) → Rechnung/Sammelrechnung → Gutschrift/Storno.

Alle Belege laufen über apps.belege.services (Nummernkreis, Festschreibung, Hash-Kette, PDF-Archiv);
die Fibu bucht festgeschriebene Rechnungen/Gutschriften automatisch (Ereignis „beleg.festgeschrieben“).
Bestände ändern sich ausschließlich über apps.lager.services.buchen().
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from apps.belege import services as beleg_services
from apps.belege.models import Beleg, BelegPosition
from apps.core.events import abonnieren, veroeffentlichen
from apps.lager import services as lager
from apps.lager.models import Bestand, Bestandsart, Lagerort
from apps.stammdaten.models import Adresse, Ansprechpartner, Geschaeftspartner

from .models import (
    NULL, Aktivitaet, Auftrag, AuftragPosition, Kundenpreis, Lead, LieferPosition, Lieferung, Verkaufschance,
    VertriebFehler,
)


def _d(wert):
    return Decimal(str(wert))


def _user(benutzer):
    return benutzer if getattr(benutzer, "pk", None) else None


# ---------------------------------------------------------------- CRM

def lead_umwandeln(lead, benutzer=None):
    """Legt aus dem Lead einen Kunden (Geschäftspartner) an und überträgt Aktivitäten und Chancen."""
    if lead.partner_id:
        return lead.partner
    with transaction.atomic():
        partner = Geschaeftspartner.objects.create(name=lead.firma, ist_kunde=True, email=lead.email,
                                                   telefon=lead.telefon)
        if lead.plz or lead.strasse:
            Adresse.objects.create(partner=partner, typ="HAUPT", strasse=lead.strasse or "-", plz=lead.plz,
                                   ort=lead.ort, land=lead.land or "DE")
        if lead.ansprechpartner:
            teile = lead.ansprechpartner.rsplit(" ", 1)
            Ansprechpartner.objects.create(partner=partner, vorname=teile[0] if len(teile) > 1 else "",
                                           nachname=teile[-1], email=lead.email, telefon=lead.telefon)
        lead.partner = partner
        if lead.status in (Lead.Status.NEU, Lead.Status.QUALIFIZIERT):
            lead.status = Lead.Status.QUALIFIZIERT
        lead.save()
        Aktivitaet.objects.filter(lead=lead, partner=None).update(partner=partner)
        Verkaufschance.objects.filter(lead=lead, partner=None).update(partner=partner)
    veroeffentlichen("vertrieb.lead_umgewandelt", lead=lead, partner=partner, benutzer=benutzer)
    return partner


def faellige_wiedervorlagen(benutzer=None, bis=None):
    qs = Aktivitaet.objects.filter(erledigt=False, wiedervorlage__isnull=False,
                                   wiedervorlage__lte=bis or timezone.localdate())
    if benutzer is not None:
        qs = qs.filter(Q(zustaendig=benutzer) | Q(zustaendig=None))
    return qs.select_related("lead", "partner")


def pipeline():
    """[(Phase-Label, Anzahl, Summe Wert, Summe gewichtet)] für die offenen und abgeschlossenen Phasen."""
    ergebnis = []
    for code, label in Verkaufschance.Phase.choices:
        chancen = list(Verkaufschance.objects.filter(phase=code))
        ergebnis.append({"code": code, "label": label, "anzahl": len(chancen),
                         "wert": sum((c.wert for c in chancen), NULL),
                         "gewichtet": sum((c.gewichteter_wert for c in chancen), NULL)})
    return ergebnis


# ---------------------------------------------------------------- Preisfindung

def preis_finden(partner, artikel, menge=1, datum=None):
    """(Einzelpreis, Rabatt %) – kundenspezifischer Preis/Staffel vor allgemeiner Staffel vor Listenpreis.
    Je Ebene gilt die höchste erreichte Staffelmenge."""
    datum = datum or timezone.localdate()
    qs = Kundenpreis.objects.filter(artikel=artikel, ab_menge__lte=_d(menge)).filter(
        Q(gueltig_ab__isnull=True) | Q(gueltig_ab__lte=datum)).filter(
        Q(gueltig_bis__isnull=True) | Q(gueltig_bis__gte=datum))
    for bedingung in ({"partner": partner} if partner else None, {"partner__isnull": True}):
        if bedingung is None:
            continue
        kp = qs.filter(**bedingung).order_by("-ab_menge").first()
        if kp:
            return (kp.preis if kp.preis is not None else artikel.verkaufspreis), kp.rabatt_prozent
    return artikel.verkaufspreis, NULL


# ---------------------------------------------------------------- Angebot / Auftrag

def angebot_anlegen(partner, positionen, benutzer=None, betreff="", freigeben=False, **felder):
    """positionen: [(Artikel, Menge)] oder [(Artikel, Menge, Preis)] – Preise über die Preisfindung."""
    with transaction.atomic():
        b = Beleg.objects.create(belegart=Beleg.Belegart.ANGEBOT, partner=partner, betreff=betreff,
                                 zahlungsbedingung=partner.zahlungsbedingung, erstellt_von=_user(benutzer), **felder)
        for i, eintrag in enumerate(positionen, start=1):
            artikel, menge = eintrag[0], _d(eintrag[1])
            preis, rabatt = preis_finden(partner, artikel, menge, b.belegdatum)
            if len(eintrag) > 2 and eintrag[2] is not None:
                preis = _d(eintrag[2])
            BelegPosition.objects.create(beleg=b, position=i * 10, artikel=artikel, menge=menge, einzelpreis=preis,
                                         rabatt_prozent=rabatt, steuersatz=artikel.steuersatz.satz)
        b.berechne_summen()
        b.save()
    if freigeben:
        b = beleg_services.freigeben(b, benutzer)
    Lead.objects.filter(partner=partner, status__in=[Lead.Status.NEU, Lead.Status.QUALIFIZIERT]).update(
        status=Lead.Status.ANGEBOT)
    return b


def auftrag_anlegen(partner, positionen, benutzer=None, quelle=Auftrag.Quelle.MANUELL, **felder):
    """positionen: [dict(artikel, menge, einzelpreis?, rabatt_prozent?, liefertermin?, kundenartikelnummer?)]."""
    if not positionen:
        raise VertriebFehler("Ein Auftrag braucht mindestens eine Position.")
    with transaction.atomic():
        a = Auftrag.objects.create(partner=partner, quelle=quelle, zustaendig=_user(benutzer),
                                   zahlungsbedingung=felder.pop("zahlungsbedingung", None) or partner.zahlungsbedingung,
                                   **felder)
        for i, p in enumerate(positionen, start=1):
            if _d(p["menge"]) <= 0:
                raise VertriebFehler("Die Menge muss größer als 0 sein.")
            AuftragPosition.objects.create(auftrag=a, position=i * 10, **p)
    veroeffentlichen("vertrieb.auftrag_angelegt", auftrag=a, benutzer=benutzer)
    return a


def auftrag_aus_angebot(angebot, benutzer=None, ihre_referenz=""):
    if angebot.belegart != Beleg.Belegart.ANGEBOT:
        raise VertriebFehler("Nur Angebote können in einen Auftrag übernommen werden.")
    if angebot.status == Beleg.Status.STORNIERT:
        raise VertriebFehler("Das Angebot ist storniert.")
    if Auftrag.objects.filter(angebot=angebot).exclude(status=Auftrag.Status.STORNIERT).exists():
        raise VertriebFehler(f"{angebot} wurde bereits in einen Auftrag übernommen.")
    if angebot.status == Beleg.Status.ENTWURF:
        angebot = beleg_services.freigeben(angebot, benutzer)
    positionen = [{"artikel": p.artikel, "menge": p.menge, "einzelpreis": p.einzelpreis,
                   "rabatt_prozent": p.rabatt_prozent, "steuersatz": p.steuersatz, "bezeichnung": p.bezeichnung}
                  for p in angebot.positionen.exclude(artikel=None)]
    a = auftrag_anlegen(angebot.partner, positionen, benutzer, quelle=Auftrag.Quelle.ANGEBOT, angebot=angebot,
                        ihre_referenz=ihre_referenz, zahlungsbedingung=angebot.zahlungsbedingung)
    Verkaufschance.objects.filter(angebot=angebot).update(phase=Verkaufschance.Phase.GEWONNEN,
                                                          wahrscheinlichkeit=100)
    Lead.objects.filter(partner=angebot.partner).exclude(status=Lead.Status.VERLOREN).update(
        status=Lead.Status.GEWONNEN)
    return a


def _beleg_aus_positionen(belegart, auftrag, zeilen, benutzer, **felder):
    """zeilen: [(AuftragPosition, Menge, Zusatztext)] → Beleg im Entwurf."""
    b = Beleg.objects.create(belegart=belegart, partner=auftrag.partner, erstellt_von=_user(benutzer),
                             zahlungsbedingung=auftrag.zahlungsbedingung or auftrag.partner.zahlungsbedingung,
                             **felder)
    for i, (pos, menge, zusatz) in enumerate(zeilen, start=1):
        BelegPosition.objects.create(
            beleg=b, position=i * 10, artikel=pos.artikel,
            bezeichnung=(pos.bezeichnung + (f" – {zusatz}" if zusatz else ""))[:300], menge=menge,
            einheit=pos.einheit, einzelpreis=pos.einzelpreis or 0, rabatt_prozent=pos.rabatt_prozent or 0,
            steuersatz=pos.steuersatz)
    b.berechne_summen()
    b.save()
    return b


def auftrag_bestaetigen(auftrag, benutzer=None):
    """Erzeugt die Auftragsbestätigung (Beleg AUFTRAG), gibt sie frei und schreibt sie fest (PDF-Archiv)."""
    with transaction.atomic():
        a = Auftrag.objects.select_for_update().get(pk=auftrag.pk)
        if a.status != Auftrag.Status.ERFASST:
            raise VertriebFehler(f"Auftrag {a.nummer} ist bereits {a.get_status_display().lower()}.")
        if not a.positionen.exists():
            raise VertriebFehler("Ein Auftrag ohne Positionen kann nicht bestätigt werden.")
        zeilen = [(p, p.menge, f"Liefertermin {p.liefertermin:%d.%m.%Y}" if p.liefertermin else "")
                  for p in a.positionen.select_related("artikel")]
        ab = _beleg_aus_positionen(
            Beleg.Belegart.AUFTRAG, a, zeilen, benutzer, ihre_referenz=a.ihre_referenz,
            betreff=f"Auftragsbestätigung zu Ihrer Bestellung {a.ihre_referenz or 'vom ' + a.bestelldatum.strftime('%d.%m.%Y')}",
            kopftext=f"Vielen Dank für Ihren Auftrag. Unsere Auftragsnummer: {a.nummer}.",
            leistungsdatum=a.liefertermin)
        ab = beleg_services.festschreiben(beleg_services.freigeben(ab, benutzer), benutzer)
        a.ab_beleg, a.status = ab, Auftrag.Status.BESTAETIGT
        a.save(update_fields=["ab_beleg", "status"])
    veroeffentlichen("vertrieb.auftrag_bestaetigt", auftrag=a, benutzer=benutzer)
    return a


def auftrag_stornieren(auftrag, benutzer=None, grund=""):
    if not (grund or "").strip():
        raise VertriebFehler("Bitte einen Stornogrund angeben.")
    with transaction.atomic():
        a = Auftrag.objects.select_for_update().get(pk=auftrag.pk)
        if a.lieferungen.exclude(status=Lieferung.Status.STORNIERT).exists():
            raise VertriebFehler("Der Auftrag hat bereits Lieferscheine – bitte zuerst diese stornieren.")
        if a.ab_beleg_id and a.ab_beleg.status != Beleg.Status.STORNIERT:
            beleg_services.stornieren(a.ab_beleg, benutzer, grund)
        a.status = Auftrag.Status.STORNIERT
        a.save(update_fields=["status"])
    return a


def status_aktualisieren(auftrag):
    if auftrag.status in (Auftrag.Status.ERFASST, Auftrag.Status.STORNIERT):
        return auftrag
    pos = list(auftrag.positionen.all())
    geliefert = sum((p.menge_geliefert for p in pos), NULL)
    if all(p.menge_geliefert >= p.menge for p in pos):
        neu = Auftrag.Status.ABGESCHLOSSEN if all(p.menge_berechnet >= p.menge for p in pos) \
            else Auftrag.Status.GELIEFERT
    else:
        neu = Auftrag.Status.TEILGELIEFERT if geliefert > 0 else Auftrag.Status.BESTAETIGT
    if neu != auftrag.status:
        auftrag.status = neu
        auftrag.save(update_fields=["status"])
    return auftrag


# ---------------------------------------------------------------- Verfügbarkeit / Fertigungsbedarf

def verfuegbarkeit(auftrag):
    """Je offener Position: frei verfügbarer Bestand, Bedarf älterer offener Aufträge, Fehlmenge und
    Hinweis auf Fertigungsbedarf (Eigenfertigungsteile)."""
    from apps.produktion.services import ist_eigenfertigung

    ergebnis = []
    for p in auftrag.positionen.select_related("artikel", "fertigungsauftrag"):
        frei = lager.verfuegbar(p.artikel)
        vorher = AuftragPosition.objects.filter(
            artikel=p.artikel, auftrag__status__in=Auftrag.OFFENE_STATI).filter(
            Q(auftrag__bestelldatum__lt=auftrag.bestelldatum) |
            Q(auftrag__bestelldatum=auftrag.bestelldatum, auftrag__pk__lt=auftrag.pk)).exclude(auftrag=auftrag)
        reserviert = sum((x.offen_liefern for x in vorher), NULL)
        disponibel = frei - reserviert
        fehl = max(p.offen_liefern - max(disponibel, NULL), NULL)
        ergebnis.append({"position": p, "frei": frei, "reserviert": reserviert, "disponibel": disponibel,
                         "offen": p.offen_liefern, "fehlmenge": fehl, "ok": fehl <= 0,
                         "eigenfertigung": ist_eigenfertigung(p.artikel), "fa": p.fertigungsauftrag})
    return ergebnis


def fa_aus_position(position, benutzer=None, menge=None):
    """Legt einen Fertigungsauftrag über die Fehlmenge (bzw. `menge`) der Auftragsposition an."""
    from apps.produktion.services import fa_anlegen

    if position.fertigungsauftrag_id:
        raise VertriebFehler(f"Zu Position {position.position} existiert bereits {position.fertigungsauftrag}.")
    if menge is None:
        zeile = next(z for z in verfuegbarkeit(position.auftrag) if z["position"].pk == position.pk)
        menge = zeile["fehlmenge"] or position.offen_liefern
    fa = fa_anlegen(position.artikel, menge, termin=position.liefertermin, benutzer=benutzer,
                    bemerkung=f"Kundenauftrag {position.auftrag.nummer} Pos. {position.position}")
    position.fertigungsauftrag = fa
    position.save(update_fields=["fertigungsauftrag"])
    return fa


# ---------------------------------------------------------------- Lieferschein

def versandlager():
    return Lagerort.objects.filter(code="FL").first() or \
        Lagerort.objects.filter(aktiv=True, ist_wareneingang=False).order_by("code").first()


def lieferung_anlegen(auftrag, mengen=None, benutzer=None, lieferdatum=None, lagerort=None):
    """Legt einen Lieferschein an (Beleg im Entwurf). `mengen` = {AuftragPosition-ID: Menge};
    ohne Angabe werden alle offenen Mengen geliefert (Teillieferung = kleinere Mengen)."""
    with transaction.atomic():
        a = Auftrag.objects.select_for_update().get(pk=auftrag.pk)
        if a.status not in (Auftrag.Status.BESTAETIGT, Auftrag.Status.TEILGELIEFERT):
            raise VertriebFehler(f"Auftrag {a.nummer} ist nicht lieferbar (Status {a.get_status_display()}).")
        # bereits in offenen (ungebuchten) Lieferscheinen eingeplante Mengen berücksichtigen
        geplant = dict(LieferPosition.objects.filter(
            auftrag_position__auftrag=a, lieferung__status__in=Lieferung.OFFEN_FUER_BUCHUNG).values(
            "auftrag_position").annotate(s=Sum("menge")).values_list("auftrag_position", "s"))
        zeilen = []
        for p in a.positionen.all():
            frei = p.offen_liefern - (geplant.get(p.pk) or NULL)
            menge = _d(mengen.get(p.pk, mengen.get(str(p.pk), 0)) or 0) if mengen is not None else frei
            if menge <= 0:
                continue
            if menge > frei:
                raise VertriebFehler(f"Position {p.position}: Liefermenge {menge.normalize():f} größer als offene "
                                     f"Menge {frei.normalize():f}.")
            zeilen.append((p, menge))
        if not zeilen:
            raise VertriebFehler("Keine Liefermengen angegeben bzw. nichts mehr offen.")
        adr = a.lieferadresse
        datum = lieferdatum or timezone.localdate()
        beleg = Beleg.objects.create(belegart=Beleg.Belegart.LIEFERSCHEIN, partner=a.partner,
                                     erstellt_von=_user(benutzer), ihre_referenz=a.ihre_referenz,
                                     leistungsdatum=datum, betreff=f"Lieferschein zu Auftrag {a.nummer}")
        lf = Lieferung.objects.create(auftrag=a, beleg=beleg, lieferdatum=datum,
                                      lagerort=lagerort or versandlager(), name=adr["name"],
                                      strasse=adr["strasse"], plz=adr["plz"], ort=adr["ort"],
                                      land=adr["land"] or "DE")
        for p, menge in zeilen:
            LieferPosition.objects.create(lieferung=lf, auftrag_position=p, menge=menge)
    return lf


def _zuteilen(artikel, lagerort, menge, charge=None):
    """Chargengenaue Entnahme planen: [(Bestandszeile, Menge)] – FIFO nach Herstell-/Anlagedatum der Charge."""
    qs = Bestand.objects.filter(artikel=artikel, lagerort=lagerort, bestandsart=Bestandsart.FREI, menge__gt=0)
    if charge is not None:
        qs = qs.filter(charge=charge)
    rest, plan = menge, []
    for zeile in qs.select_related("charge", "lagerplatz").order_by("charge__herstelldatum", "charge__erstellt_am",
                                                                     "pk"):
        if rest <= 0:
            break
        nimm = min(rest, zeile.menge)
        plan.append((zeile, nimm))
        rest -= nimm
    if rest > 0:
        verfuegbar = menge - rest
        raise VertriebFehler(
            f"Nicht genügend Bestand von {artikel.artikelnummer}"
            f"{' Charge ' + charge.chargennummer if charge else ''} in {lagerort.code}: "
            f"verfügbar {verfuegbar.normalize():f}, benötigt {menge.normalize():f}.")
    return plan


def warenausgang_buchen(lieferung, benutzer=None):
    """Bucht den Lagerabgang (chargengenau, FIFO falls keine Charge vorgegeben), schreibt den Lieferschein
    fest und aktualisiert die Liefermengen des Auftrags. Alles oder nichts (eine Transaktion)."""
    with transaction.atomic():
        lf = Lieferung.objects.select_for_update().get(pk=lieferung.pk)
        if lf.status not in Lieferung.OFFEN_FUER_BUCHUNG:
            raise VertriebFehler(f"{lf} ist bereits gebucht.")
        plaene = []
        for lp in list(lf.positionen.select_related("auftrag_position__artikel", "charge")):
            menge = lp.kommissioniert if lp.kommissioniert is not None else lp.menge
            if menge <= 0:
                lp.delete()
                continue
            ap = AuftragPosition.objects.select_for_update().get(pk=lp.auftrag_position_id)
            if menge > ap.offen_liefern:
                raise VertriebFehler(f"Position {ap.position}: Liefermenge größer als offene Menge.")
            plan = _zuteilen(ap.artikel, lf.lagerort, menge, lp.charge)
            # je Charge eine Lieferscheinposition (Splitting bei FIFO über mehrere Chargen)
            for i, (zeile, teil) in enumerate(plan):
                ziel = lp if i == 0 else LieferPosition(lieferung=lf, auftrag_position=ap, packstueck=lp.packstueck)
                ziel.menge, ziel.kommissioniert, ziel.charge = teil, None, zeile.charge
                ziel.save()
                plaene.append((ziel, zeile, ap))
        if not plaene:
            raise VertriebFehler("Der Lieferschein enthält keine Mengen.")
        # Belegpositionen aus den (ggf. gesplitteten) Lieferpositionen aufbauen, dann Nummer vergeben
        beleg = lf.beleg
        beleg.positionen.all().delete()
        for i, (lp, _, ap) in enumerate(plaene, start=1):
            zusatz = f"Charge {lp.charge.chargennummer}" if lp.charge_id else ""
            BelegPosition.objects.create(
                beleg=beleg, position=i * 10, artikel=ap.artikel,
                bezeichnung=(ap.bezeichnung + (f" – {zusatz}" if zusatz else ""))[:300], menge=lp.menge,
                einheit=ap.einheit, einzelpreis=ap.einzelpreis or 0, rabatt_prozent=ap.rabatt_prozent or 0,
                steuersatz=ap.steuersatz)
        beleg.leistungsdatum = lf.lieferdatum
        beleg.berechne_summen()
        beleg.save()
        beleg = beleg_services.freigeben(beleg, benutzer)
        for lp, zeile, ap in plaene:
            bew = lager.buchen(lager.Art.ABGANG, ap.artikel, lp.menge,
                               von=(lf.lagerort, zeile.lagerplatz, Bestandsart.FREI), charge=zeile.charge,
                               benutzer=benutzer, quelle="lieferschein", quelle_id=lf.pk, beleg_nummer=beleg.nummer,
                               grund=f"Lieferschein {beleg.nummer} / Auftrag {lf.auftrag.nummer}",
                               buchungsdatum=lf.lieferdatum)
            lp.lagerbewegung = bew
            lp.save(update_fields=["lagerbewegung"])
            AuftragPosition.objects.filter(pk=ap.pk).update(menge_geliefert=F("menge_geliefert") + lp.menge)
        lf.beleg = beleg_services.festschreiben(beleg, benutzer)
        lf.status = Lieferung.Status.GEBUCHT
        lf.save(update_fields=["status"])
        status_aktualisieren(lf.auftrag)
    veroeffentlichen("vertrieb.lieferung_gebucht", lieferung=lf, benutzer=benutzer)
    return lf


def lieferung_stornieren(lieferung, benutzer=None, grund=""):
    """Storniert einen Lieferschein: Lagerrückbuchung (Gegenbuchung, gleiche Charge/Platz) + Beleg-Storno."""
    if not (grund or "").strip():
        raise VertriebFehler("Bitte einen Stornogrund angeben.")
    with transaction.atomic():
        lf = Lieferung.objects.select_for_update().get(pk=lieferung.pk)
        if lf.rechnung_id:
            raise VertriebFehler("Der Lieferschein ist bereits berechnet – bitte zuerst die Rechnung stornieren.")
        if hasattr(lf, "tourstopp"):
            raise VertriebFehler("Der Lieferschein ist einer Tour zugeordnet – bitte zuerst aus der Tour entfernen.")
        if lf.status == Lieferung.Status.STORNIERT:
            raise VertriebFehler("Bereits storniert.")
        for lp in lf.positionen.select_related("lagerbewegung", "auftrag_position"):
            if lp.lagerbewegung_id:
                bew = lp.lagerbewegung
                lager.buchen(lager.Art.ZUGANG, bew.artikel, bew.menge,
                             nach=(bew.von_lagerort, bew.von_lagerplatz, Bestandsart.FREI), charge=bew.charge,
                             preis=bew.bewertungspreis, benutzer=benutzer, quelle="lieferschein_storno",
                             quelle_id=lf.pk, beleg_nummer=lf.beleg.nummer or "", grund=f"Storno: {grund}"[:300])
                ap = lp.auftrag_position
                AuftragPosition.objects.filter(pk=ap.pk).update(menge_geliefert=F("menge_geliefert") - lp.menge)
        if lf.beleg.status in (Beleg.Status.FREIGEGEBEN, Beleg.Status.FESTGESCHRIEBEN):
            beleg_services.stornieren(lf.beleg, benutzer, grund)
        lf.status = Lieferung.Status.STORNIERT
        lf.save(update_fields=["status"])
        status_aktualisieren(lf.auftrag)
    return lf


# ---------------------------------------------------------------- Rechnung / Gutschrift

BERECHENBAR = [Lieferung.Status.GEBUCHT, Lieferung.Status.VERLADEN, Lieferung.Status.ZUGESTELLT]


def rechnung_erstellen(lieferungen, benutzer=None, festschreiben=True, belegdatum=None):
    """Rechnung (bei mehreren Lieferscheinen: Sammelrechnung) über gebuchte, noch nicht berechnete
    Lieferscheine eines Kunden. Freigabe + Festschreibung → ZUGFeRD-PDF im Archiv, Fibu-Buchung und OP."""
    lieferungen = list(lieferungen)
    if not lieferungen:
        raise VertriebFehler("Bitte mindestens einen Lieferschein auswählen.")
    with transaction.atomic():
        lfs = list(Lieferung.objects.select_for_update().filter(pk__in=[x.pk for x in lieferungen])
                   .select_related("auftrag", "beleg").order_by("lieferdatum", "pk"))
        partner = {lf.auftrag.partner_id for lf in lfs}
        if len(partner) != 1:
            raise VertriebFehler("Eine Sammelrechnung ist nur für Lieferscheine desselben Kunden möglich.")
        for lf in lfs:
            if lf.rechnung_id:
                raise VertriebFehler(f"{lf} ist bereits berechnet.")
            if lf.status not in BERECHENBAR:
                raise VertriebFehler(f"{lf} ist noch nicht gebucht.")
        mengen = {}
        for lf in lfs:
            for lp in lf.positionen.select_related("auftrag_position__artikel"):
                ap = lp.auftrag_position
                mengen.setdefault(ap.pk, [ap, NULL])[1] += lp.menge
        erster = lfs[0].auftrag
        sammel = len(lfs) > 1
        zeilen = [(ap, menge, f"Auftrag {ap.auftrag.nummer}" if sammel else "") for ap, menge in mengen.values()]
        refs = sorted({lf.auftrag.ihre_referenz for lf in lfs if lf.auftrag.ihre_referenz})
        felder = {"ihre_referenz": ", ".join(refs)[:100],
                  "leistungsdatum": max(lf.lieferdatum for lf in lfs),
                  "betreff": ("Sammelrechnung" if sammel else f"Rechnung zu Auftrag {erster.nummer}"),
                  "kopftext": "Lieferscheine: " + ", ".join(lf.beleg.nummer for lf in lfs)}
        if belegdatum:
            felder["belegdatum"] = belegdatum
        re = _beleg_aus_positionen(Beleg.Belegart.RECHNUNG, erster, zeilen, benutzer, **felder)
        Lieferung.objects.filter(pk__in=[lf.pk for lf in lfs]).update(rechnung=re)
        for ap, menge in mengen.values():
            AuftragPosition.objects.filter(pk=ap.pk).update(menge_berechnet=F("menge_berechnet") + menge)
        re = beleg_services.freigeben(re, benutzer)
        if festschreiben:
            re = beleg_services.festschreiben(re, benutzer)
        for a in {lf.auftrag for lf in lfs}:
            a.refresh_from_db()
            status_aktualisieren(a)
    veroeffentlichen("vertrieb.rechnung_erstellt", rechnung=re, lieferungen=lfs, benutzer=benutzer)
    return re


def gutschrift_erstellen(rechnung, benutzer=None, mengen=None, grund="", festschreiben=True):
    """Gutschrift (Rechnungskorrektur) zu einer festgeschriebenen Rechnung. `mengen` = {BelegPosition-ID: Menge};
    ohne Angabe wird die ganze Rechnung gutgeschrieben. Die Fibu bucht Erlösminderung + negativen OP."""
    if rechnung.belegart != Beleg.Belegart.RECHNUNG or rechnung.status != Beleg.Status.FESTGESCHRIEBEN:
        raise VertriebFehler("Gutschriften sind nur zu festgeschriebenen Rechnungen möglich.")
    with transaction.atomic():
        gs = Beleg.objects.create(belegart=Beleg.Belegart.GUTSCHRIFT, partner=rechnung.partner,
                                  erstellt_von=_user(benutzer), ihre_referenz=rechnung.nummer,
                                  leistungsdatum=rechnung.leistungsdatum,
                                  betreff=f"Gutschrift zu Rechnung {rechnung.nummer}",
                                  kopftext=(grund or "").strip())
        i = 0
        for p in rechnung.positionen.all():
            menge = p.menge if mengen is None else _d(mengen.get(p.pk, mengen.get(str(p.pk), 0)) or 0)
            if menge <= 0:
                continue
            if menge > p.menge:
                raise VertriebFehler(f"Gutschriftsmenge Pos. {p.position} größer als berechnet.")
            i += 1
            BelegPosition.objects.create(beleg=gs, position=i * 10, artikel=p.artikel, bezeichnung=p.bezeichnung,
                                         menge=menge, einheit=p.einheit, einzelpreis=p.einzelpreis,
                                         rabatt_prozent=p.rabatt_prozent, steuersatz=p.steuersatz)
        if not i:
            raise VertriebFehler("Keine Gutschriftspositionen angegeben.")
        gs.berechne_summen()
        gs.save()
        gs = beleg_services.freigeben(gs, benutzer)
        if festschreiben:
            gs = beleg_services.festschreiben(gs, benutzer)
    return gs


@abonnieren("beleg.storniert")
def _rechnung_storniert(beleg, benutzer=None, **kwargs):
    """Storno einer Rechnung gibt die Lieferscheine zur erneuten Berechnung frei."""
    if beleg.belegart != Beleg.Belegart.RECHNUNG:
        return
    for lf in Lieferung.objects.filter(rechnung=beleg).select_related("auftrag"):
        for lp in lf.positionen.select_related("auftrag_position"):
            ap = lp.auftrag_position
            AuftragPosition.objects.filter(pk=ap.pk).update(menge_berechnet=F("menge_berechnet") - lp.menge)
        lf.rechnung = None
        lf.save(update_fields=["rechnung"])
        a = lf.auftrag
        a.refresh_from_db()
        if a.status == Auftrag.Status.ABGESCHLOSSEN:
            a.status = Auftrag.Status.GELIEFERT
            a.save(update_fields=["status"])


# ---------------------------------------------------------------- Kennzahlen

def kennzahlen():
    heute = timezone.localdate()
    monat = heute.replace(day=1)
    umsatz = NULL
    for b in Beleg.objects.filter(belegart__in=["RECHNUNG", "GUTSCHRIFT"], status=Beleg.Status.FESTGESCHRIEBEN,
                                  belegdatum__gte=monat, belegdatum__lte=heute):
        umsatz += b.summe_netto if b.belegart == "RECHNUNG" else -b.summe_netto
    offen = Auftrag.objects.filter(status__in=Auftrag.OFFENE_STATI)
    rueckstand = [p for p in AuftragPosition.objects.filter(
        auftrag__status__in=Auftrag.OFFENE_STATI, liefertermin__lt=heute).select_related("auftrag__partner",
                                                                                         "artikel") if p.rueckstand]
    chancen = Verkaufschance.objects.filter(phase__in=Verkaufschance.OFFENE_PHASEN)
    return {
        "umsatz_monat": umsatz, "offene_auftraege": offen.count(),
        "offener_auftragswert": sum((a.summe_netto for a in offen.prefetch_related("positionen")), NULL),
        "rueckstand": rueckstand, "rueckstand_n": len(rueckstand),
        "pipeline_gewichtet": sum((c.gewichteter_wert for c in chancen), NULL),
        "pipeline_n": chancen.count(),
        "wiedervorlagen": faellige_wiedervorlagen().count(),
        "unberechnet": Lieferung.objects.filter(status__in=BERECHENBAR, rechnung=None).count(),
    }

"""Buchungslogik Wareneingang und Qualitätsprüfung.

Ereignisse (nur Event, keine Fibu-Logik – die spätere Rechnungsprüfung/WE-RE-Verrechnung abonniert sie):
* `wareneingang.gebucht`      – wareneingang, bestellung, wert, benutzer
* `wareneingang.qs_entschieden` – position, entscheid, ruecksendung, benutzer
"""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.events import veroeffentlichen
from apps.core.nummernkreise import naechste_nummer
from apps.einkauf.models import Bestellung, BestellPosition
from apps.einkauf.services import status_fortschreiben
from apps.lager import services as lager
from apps.lager.models import Bestandsart, Lagerbewegung

from .models import Wareneingang, WareneingangFehler, WEPosition

Art = Lagerbewegung.Art
QS = WEPosition.QS


def _d(w):
    return Decimal(str(w)) if w not in (None, "") else None


def buchen(bestellung, positionen, *, lieferschein_nr, lagerort, datum=None, bemerkung="", benutzer=None):
    """Bucht einen Wareneingang.

    `positionen`: Liste von Dicts mit `bestellposition`, `menge` und optional `chargennummer`,
    `lieferantencharge`, `mhd`, `herstelldatum`, `lagerplatz`, `lieferpreis`, `qs_pruefen`,
    `restmenge_abschliessen`. Positionen mit Menge 0/leer werden ignoriert.
    """
    datum = datum or timezone.localdate()
    if not (lieferschein_nr or "").strip():
        raise WareneingangFehler("Bitte die Lieferschein-Nr. des Lieferanten angeben.")
    if lagerort is None:
        raise WareneingangFehler("Bitte einen Lagerort angeben.")
    zeilen = [p for p in positionen if (_d(p.get("menge")) or 0) > 0]
    if not zeilen:
        raise WareneingangFehler("Keine Liefermenge erfasst.")

    with transaction.atomic():
        b = Bestellung.objects.select_for_update().get(pk=bestellung.pk)
        if not b.ist_offen:
            raise WareneingangFehler(f"{b} ist {b.get_status_display().lower()} – kein Wareneingang möglich.")
        we = Wareneingang.objects.create(
            nummer=naechste_nummer("WARENEINGANG", datum), bestellung=b, lieferant=b.lieferant,
            lieferschein_nr=lieferschein_nr.strip(), datum=datum, lagerort=lagerort, bemerkung=bemerkung,
            gebucht_von=benutzer if getattr(benutzer, "pk", None) else None)
        protokoll = []
        for z in zeilen:
            bp = BestellPosition.objects.select_for_update().select_related("artikel", "artikel__einheit").get(
                pk=getattr(z["bestellposition"], "pk", z["bestellposition"]))
            if bp.bestellung_id != b.pk:
                raise WareneingangFehler(f"Position {bp} gehört nicht zu {b}.")
            artikel, menge = bp.artikel, _d(z["menge"])
            if artikel.chargenpflicht and not (z.get("chargennummer") or "").strip():
                raise WareneingangFehler(f"Pos. {bp.position} ({artikel.artikelnummer}) ist chargenpflichtig – "
                                         f"bitte Chargennummer erfassen.")
            offen = bp.offen_menge
            diff = menge - offen
            hinweise = []
            if diff > 0:
                hinweise.append(f"Überlieferung +{diff.normalize():f} {bp.einheit} (offen {offen.normalize():f})")
            elif diff < 0:
                rest = "Restmenge abgeschlossen" if z.get("restmenge_abschliessen") else "Restmenge bleibt offen"
                hinweise.append(f"Unterlieferung {diff.normalize():f} {bp.einheit} – {rest}")
            bestellpreis = bp.nettopreis
            lieferpreis = _d(z.get("lieferpreis"))
            preisdiff = (lieferpreis - bestellpreis) if lieferpreis is not None else Decimal(0)
            if preisdiff:
                hinweise.append(f"Preisdifferenz {preisdiff:+.4f} €/{bp.einheit} (Bestellung {bestellpreis:.4f}, "
                                f"Lieferschein {lieferpreis:.4f})")
            ld = lager.lagerdaten(artikel)
            qs_noetig = bool(ld.qs_pflicht or z.get("qs_pruefen"))
            charge = lager.charge_holen(artikel, z.get("chargennummer"), anlegen=True, lieferant=b.lieferant,
                                        lieferantencharge=z.get("lieferantencharge") or "",
                                        mhd=z.get("mhd"), herstelldatum=z.get("herstelldatum"))
            platz = z.get("lagerplatz")
            pos = WEPosition.objects.create(
                wareneingang=we, bestellposition=bp, artikel=artikel, menge=menge, einheit=bp.einheit,
                lagerplatz=platz, charge=charge, offen_vorher=offen, mengendifferenz=diff,
                bestellpreis=bestellpreis, lieferpreis=lieferpreis, preisdifferenz=preisdiff,
                hinweis="; ".join(hinweise)[:300], qs_erforderlich=qs_noetig,
                qs_status=QS.OFFEN if qs_noetig else QS.FREIGEGEBEN,
                qs_bemerkung="" if qs_noetig else "Keine Prüfung erforderlich")
            pos.lagerbewegung = lager.buchen(
                Art.ZUGANG, artikel, menge,
                nach=(lagerort, platz, Bestandsart.GESPERRT if qs_noetig else Bestandsart.FREI),
                charge=charge, preis=lieferpreis if lieferpreis is not None else bestellpreis, benutzer=benutzer,
                quelle="wareneingang", quelle_id=we.pk, beleg_nummer=we.nummer, buchungsdatum=datum,
                grund=f"WE {we.nummer} zu {b.nummer}, LS {we.lieferschein_nr}")
            pos.save(update_fields=["lagerbewegung"])
            bp.geliefert_menge += menge
            if diff < 0 and z.get("restmenge_abschliessen"):
                bp.erledigt = True
            bp.save(update_fields=["geliefert_menge", "erledigt"])
            if hinweise:
                protokoll.append(f"Pos. {bp.position} {artikel.artikelnummer}: " + "; ".join(hinweise))
        we.hinweise = "\n".join(protokoll)
        we.save(update_fields=["hinweise"])
        status_fortschreiben(b)
        wert = sum((p.lagerbewegung.wert for p in we.positionen.select_related("lagerbewegung")), Decimal(0))
        transaction.on_commit(lambda: veroeffentlichen("wareneingang.gebucht", wareneingang=we, bestellung=b,
                                                       wert=wert, benutzer=benutzer))
    return we


def qs_entscheiden(position, entscheid, *, bemerkung="", ruecksenden=False, benutzer=None):
    """QS-Entscheid für eine Wareneingangsposition.

    * FREIGEGEBEN: Umbuchung Sperrbestand → frei verfügbar (ohne Werteffekt)
    * GESPERRT:    bleibt im Sperrbestand (reklamiert); mit `ruecksenden` Abgang aus dem Sperrbestand und
                   Reduktion der gelieferten Menge in der Bestellung (Ersatzlieferung wird wieder offen).
    """
    if entscheid not in (QS.FREIGEGEBEN, QS.GESPERRT):
        raise WareneingangFehler("Unbekannter Prüfentscheid.")
    with transaction.atomic():
        pos = WEPosition.objects.select_for_update(of=("self",)).select_related(
            "wareneingang", "artikel", "charge", "lagerplatz", "bestellposition").get(pk=position.pk)
        we = pos.wareneingang
        if pos.zuruckgesendet:
            raise WareneingangFehler("Die Ware wurde bereits an den Lieferanten zurückgesendet.")
        if pos.qs_status == QS.FREIGEGEBEN:
            raise WareneingangFehler("Die Position ist bereits freigegeben.")
        if entscheid == QS.GESPERRT and not bemerkung.strip():
            raise WareneingangFehler("Bitte den Reklamationsgrund angeben.")
        ort = (we.lagerort, pos.lagerplatz, Bestandsart.GESPERRT)
        if entscheid == QS.FREIGEGEBEN:
            lager.buchen(Art.UMBUCHUNG, pos.artikel, pos.menge, von=ort,
                         nach=(we.lagerort, pos.lagerplatz, Bestandsart.FREI), charge=pos.charge, benutzer=benutzer,
                         quelle="qs", quelle_id=pos.pk, beleg_nummer=we.nummer,
                         grund=f"QS-Freigabe {we.nummer}" + (f": {bemerkung}" if bemerkung else ""))
        elif ruecksenden:
            lager.buchen(Art.ABGANG, pos.artikel, pos.menge, von=ort, charge=pos.charge, benutzer=benutzer,
                         quelle="qs", quelle_id=pos.pk, beleg_nummer=we.nummer,
                         grund=f"Rücksendung an Lieferant (Reklamation {we.nummer}): {bemerkung}")
            bp = BestellPosition.objects.select_for_update().get(pk=pos.bestellposition_id)
            bp.geliefert_menge -= pos.menge
            bp.erledigt = False
            bp.save(update_fields=["geliefert_menge", "erledigt"])
            pos.zuruckgesendet = True
            status_fortschreiben(bp.bestellung)
        pos.qs_status, pos.qs_bemerkung = entscheid, bemerkung.strip()[:300]
        pos.qs_von, pos.qs_am = (benutzer if getattr(benutzer, "pk", None) else None), timezone.now()
        pos.save()
        transaction.on_commit(lambda: veroeffentlichen("wareneingang.qs_entschieden", position=pos,
                                                       entscheid=entscheid, ruecksendung=ruecksenden,
                                                       benutzer=benutzer))
    return pos


def offene_bestellungen():
    return (Bestellung.objects.filter(typ=Bestellung.Typ.BESTELLUNG, status__in=Bestellung.OFFENE_STATI)
            .select_related("lieferant").order_by("liefertermin", "pk"))


def offene_pruefungen():
    return (WEPosition.objects.filter(qs_status=QS.OFFEN)
            .select_related("wareneingang", "artikel", "artikel__einheit", "charge", "wareneingang__lieferant"))

"""Geschäftslogik der Fertigung.

Ablauf: Auftrag anlegen → freigeben (Material/Arbeitsgänge kopieren, Fehlteile prüfen) → Material entnehmen
(Lagerabgang) → stempeln (Start/Unterbrechung/Ende) und rückmelden (Gut/Ausschuss/Nacharbeit) → fertigmelden
(Lagerzugang zu Herstellkosten) → Nachkalkulation Soll/Ist. Akkordauswertung siehe `akkord_auswertung`.

Zeitakkord (REFA):
* Vorgabeminuten   = Gutmenge × te (+ Rüstzeit, gutgeschrieben dem Mitarbeiter, der gerüstet hat)
* Leistungsgrad    = Vorgabeminuten / Ist-Minuten (Akkordarbeitsgänge) × 100 %
* Minutenfaktor    = Akkordrichtsatz / 60
* Akkordverdienst  = Vorgabeminuten × Minutenfaktor
* Garantie         = Ist-Stunden Akkord × max(Grundlohn, gesetzlicher Mindestlohn)
* Akkordlohn       = max(Akkordverdienst, Garantie); Zeitlohn-Anteile = Ist-Stunden × max(Stundenlohn, Mindestlohn)
"""
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Avg, Sum
from django.utils import timezone

from apps.core.events import veroeffentlichen
from apps.core.nummernkreise import naechste_nummer
from apps.lager import services as lager
from apps.lager.models import Bestand, Bestandsart, Charge, Lagerbewegung, LagerFehler, Lagerort
from apps.stammdaten.models import Artikel, Lohnart, Mitarbeiter

from .models import (
    Anwesenheit, FAArbeitsgang, FAMaterial, Fertigmeldung, Fertigungsauftrag, Materialentnahme,
    ProduktionFehler, Rueckmeldung, Stempelung,
)

NULL, DREI, ZWEI, VIER = Decimal(0), Decimal("0.001"), Decimal("0.01"), Decimal("0.0001")
S = Fertigungsauftrag.Status
FERTIGBARE_ARTEN = {Artikel.Art.FERTIGARTIKEL, Artikel.Art.HALBZEUG, Artikel.Art.WERKZEUG}


def _d(wert):
    return Decimal(str(wert or 0))


def _geld(wert):
    return Decimal(wert).quantize(ZWEI, ROUND_HALF_UP)


def _sperren(fa):
    return Fertigungsauftrag.objects.select_for_update().get(pk=fa.pk)


def _offen_pruefen(fa):
    if fa.status not in Fertigungsauftrag.OFFENE_STATI:
        raise ProduktionFehler(f"{fa.nummer} ist {fa.get_status_display().lower()} – keine Buchung möglich.")


# ---------------------------------------------------------------- Aufträge

def aktiver_arbeitsplan(artikel):
    return artikel.arbeitsplaene.filter(aktiv=True).order_by("-version").first()


def fa_anlegen(artikel, menge, *, termin=None, starttermin=None, stueckliste=None, arbeitsplan=None,
               ziel_lagerort=None, bemerkung="", uebergeordnet=None, benutzer=None):
    menge = _d(menge)
    if menge <= 0:
        raise ProduktionFehler("Die Auftragsmenge muss größer als 0 sein.")
    if artikel.art not in FERTIGBARE_ARTEN:
        raise ProduktionFehler(f"{artikel.artikelnummer} ist kein Eigenfertigungsteil "
                               f"(nur Fertigartikel, Halbzeuge, Werkzeuge).")
    stueckliste = stueckliste or artikel.aktive_stueckliste
    arbeitsplan = arbeitsplan or aktiver_arbeitsplan(artikel)
    if stueckliste is None and arbeitsplan is None:
        raise ProduktionFehler(f"Für {artikel.artikelnummer} gibt es weder Stückliste noch Arbeitsplan.")
    for obj, name in ((stueckliste, "Stückliste"), (arbeitsplan, "Arbeitsplan")):
        if obj is not None and obj.artikel_id != artikel.pk:
            raise ProduktionFehler(f"{name} {obj} gehört nicht zu {artikel.artikelnummer}.")
    if ziel_lagerort is None:
        ziel_lagerort = lager.lagerdaten(artikel).standard_lagerort or \
            Lagerort.objects.filter(aktiv=True, ist_wareneingang=False).order_by("code").first()
    with transaction.atomic():
        return Fertigungsauftrag.objects.create(
            nummer=naechste_nummer("FERTIGUNGSAUFTRAG"), artikel=artikel, stueckliste=stueckliste,
            arbeitsplan=arbeitsplan, menge=menge, termin=termin, starttermin=starttermin,
            ziel_lagerort=ziel_lagerort, bemerkung=bemerkung, uebergeordnet=uebergeordnet,
            erstellt_von=benutzer if getattr(benutzer, "pk", None) else None)


def materialbedarf(fa):
    """Bedarf der ersten Stücklistenebene [(Artikel, Menge)] – bei freigegebenen Aufträgen aus dem Auftrag."""
    if fa.status != S.ENTWURF:
        return [(m.artikel, m.soll_menge) for m in fa.materialien.select_related("artikel")]
    if not fa.stueckliste:
        return []
    faktor = fa.menge / (fa.stueckliste.basismenge or 1)
    summen = {}
    for pos in fa.stueckliste.positionen.select_related("komponente").order_by("position"):
        summen[pos.komponente] = summen.get(pos.komponente, NULL) + (pos.menge * faktor).quantize(DREI)
    return list(summen.items())


def ist_eigenfertigung(artikel):
    return artikel.aktive_stueckliste is not None and aktiver_arbeitsplan(artikel) is not None


def fehlteile(fa, nur_fehlend=False):
    """Verfügbarkeitsprüfung gegen den frei verfügbaren Lagerbestand."""
    zeilen = []
    materialien = {m.artikel_id: m for m in fa.materialien.all()} if fa.status != S.ENTWURF else {}
    for artikel, soll in materialbedarf(fa):
        m = materialien.get(artikel.pk)
        offen = m.offen_menge if m else soll
        frei = lager.verfuegbar(artikel)
        fehl = max(offen - frei, NULL)
        if nur_fehlend and fehl <= 0:
            continue
        zeilen.append({"artikel": artikel, "material": m, "bedarf": soll, "offen": offen, "frei": frei,
                       "fehlmenge": fehl, "eigenfertigung": ist_eigenfertigung(artikel),
                       "unterauftrag": m.unterauftrag if m else None})
    return zeilen


@transaction.atomic
def freigeben(fa, benutzer=None, unterauftraege=False):
    """Kopiert Material (1. Ebene) und Arbeitsgänge. Optional: Unteraufträge für fehlende Halbzeuge
    (Eigenfertigungsteile mit eigener Stückliste und Arbeitsplan) als Entwurf anlegen."""
    fa = _sperren(fa)
    if fa.status != S.ENTWURF:
        raise ProduktionFehler(f"{fa.nummer} ist bereits {fa.get_status_display().lower()}.")
    bedarf = materialbedarf(fa)
    if not bedarf and not fa.arbeitsplan:
        raise ProduktionFehler("Auftrag ohne Material und ohne Arbeitsgänge kann nicht freigegeben werden.")
    for i, (artikel, menge) in enumerate(bedarf, start=1):
        FAMaterial.objects.create(auftrag=fa, position=i * 10, artikel=artikel, soll_menge=menge)
    if fa.arbeitsplan:
        for ag in fa.arbeitsplan.arbeitsgaenge.order_by("position"):
            FAArbeitsgang.objects.create(
                auftrag=fa, position=ag.position, bezeichnung=ag.bezeichnung, arbeitsplatz=ag.arbeitsplatz,
                ruestzeit_min=ag.ruestzeit_min, stueckzeit_min=ag.stueckzeit_min, lohngruppe=ag.lohngruppe,
                lohnart=ag.lohnart)
    fa.status, fa.freigegeben_am = S.FREIGEGEBEN, timezone.now()
    fa.freigegeben_von = benutzer if getattr(benutzer, "pk", None) else None
    fa.save()
    angelegt = []
    if unterauftraege:
        for z in fehlteile(fa, nur_fehlend=True):
            if z["eigenfertigung"]:
                termin = fa.starttermin or fa.termin
                unter = fa_anlegen(z["artikel"], z["fehlmenge"], termin=termin, uebergeordnet=fa, benutzer=benutzer,
                                   bemerkung=f"Unterauftrag für {fa.nummer}")
                z["material"].unterauftrag = unter
                z["material"].save(update_fields=["unterauftrag"])
                angelegt.append(unter)
    veroeffentlichen("produktion.freigegeben", auftrag=fa, benutzer=benutzer)
    return fa, angelegt


@transaction.atomic
def stornieren(fa, benutzer=None, grund=""):
    fa = _sperren(fa)
    if fa.status in (S.ABGESCHLOSSEN, S.STORNIERT):
        raise ProduktionFehler(f"{fa.nummer} ist bereits {fa.get_status_display().lower()}.")
    if not (grund or "").strip():
        raise ProduktionFehler("Bitte einen Stornogrund angeben.")
    if fa.entnahmen.exists() or fa.fertigmeldungen.exists():
        raise ProduktionFehler("Es gibt bereits Materialentnahmen oder Fertigmeldungen – Storno nicht möglich, "
                               "bitte den Auftrag abschließen bzw. Material zurückbuchen.")
    if Stempelung.objects.filter(arbeitsgang__auftrag=fa, ende__isnull=True).exists():
        raise ProduktionFehler("Am Auftrag ist noch jemand angestempelt.")
    fa.status, fa.storno_grund = S.STORNIERT, grund.strip()[:300]
    fa.save()
    return fa


# ---------------------------------------------------------------- Material

def entnahme_bestaende(artikel):
    """Freie Bestandszeilen eines Artikels (für die Chargen-/Platzwahl), FIFO nach Charge."""
    return Bestand.objects.filter(artikel=artikel, bestandsart=Bestandsart.FREI, menge__gt=0).select_related(
        "lagerort", "lagerplatz", "charge").order_by("charge__erstellt_am", "lagerort__code", "pk")


@transaction.atomic
def material_entnehmen(fa, material, menge, *, bestand=None, benutzer=None):
    """Lagerabgang zum Auftrag. Chargenpflichtige Artikel brauchen eine gewählte Bestandszeile (= Charge);
    sonst wird automatisch aus den freien Beständen (FIFO) entnommen."""
    fa = _sperren(fa)
    _offen_pruefen(fa)
    menge = _d(menge)
    if menge <= 0:
        raise ProduktionFehler("Die Entnahmemenge muss größer als 0 sein.")
    if material.auftrag_id != fa.pk:
        raise ProduktionFehler("Material gehört nicht zu diesem Auftrag.")
    artikel = material.artikel
    if bestand is not None:
        if bestand.artikel_id != artikel.pk or bestand.bestandsart != Bestandsart.FREI:
            raise ProduktionFehler("Die gewählte Bestandszeile passt nicht (Artikel/frei verfügbar).")
        quellen = [(bestand, menge)]
    else:
        if artikel.chargenpflicht:
            raise ProduktionFehler(f"{artikel.artikelnummer} ist chargenpflichtig – bitte die Charge wählen.")
        quellen, rest = [], menge
        for b in entnahme_bestaende(artikel):
            teil = min(b.menge, rest)
            quellen.append((b, teil))
            rest -= teil
            if rest <= 0:
                break
        if rest > 0:
            raise ProduktionFehler(f"Nicht genügend freier Bestand von {artikel.artikelnummer}: "
                                   f"es fehlen {rest.normalize():f} {artikel.einheit.code}.")
    entnahmen = []
    try:
        for b, teil in quellen:
            bew = lager.buchen(Lagerbewegung.Art.ABGANG, artikel, teil,
                               von=(b.lagerort, b.lagerplatz, Bestandsart.FREI), charge=b.charge, benutzer=benutzer,
                               quelle="produktion", quelle_id=fa.pk, beleg_nummer=fa.nummer,
                               grund=f"Materialentnahme {fa.nummer}")
            entnahmen.append(Materialentnahme.objects.create(
                auftrag=fa, material=material, artikel=artikel, charge=b.charge, lagerort=b.lagerort,
                lagerplatz=b.lagerplatz, menge=teil, wert=abs(bew.wert), bewegung=bew,
                benutzer=benutzer if getattr(benutzer, "pk", None) else None))
    except LagerFehler as exc:
        raise ProduktionFehler(str(exc)) from exc
    material.entnommen_menge += menge
    material.save(update_fields=["entnommen_menge"])
    if fa.status == S.FREIGEGEBEN:
        fa.status = S.IN_ARBEIT
        fa.save(update_fields=["status"])
    wert = sum((e.wert for e in entnahmen), NULL)
    veroeffentlichen("produktion.material_entnommen", auftrag=fa, entnahmen=entnahmen, wert=wert, benutzer=benutzer)
    return entnahmen


def restmaterial_entnehmen(fa, benutzer=None):
    """Entnimmt die offene Menge aller nicht chargenpflichtigen Materialien. Liefert (erledigt, übersprungen)."""
    erledigt, uebersprungen = [], []
    for m in fa.materialien.select_related("artikel", "artikel__einheit"):
        if m.offen_menge <= 0:
            continue
        if m.artikel.chargenpflicht:
            uebersprungen.append((m, "chargenpflichtig – Charge wählen"))
            continue
        try:
            material_entnehmen(fa, m, m.offen_menge, benutzer=benutzer)
            erledigt.append(m)
        except ProduktionFehler as exc:
            uebersprungen.append((m, str(exc)))
    return erledigt, uebersprungen


# ---------------------------------------------------------------- BDE / Stempeln

def mitarbeiter_finden(kennung):
    kennung = (kennung or "").strip()
    if not kennung:
        raise ProduktionFehler("Bitte Personal- oder Ausweisnummer eingeben.")
    ma = (Mitarbeiter.objects.filter(personalnummer__iexact=kennung, aktiv=True).first()
          or Mitarbeiter.objects.filter(ausweisnummer__iexact=kennung, aktiv=True).first())
    if not ma:
        raise ProduktionFehler(f"Mitarbeiter „{kennung}“ unbekannt oder inaktiv.")
    return ma


def arbeitsgang_finden(code):
    """Stempelcode <FA-Nummer>-<AG-Nr.>, z. B. FA-2026-00001-10."""
    code = (code or "").strip().upper()
    nummer, _, pos = code.rpartition("-")
    if not nummer or not pos.isdigit():
        raise ProduktionFehler(f"„{code}“ ist kein gültiger Arbeitsgang-Code (Format FA-JJJJ-NNNNN-AG).")
    ag = FAArbeitsgang.objects.select_related("auftrag", "arbeitsplatz").filter(
        auftrag__nummer=nummer, position=int(pos)).first()
    if not ag:
        raise ProduktionFehler(f"Arbeitsgang {code} nicht gefunden.")
    return ag


def offene_stempelung(ma):
    return Stempelung.objects.select_related("arbeitsgang__auftrag").filter(mitarbeiter=ma, ende__isnull=True).first()


def offene_anwesenheit(ma):
    return Anwesenheit.objects.filter(mitarbeiter=ma, gehen__isnull=True).first()


def _ma_sperren(ma):
    return Mitarbeiter.objects.select_for_update().get(pk=ma.pk)


@transaction.atomic
def kommen(ma, zeit=None, bemerkung=""):
    _ma_sperren(ma)
    zeit = zeit or timezone.now()
    offen = offene_anwesenheit(ma)
    if offen:
        raise ProduktionFehler(f"{ma.name} ist bereits seit {timezone.localtime(offen.kommen):%d.%m. %H:%M} da.")
    return Anwesenheit.objects.create(mitarbeiter=ma, kommen=zeit, bemerkung=bemerkung)


@transaction.atomic
def gehen(ma, zeit=None):
    _ma_sperren(ma)
    zeit = zeit or timezone.now()
    st = offene_stempelung(ma)
    if st:
        _beenden(st, Stempelung.EndeArt.UNTERBRECHUNG, zeit)
    offen = offene_anwesenheit(ma)
    if not offen:
        raise ProduktionFehler(f"{ma.name} ist nicht angemeldet (kein „Kommen“).")
    offen.gehen = max(zeit, offen.kommen)
    offen.save(update_fields=["gehen"])
    return offen, st


@transaction.atomic
def stempel_start(ma, ag, taetigkeit=Stempelung.Taetigkeit.FERTIGEN, zeit=None, quelle=Stempelung.Quelle.TERMINAL):
    _ma_sperren(ma)
    zeit = zeit or timezone.now()
    fa = _sperren(ag.auftrag)
    _offen_pruefen(fa)
    st = offene_stempelung(ma)
    if st:
        raise ProduktionFehler(f"{ma.name} ist noch an {st.arbeitsgang.code} angestempelt – bitte zuerst "
                               f"unterbrechen oder beenden (keine parallelen Auftragszeiten).")
    if Stempelung.objects.filter(mitarbeiter=ma, ende__gt=zeit).exists():
        raise ProduktionFehler("Die Startzeit überschneidet sich mit einer vorhandenen Stempelung.")
    if not offene_anwesenheit(ma):
        Anwesenheit.objects.create(mitarbeiter=ma, kommen=zeit, bemerkung="automatisch beim Anstempeln")
    try:
        with transaction.atomic():
            st = Stempelung.objects.create(mitarbeiter=ma, arbeitsgang=ag, taetigkeit=taetigkeit, beginn=zeit,
                                           quelle=quelle)
    except IntegrityError as exc:
        raise ProduktionFehler(f"{ma.name} ist bereits angestempelt.") from exc
    ag.status = FAArbeitsgang.Status.LAEUFT
    ag.save(update_fields=["status"])
    if fa.status == S.FREIGEGEBEN:
        fa.status = S.IN_ARBEIT
        fa.save(update_fields=["status"])
    return st


def _beenden(st, art, zeit):
    st.ende, st.ende_art = max(zeit, st.beginn), art
    st.save(update_fields=["ende", "ende_art"])
    ag = st.arbeitsgang
    if not ag.stempelungen.filter(ende__isnull=True).exists():
        ag.status = FAArbeitsgang.Status.ERLEDIGT if art == Stempelung.EndeArt.ENDE else \
            FAArbeitsgang.Status.UNTERBROCHEN
        ag.save(update_fields=["status"])
    return st


@transaction.atomic
def stempel_beenden(ma, art=Stempelung.EndeArt.ENDE, ag=None, zeit=None, gut=0, ausschuss=0, grund="",
                    nacharbeit=0, benutzer=None):
    """Unterbrechung oder Ende der laufenden Stempelung, optional mit Rückmeldung der Mengen."""
    _ma_sperren(ma)
    st = offene_stempelung(ma)
    if not st:
        raise ProduktionFehler(f"{ma.name} ist an keinem Arbeitsgang angestempelt.")
    if ag is not None and st.arbeitsgang_id != ag.pk:
        raise ProduktionFehler(f"{ma.name} ist an {st.arbeitsgang.code} angestempelt, nicht an {ag.code}.")
    _beenden(st, art, zeit or timezone.now())
    rm = None
    if _d(gut) or _d(ausschuss) or _d(nacharbeit):
        rm = rueckmelden(st.arbeitsgang, gut, ausschuss, grund, nacharbeit, mitarbeiter=ma, stempelung=st,
                         benutzer=benutzer, zeitpunkt=st.ende)
    return st, rm


AKTIONEN = {"kommen", "gehen", "ruesten", "start", "unterbrechung", "ende", "auto"}


def terminal_buchen(kennung, code="", aktion="auto", gut=0, ausschuss=0, grund="", nacharbeit=0,
                    quelle=Stempelung.Quelle.TERMINAL, benutzer=None, zeit=None):
    """Zentrale Terminal-Funktion (Web-Terminal und REST-API). Liefert (Mitarbeiter, Meldungstext)."""
    ma = mitarbeiter_finden(kennung)
    code = (code or "").strip()
    if code.upper() in ("KOMMEN", "GEHEN"):
        aktion, code = code.lower(), ""
    aktion = (aktion or "auto").lower()
    if aktion not in AKTIONEN:
        raise ProduktionFehler(f"Unbekannte Aktion „{aktion}“.")
    ag = arbeitsgang_finden(code) if code else None
    if aktion == "auto":
        laufend = offene_stempelung(ma)
        if not ag:
            if offene_anwesenheit(ma):
                raise ProduktionFehler("Bitte Arbeitsgang scannen oder eine Aktion (z. B. Gehen) wählen.")
            aktion = "kommen"
        elif laufend and laufend.arbeitsgang_id == ag.pk:
            raise ProduktionFehler(f"Bereits an {ag.code} angestempelt – bitte „Unterbrechung“ oder „Ende“ wählen.")
        else:
            aktion = "start"
    if aktion == "kommen":
        a = kommen(ma, zeit)
        return ma, f"Willkommen, {ma.name} – Kommen {timezone.localtime(a.kommen):%H:%M} Uhr."
    if aktion == "gehen":
        a, st = gehen(ma, zeit)
        zusatz = f" (Arbeitsgang {st.arbeitsgang.code} unterbrochen)" if st else ""
        return ma, f"Auf Wiedersehen, {ma.name} – Gehen {timezone.localtime(a.gehen):%H:%M} Uhr{zusatz}."
    if aktion in ("start", "ruesten"):
        if not ag:
            raise ProduktionFehler("Bitte den Arbeitsgang-Code scannen.")
        taet = Stempelung.Taetigkeit.RUESTEN if aktion == "ruesten" else Stempelung.Taetigkeit.FERTIGEN
        st = stempel_start(ma, ag, taet, zeit=zeit, quelle=quelle)
        return ma, f"{ma.name}: {st.get_taetigkeit_display()} {ag.code} „{ag.bezeichnung}“ gestartet."
    art = Stempelung.EndeArt.ENDE if aktion == "ende" else Stempelung.EndeArt.UNTERBRECHUNG
    st, rm = stempel_beenden(ma, art, ag=ag, zeit=zeit, gut=gut, ausschuss=ausschuss, grund=grund,
                             nacharbeit=nacharbeit, benutzer=benutzer)
    text = f"{ma.name}: {st.arbeitsgang.code} {st.get_ende_art_display().lower()} nach {st.dauer_min:.0f} min"
    if rm:
        text += f" – gemeldet {rm.gut_menge.normalize():f} gut, {rm.ausschuss_menge.normalize():f} Ausschuss"
    return ma, text + "."


def _ueberschneidung_pruefen(ma, beginn, ende, ausser=None):
    qs = Stempelung.objects.filter(mitarbeiter=ma)
    if ausser is not None:
        qs = qs.exclude(pk=ausser.pk)
    if ende is not None:
        qs = qs.filter(beginn__lt=ende)
    for s in qs.select_related("arbeitsgang__auftrag"):
        if s.ende is None or s.ende > beginn:
            raise ProduktionFehler(f"Überschneidung mit Stempelung {s.arbeitsgang.code} "
                                   f"({timezone.localtime(s.beginn):%d.%m. %H:%M}).")


@transaction.atomic
def stempelung_korrigieren(st, *, beginn, ende, grund, benutzer=None, arbeitsgang=None, taetigkeit=None):
    """Korrektur nur mit Recht bde/ändern (Prüfung in der View/API). Vorher/Nachher landet im Audit-Log."""
    if not (grund or "").strip():
        raise ProduktionFehler("Bitte einen Korrekturgrund angeben.")
    if ende is not None and ende < beginn:
        raise ProduktionFehler("Das Ende liegt vor dem Beginn.")
    _ma_sperren(st.mitarbeiter)
    _ueberschneidung_pruefen(st.mitarbeiter, beginn, ende, ausser=st)
    st.beginn, st.ende = beginn, ende
    if ende is not None and not st.ende_art:
        st.ende_art = Stempelung.EndeArt.UNTERBRECHUNG
    if ende is None:
        st.ende_art = ""
    if arbeitsgang is not None:
        st.arbeitsgang = arbeitsgang
    if taetigkeit:
        st.taetigkeit = taetigkeit
    wer = getattr(benutzer, "username", "System")
    eintrag = f"{timezone.localtime():%d.%m.%Y %H:%M} {wer}: {grund.strip()}"
    st.korrigiert = True
    st.korrektur_grund = (f"{st.korrektur_grund} | {eintrag}" if st.korrektur_grund else eintrag)[-300:]
    try:
        st.save()
    except IntegrityError as exc:
        raise ProduktionFehler("Je Mitarbeiter ist nur eine offene Stempelung zulässig.") from exc
    return st


@transaction.atomic
def stempelung_nachtragen(ma, ag, *, beginn, ende, grund, taetigkeit=Stempelung.Taetigkeit.FERTIGEN, benutzer=None):
    if not (grund or "").strip():
        raise ProduktionFehler("Bitte einen Grund für den Nachtrag angeben.")
    if ende is None or ende <= beginn:
        raise ProduktionFehler("Für einen Nachtrag sind Beginn und ein späteres Ende erforderlich.")
    _ma_sperren(ma)
    _ueberschneidung_pruefen(ma, beginn, ende)
    wer = getattr(benutzer, "username", "System")
    return Stempelung.objects.create(
        mitarbeiter=ma, arbeitsgang=ag, taetigkeit=taetigkeit, beginn=beginn, ende=ende,
        ende_art=Stempelung.EndeArt.UNTERBRECHUNG, quelle=Stempelung.Quelle.MANUELL, korrigiert=True,
        korrektur_grund=f"{timezone.localtime():%d.%m.%Y %H:%M} {wer}: Nachtrag – {grund.strip()}"[:300])


# ---------------------------------------------------------------- Rückmeldung

@transaction.atomic
def rueckmelden(ag, gut=0, ausschuss=0, grund="", nacharbeit=0, *, mitarbeiter=None, stempelung=None,
                benutzer=None, bemerkung="", zeitpunkt=None):
    gut, ausschuss, nacharbeit = _d(gut), _d(ausschuss), _d(nacharbeit)
    if min(gut, ausschuss, nacharbeit) < 0:
        raise ProduktionFehler("Mengen dürfen nicht negativ sein.")
    if gut + ausschuss + nacharbeit <= 0:
        raise ProduktionFehler("Bitte Gut-, Ausschuss- oder Nacharbeitsmenge angeben.")
    if ausschuss > 0 and not grund:
        raise ProduktionFehler("Bei Ausschuss ist ein Ausschussgrund anzugeben.")
    if grund and grund not in Rueckmeldung.Grund.values:
        raise ProduktionFehler(f"Unbekannter Ausschussgrund „{grund}“.")
    fa = _sperren(ag.auftrag)
    _offen_pruefen(fa)
    ag = FAArbeitsgang.objects.select_for_update().get(pk=ag.pk)
    rm = Rueckmeldung.objects.create(
        arbeitsgang=ag, mitarbeiter=mitarbeiter, stempelung=stempelung, gut_menge=gut, ausschuss_menge=ausschuss,
        ausschuss_grund=grund if ausschuss > 0 else "", nacharbeit_menge=nacharbeit, bemerkung=bemerkung[:300],
        zeitpunkt=zeitpunkt or timezone.now(), erfasst_von=benutzer if getattr(benutzer, "pk", None) else None)
    ag.gut_menge += gut
    ag.ausschuss_menge += ausschuss
    ag.nacharbeit_menge += nacharbeit
    ag.save(update_fields=["gut_menge", "ausschuss_menge", "nacharbeit_menge"])
    fa.ausschuss_menge = FAArbeitsgang.objects.filter(auftrag=fa).aggregate(s=Sum("ausschuss_menge"))["s"] or NULL
    if fa.status in (S.FREIGEGEBEN, S.IN_ARBEIT):
        fa.status = S.TEILGEFERTIGT
    fa.save(update_fields=["ausschuss_menge", "status"])
    veroeffentlichen("produktion.rueckmeldung", rueckmeldung=rm)
    return rm


# ---------------------------------------------------------------- Kosten / Kalkulation

def _preis(artikel):
    ld = lager.lagerdaten(artikel)
    return ld.durchschnittspreis or artikel.einkaufspreis or NULL


def lohnsatz_ist(st):
    ma, ag = st.mitarbeiter, st.arbeitsgang
    satz = ma.akkordrichtsatz if ag.lohnart == Lohnart.AKKORD else ma.stundenlohn
    return satz or ma.stundenlohn or settings.ERP_MINDESTLOHN


def lohnsatz_soll(ag):
    feld = "akkordrichtsatz" if ag.lohnart == Lohnart.AKKORD else "stundenlohn"
    qs = Mitarbeiter.objects.filter(aktiv=True, **{f"{feld}__gt": 0})
    if ag.lohngruppe:
        qs = qs.filter(lohngruppe=ag.lohngruppe)
    wert = qs.aggregate(s=Avg(feld))["s"]
    return Decimal(wert).quantize(ZWEI) if wert else settings.ERP_STANDARD_LOHNSATZ


def ist_kosten(fa):
    """Ist-Kosten: Material = Entnahmewerte; Lohn = Ist-Zeit × Lohnsatz (Akkord: Richtsatz, sonst Stundenlohn);
    Arbeitsplatz = Ist-Zeit × Platzkostensatz. Nur abgeschlossene Stempelungen."""
    material = fa.entnahmen.aggregate(s=Sum("wert"))["s"] or NULL
    lohn = platz = NULL
    for st in Stempelung.objects.filter(arbeitsgang__auftrag=fa, ende__isnull=False).select_related(
            "mitarbeiter", "arbeitsgang__arbeitsplatz"):
        stunden = st.dauer_min / 60
        lohn += stunden * lohnsatz_ist(st)
        if st.arbeitsgang.arbeitsplatz:
            platz += stunden * st.arbeitsgang.arbeitsplatz.stundensatz
    return {"material": _geld(material), "lohn": _geld(lohn), "platz": _geld(platz),
            "summe": _geld(material + lohn + platz)}


def soll_kosten(fa, menge=None):
    menge = fa.menge if menge is None else _d(menge)
    faktor = menge / fa.menge if fa.menge else NULL
    material = sum((m * faktor * _preis(a) for a, m in materialbedarf(fa)), NULL)
    lohn = platz = NULL
    if fa.status == S.ENTWURF and fa.arbeitsplan:
        gaenge = list(fa.arbeitsplan.arbeitsgaenge.select_related("arbeitsplatz"))
    else:
        gaenge = list(fa.arbeitsgaenge.select_related("arbeitsplatz"))
    for ag in gaenge:
        stunden = (ag.ruestzeit_min + ag.stueckzeit_min * menge) / 60 if menge else NULL
        lohn += stunden * lohnsatz_soll(ag)
        if ag.arbeitsplatz:
            platz += stunden * ag.arbeitsplatz.stundensatz
    return {"material": _geld(material), "lohn": _geld(lohn), "platz": _geld(platz),
            "summe": _geld(material + lohn + platz)}


@transaction.atomic
def fertigmelden(fa, menge, *, lagerort=None, lagerplatz=None, chargennummer="", abschliessen=False, benutzer=None):
    """Lagerzugang zu Herstellkosten: (Ist-Kosten bis jetzt − bereits aktivierte Kosten) / Menge; bei einer
    Teilmeldung anteilig (Menge / offene Auftragsmenge), bei der Schlussmeldung der gesamte Rest. Liegen noch keine Ist-Kosten vor, wird mit den Soll-Herstellkosten bewertet."""
    fa = _sperren(fa)
    _offen_pruefen(fa)
    menge = _d(menge)
    if menge <= 0:
        raise ProduktionFehler("Die Fertigmenge muss größer als 0 sein.")
    if abschliessen and Stempelung.objects.filter(arbeitsgang__auftrag=fa, ende__isnull=True).exists():
        raise ProduktionFehler("Am Auftrag ist noch jemand angestempelt – Abschluss erst nach dem Abstempeln.")
    lagerort = lagerort or fa.ziel_lagerort
    if lagerort is None:
        raise ProduktionFehler("Bitte einen Ziel-Lagerort angeben.")
    ist = ist_kosten(fa)
    bisher = fa.fertigmeldungen.aggregate(m=Sum("materialkosten"), l=Sum("lohnkosten"), p=Sum("platzkosten"))
    anteil = {k: max(ist[k] - (bisher[b] or NULL), NULL) for k, b in (("material", "m"), ("lohn", "l"),
                                                                      ("platz", "p"))}
    rest_menge = fa.menge - fa.gut_menge
    if not abschliessen and menge < rest_menge:
        # Teilmeldung: noch nicht aktivierte Kosten gleichmäßig auf die offene Auftragsmenge verteilen
        anteil = {k: _geld(v * menge / rest_menge) for k, v in anteil.items()}
    if sum(anteil.values()) <= 0:
        soll = soll_kosten(fa, menge)
        anteil = {k: soll[k] for k in ("material", "lohn", "platz")}
    hk = (sum(anteil.values()) / menge).quantize(VIER, ROUND_HALF_UP)
    nummer = (chargennummer or "").strip() or naechste_nummer("FERTIGUNGSCHARGE")
    charge = lager.charge_holen(fa.artikel, nummer, anlegen=True, herstelldatum=timezone.localdate(),
                                bemerkung=f"Fertigung {fa.nummer}")
    try:
        bew = lager.buchen(Lagerbewegung.Art.ZUGANG, fa.artikel, menge, nach=(lagerort, lagerplatz, Bestandsart.FREI),
                           charge=charge, preis=hk, benutzer=benutzer, quelle="produktion", quelle_id=fa.pk,
                           beleg_nummer=fa.nummer, grund=f"Fertigmeldung {fa.nummer}")
    except LagerFehler as exc:
        raise ProduktionFehler(str(exc)) from exc
    fm = Fertigmeldung.objects.create(
        auftrag=fa, menge=menge, charge=charge, lagerort=lagerort, lagerplatz=lagerplatz,
        materialkosten=anteil["material"], lohnkosten=anteil["lohn"], platzkosten=anteil["platz"],
        herstellkosten_stueck=hk, bewegung=bew, benutzer=benutzer if getattr(benutzer, "pk", None) else None)
    fa.gut_menge += menge
    if abschliessen or fa.gut_menge >= fa.menge:
        fa.status, fa.abgeschlossen_am = S.ABGESCHLOSSEN, timezone.now()
        fa.arbeitsgaenge.exclude(status=FAArbeitsgang.Status.ERLEDIGT).update(status=FAArbeitsgang.Status.ERLEDIGT)
    else:
        fa.status = S.TEILGEFERTIGT
    fa.save()
    veroeffentlichen("produktion.fertiggemeldet", auftrag=fa, fertigmeldung=fm, wert=_geld(fm.herstellkosten),
                     benutzer=benutzer)
    return fm


def nachkalkulation(fa):
    gut = fa.gut_menge
    soll_plan = soll_kosten(fa)
    soll_gut = soll_kosten(fa, gut) if gut else {k: NULL for k in soll_plan}
    ist = ist_kosten(fa)
    # Vergleichsbasis: abgeschlossene Aufträge gegen Soll der Gutmenge, offene gegen Soll der Auftragsmenge
    abgeschlossen = fa.status == S.ABGESCHLOSSEN
    basis = soll_gut if abgeschlossen else soll_plan
    zeilen = []
    for key, name in (("material", "Material"), ("lohn", "Fertigungslohn"), ("platz", "Arbeitsplatzkosten"),
                      ("summe", "Herstellkosten gesamt")):
        abw = ist[key] - basis[key]
        zeilen.append({"name": name, "soll_plan": soll_plan[key], "soll_gut": soll_gut[key], "ist": ist[key],
                       "abweichung": abw,
                       "prozent": (abw / basis[key] * 100).quantize(Decimal("0.1")) if basis[key] else None})
    materialien = []
    for m in fa.materialien.select_related("artikel", "artikel__einheit"):
        ist_wert = m.entnahmen.aggregate(s=Sum("wert"))["s"] or NULL
        materialien.append({"m": m, "soll_wert": _geld(m.soll_menge * _preis(m.artikel)), "ist_wert": ist_wert})
    ist_min = defaultdict(lambda: NULL)
    for st in Stempelung.objects.filter(arbeitsgang__auftrag=fa, ende__isnull=False):
        ist_min[st.arbeitsgang_id] += st.dauer_min
    gaenge = []
    for ag in fa.arbeitsgaenge.select_related("arbeitsplatz"):
        vorgabe_gut = (ag.ruestzeit_min + ag.stueckzeit_min * ag.gut_menge) if ag.gut_menge else NULL
        lg = (vorgabe_gut / ist_min[ag.pk] * 100).quantize(Decimal("0.1")) if ist_min[ag.pk] else None
        gaenge.append({"ag": ag, "vorgabe_plan": ag.vorgabe_min(), "vorgabe_gut": vorgabe_gut, "ist": ist_min[ag.pk],
                       "leistungsgrad": lg})
    return {
        "zeilen": zeilen, "materialien": materialien, "gaenge": gaenge,
        "basis": "Gutmenge" if abgeschlossen else "Auftragsmenge (Auftrag noch offen)",
        "hk_soll": (soll_plan["summe"] / fa.menge).quantize(VIER) if fa.menge else NULL,
        "hk_ist": (ist["summe"] / gut).quantize(VIER) if gut else None,
        "aktiviert": sum((f.herstellkosten for f in fa.fertigmeldungen.all()), NULL),
    }


# ---------------------------------------------------------------- Akkordlohn

def _zeitraum(von, bis):
    tz = timezone.get_current_timezone()
    return (timezone.make_aware(datetime.combine(von, time.min), tz),
            timezone.make_aware(datetime.combine(bis + timedelta(days=1), time.min), tz))


def akkord_auswertung(von, bis, mitarbeiter=None, auftrag=None):
    """Zeitakkord je Mitarbeiter im Zeitraum [von, bis] (Datum der Stempelung/Rückmeldung).
    Liefert {"zeilen": [...], "summe": {...}, "mindestlohn": ...}."""
    anfang, ende = _zeitraum(von, bis)
    mindestlohn = settings.ERP_MINDESTLOHN
    st_qs = Stempelung.objects.filter(beginn__gte=anfang, beginn__lt=ende, ende__isnull=False).select_related(
        "mitarbeiter", "arbeitsgang__auftrag__artikel")
    rm_qs = Rueckmeldung.objects.filter(zeitpunkt__gte=anfang, zeitpunkt__lt=ende, mitarbeiter__isnull=False)
    if mitarbeiter:
        st_qs, rm_qs = st_qs.filter(mitarbeiter=mitarbeiter), rm_qs.filter(mitarbeiter=mitarbeiter)
    if auftrag:
        st_qs, rm_qs = st_qs.filter(arbeitsgang__auftrag=auftrag), rm_qs.filter(arbeitsgang__auftrag=auftrag)

    daten = defaultdict(lambda: defaultdict(lambda: {"ist": NULL, "gut": NULL, "ruest": NULL}))
    ag_obj = {}
    for st in st_qs:
        daten[st.mitarbeiter_id][st.arbeitsgang_id]["ist"] += st.dauer_min
        ag_obj[st.arbeitsgang_id] = st.arbeitsgang
    for rm in rm_qs.select_related("arbeitsgang__auftrag__artikel"):
        daten[rm.mitarbeiter_id][rm.arbeitsgang_id]["gut"] += rm.gut_menge
        ag_obj[rm.arbeitsgang_id] = rm.arbeitsgang
    # Rüstzeit-Vorgabe: einmal je Arbeitsgang für den Mitarbeiter mit der ersten Rüst-Stempelung
    ruest_qs = Stempelung.objects.filter(taetigkeit=Stempelung.Taetigkeit.RUESTEN, ende__isnull=False).order_by(
        "arbeitsgang_id", "beginn")
    if auftrag:
        ruest_qs = ruest_qs.filter(arbeitsgang__auftrag=auftrag)
    gesehen = set()
    for st in ruest_qs.select_related("arbeitsgang__auftrag__artikel"):
        if st.arbeitsgang_id in gesehen:
            continue
        gesehen.add(st.arbeitsgang_id)
        if anfang <= st.beginn < ende and (not mitarbeiter or st.mitarbeiter_id == mitarbeiter.pk):
            daten[st.mitarbeiter_id][st.arbeitsgang_id]["ruest"] = st.arbeitsgang.ruestzeit_min
            ag_obj[st.arbeitsgang_id] = st.arbeitsgang

    anwesend = defaultdict(lambda: NULL)
    for a in Anwesenheit.objects.filter(kommen__gte=anfang, kommen__lt=ende, gehen__isnull=False):
        anwesend[a.mitarbeiter_id] += a.dauer_min

    mitarbeiter_map = Mitarbeiter.objects.in_bulk(list(daten.keys()))
    zeilen = []
    for ma_id, gaenge in daten.items():
        ma = mitarbeiter_map[ma_id]
        positionen = []
        ist_akk = ist_zeit = vorgabe = NULL
        for ag_id, w in gaenge.items():
            ag = ag_obj[ag_id]
            if ag.lohnart == Lohnart.AKKORD:
                vg = w["gut"] * ag.stueckzeit_min + w["ruest"]
                ist_akk += w["ist"]
                vorgabe += vg
            else:
                vg = None
                ist_zeit += w["ist"]
            positionen.append({
                "ag": ag, "akkord": ag.lohnart == Lohnart.AKKORD, "ist_min": w["ist"], "gut": w["gut"],
                "ruest_min": w["ruest"], "vorgabe_min": vg,
                "leistungsgrad": (vg / w["ist"] * 100).quantize(Decimal("0.1")) if vg is not None and w["ist"] else None,
            })
        positionen.sort(key=lambda p: (p["ag"].auftrag.nummer, p["ag"].position))
        garantiesatz = max(ma.stundenlohn, mindestlohn)
        verdienst = _geld(vorgabe * ma.akkordrichtsatz / 60)
        garantie = _geld(ist_akk / 60 * garantiesatz)
        ausgleich = max(garantie - verdienst, NULL)
        akkordlohn = verdienst + ausgleich
        zeitlohn = _geld(ist_zeit / 60 * garantiesatz)
        gesamt = akkordlohn + zeitlohn
        ist_gesamt = ist_akk + ist_zeit
        zeilen.append({
            "mitarbeiter": ma, "positionen": positionen, "ist_akkord_min": ist_akk, "ist_zeit_min": ist_zeit,
            "vorgabe_min": vorgabe.quantize(ZWEI),
            "leistungsgrad": (vorgabe / ist_akk * 100).quantize(Decimal("0.1")) if ist_akk else None,
            "minutenfaktor": ma.minutenfaktor, "akkordverdienst": verdienst, "garantiesatz": garantiesatz,
            "garantie": garantie, "ausgleich": ausgleich, "akkordlohn": akkordlohn, "zeitlohn": zeitlohn,
            "gesamt": gesamt, "mindestlohn_angehoben": ma.stundenlohn < mindestlohn,
            "stundenverdienst": _geld(gesamt / (ist_gesamt / 60)) if ist_gesamt else None,
            "anwesend_min": anwesend[ma_id], "nicht_zugeordnet_min": max(anwesend[ma_id] - ist_gesamt, NULL),
        })
    zeilen.sort(key=lambda z: z["mitarbeiter"].personalnummer)
    summe = {k: sum((z[k] for z in zeilen), NULL) for k in (
        "ist_akkord_min", "ist_zeit_min", "vorgabe_min", "akkordverdienst", "ausgleich", "akkordlohn", "zeitlohn",
        "gesamt", "anwesend_min")}
    summe["leistungsgrad"] = (summe["vorgabe_min"] / summe["ist_akkord_min"] * 100).quantize(Decimal("0.1")) \
        if summe["ist_akkord_min"] else None
    return {"zeilen": zeilen, "summe": summe, "mindestlohn": mindestlohn, "von": von, "bis": bis}


def akkord_csv(auswertung):
    """CSV für die Lohnbuchhaltung (Semikolon, Dezimalkomma, UTF-8 mit BOM)."""
    import csv
    import io

    def z(wert):
        return "" if wert is None else f"{wert:.2f}".replace(".", ",")

    puffer = io.StringIO()
    puffer.write("\ufeff")
    w = csv.writer(puffer, delimiter=";")
    w.writerow(["Personalnr.", "Name", "Lohngruppe", "Von", "Bis", "Ist Akkord (min)", "Vorgabe (min)",
                "Leistungsgrad %", "Akkordrichtsatz €/h", "Minutenfaktor €", "Akkordverdienst €", "Garantie €",
                "Ausgleich €", "Akkordlohn €", "Ist Zeitlohn (min)", "Zeitlohn €", "Gesamt €", "Anwesenheit (min)"])
    for r in auswertung["zeilen"]:
        ma = r["mitarbeiter"]
        w.writerow([ma.personalnummer, ma.name, ma.lohngruppe, auswertung["von"].strftime("%d.%m.%Y"),
                    auswertung["bis"].strftime("%d.%m.%Y"), z(r["ist_akkord_min"]), z(r["vorgabe_min"]),
                    z(r["leistungsgrad"]), z(ma.akkordrichtsatz), f"{r['minutenfaktor']:.4f}".replace(".", ","),
                    z(r["akkordverdienst"]), z(r["garantie"]), z(r["ausgleich"]), z(r["akkordlohn"]),
                    z(r["ist_zeit_min"]), z(r["zeitlohn"]), z(r["gesamt"]), z(r["anwesend_min"])])
    return puffer.getvalue()


# ---------------------------------------------------------------- Dashboard

def laufende_stempelungen():
    return Stempelung.objects.filter(ende__isnull=True).select_related(
        "mitarbeiter", "arbeitsgang__auftrag__artikel", "arbeitsgang__arbeitsplatz").order_by("beginn")


def anwesende():
    return Anwesenheit.objects.filter(gehen__isnull=True).select_related("mitarbeiter").order_by("kommen")


def leistungsgrade_arbeitsgaenge(tage=14):
    """Leistungsgrad je Arbeitsgang (auftragsbezogen, ohne Personenbezug) der letzten Tage."""
    seit = timezone.now() - timedelta(days=tage)
    ergebnis = []
    ag_ids = Stempelung.objects.filter(beginn__gte=seit, ende__isnull=False,
                                       arbeitsgang__lohnart=Lohnart.AKKORD).values_list("arbeitsgang", flat=True)
    for ag in FAArbeitsgang.objects.filter(pk__in=set(ag_ids)).select_related("auftrag__artikel"):
        ist = sum((s.dauer_min for s in ag.stempelungen.filter(ende__isnull=False)), NULL)
        if ist and ag.gut_menge:
            vg = ag.ruestzeit_min + ag.stueckzeit_min * ag.gut_menge
            ergebnis.append({"ag": ag, "ist": ist, "vorgabe": vg, "leistungsgrad": (vg / ist * 100).quantize(Decimal("0.1"))})
    return sorted(ergebnis, key=lambda e: (e["ag"].auftrag.nummer, e["ag"].position))


def chargen_fuer_fertigung(fa):
    return Charge.objects.filter(artikel=fa.artikel, bemerkung=f"Fertigung {fa.nummer}")

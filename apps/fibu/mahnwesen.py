"""Mahnwesen: Vorschlagslauf, Verzugszinsen (§ 288 BGB), Ausführung, Mahn-PDF.

* Stufe 1 wird fällig, wenn der OP mindestens `tage` Tage überfällig ist; jede weitere Stufe, wenn seit der
  letzten Mahnung mindestens `tage` Tage vergangen sind.
* Verzugszinsen (nur Stufen mit „Zinsen“): offener Betrag × (Basiszinssatz + Aufschlag) × Verzugstage / 365.
  Aufschlag: 9 Prozentpunkte bei Geschäftskunden (§ 288 Abs. 2 BGB), 5 bei Verbrauchern (Abs. 1).
  Geschäftskunde = Partner mit USt-IdNr. oder Steuernummer bzw. Firmenname (Standard im Industriebetrieb).
* Mahnsperre am Partner oder OP schließt den Posten aus. Gutschriften (negative OPs) werden nicht gemahnt.
* Mahngebühren und Zinsen werden im Schreiben ausgewiesen, aber nicht automatisch gebucht
  (Ertrag erst bei Zahlungseingang, z. B. per Bankzuordnung auf das Mahngebührenkonto).
"""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.template.loader import get_template
from django.utils import timezone

from apps.core.models import Mandant
from apps.core.nummernkreise import naechste_nummer

from .models import NULL, Basiszinssatz, Mahnlauf, MahnPosition, Mahnstufe, Mahnung, OffenerPosten
from .services import FibuFehler, q


def basiszinssatz(stichtag):
    b = Basiszinssatz.objects.filter(gueltig_ab__lte=stichtag).order_by("-gueltig_ab").first()
    if not b:
        raise FibuFehler(f"Kein Basiszinssatz für den {stichtag:%d.%m.%Y} hinterlegt.")
    return b.satz


def verzugszinssatz(stichtag, b2b=True):
    aufschlag = getattr(settings, "ERP_VERZUGSZINS_AUFSCHLAG_B2B" if b2b else "ERP_VERZUGSZINS_AUFSCHLAG_B2C",
                        9 if b2b else 5)
    return basiszinssatz(stichtag) + Decimal(str(aufschlag))


def verzugszinsen(betrag, tage, zinssatz):
    if tage <= 0 or betrag <= 0:
        return NULL
    return q(Decimal(betrag) * Decimal(zinssatz) / 100 * tage / 365)


def ist_geschaeftskunde(partner):
    return True  # Industriebetrieb: Kunden sind Unternehmer (§ 14 BGB); bei Bedarf hier differenzieren


def faellige_posten(stichtag):
    """OPs mit der jeweils nächsten Mahnstufe → [(op, stufe)]."""
    stufen = {s.stufe: s for s in Mahnstufe.objects.all()}
    if not stufen:
        raise FibuFehler("Es sind keine Mahnstufen angelegt.")
    hoechste = max(stufen)
    ergebnis = []
    qs = (OffenerPosten.objects.filter(art="D", offen__gt=0, mahnsperre=False, partner__mahnsperre=False,
                                       faellig_am__lt=stichtag).select_related("partner"))
    for op in qs:
        naechste = min(op.mahnstufe + 1, hoechste)
        stufe = stufen.get(naechste)
        if not stufe:
            continue
        if op.mahnstufe == 0:
            if op.tage_ueberfaellig(stichtag) < stufe.tage:
                continue
        elif not op.letzte_mahnung or (stichtag - op.letzte_mahnung).days < stufe.tage:
            continue
        ergebnis.append((op, stufe))
    return ergebnis


def vorschlag(stichtag=None, benutzer=None):
    """Mahnvorschlag erzeugen: je Partner eine Mahnung mit der höchsten fälligen Stufe."""
    stichtag = stichtag or timezone.localdate()
    posten = faellige_posten(stichtag)
    if not posten:
        raise FibuFehler("Zum Stichtag gibt es keine mahnfähigen offenen Posten.")
    je_partner = {}
    for op, stufe in posten:
        je_partner.setdefault(op.partner, []).append((op, stufe))
    with transaction.atomic():
        lauf = Mahnlauf.objects.create(nummer=naechste_nummer("FIBU_MAHNLAUF", timezone.localdate()),
                                       stichtag=stichtag, erstellt_von=benutzer if getattr(benutzer, "pk", None)
                                       else None)
        for partner, liste in je_partner.items():
            stufe = max((s for _, s in liste), key=lambda s: s.stufe)
            satz = verzugszinssatz(stichtag, ist_geschaeftskunde(partner)) if stufe.zinsen else NULL
            m = Mahnung.objects.create(mahnlauf=lauf, partner=partner, stufe=stufe, gebuehr=stufe.gebuehr,
                                       frist=stichtag + timedelta(days=stufe.frist_tage), zinssatz=satz)
            summe = zinsen = NULL
            for op, s in liste:
                tage = op.tage_ueberfaellig(stichtag)
                z = verzugszinsen(op.offen, tage, satz) if stufe.zinsen else NULL
                MahnPosition.objects.create(mahnung=m, op=op, stufe=s.stufe, offen=op.offen, tage_verzug=tage,
                                            zinsen=z)
                summe += op.offen
                zinsen += z
            m.summe_offen, m.zinsen = summe, zinsen
            m.save(update_fields=["summe_offen", "zinsen"])
    return lauf


def ausfuehren(lauf, benutzer=None):
    """Vorschlag ausführen: Mahnstufe und -datum an den OPs fortschreiben (Mahnhistorie)."""
    if lauf.status != Mahnlauf.Status.VORSCHLAG:
        raise FibuFehler("Der Mahnlauf wurde bereits ausgeführt.")
    with transaction.atomic():
        for pos in MahnPosition.objects.filter(mahnung__mahnlauf=lauf).select_related("op"):
            op = pos.op
            op.mahnstufe = max(op.mahnstufe, pos.stufe)
            op.letzte_mahnung = lauf.stichtag
            op.save(update_fields=["mahnstufe", "letzte_mahnung"])
        lauf.status, lauf.ausgefuehrt_am = Mahnlauf.Status.AUSGEFUEHRT, timezone.now()
        lauf.save(update_fields=["status", "ausgefuehrt_am"])
    return lauf


def mahnung_html(mahnung):
    adresse = mahnung.partner.rechnungsadresse
    return get_template("fibu/pdf/mahnung.html").render({
        "mahnung": mahnung, "lauf": mahnung.mahnlauf, "partner": mahnung.partner, "adresse": adresse,
        "positionen": list(mahnung.positionen.select_related("op").all()), "mandant": Mandant.aktuell(),
        "basiszins": basiszinssatz(mahnung.mahnlauf.stichtag) if mahnung.zinssatz else None,
        "entwurf": mahnung.mahnlauf.status == Mahnlauf.Status.VORSCHLAG, "jetzt": timezone.localtime()})


def mahnung_pdf(mahnung):
    from weasyprint import HTML

    return HTML(string=mahnung_html(mahnung), base_url=str(settings.BASE_DIR)).write_pdf()

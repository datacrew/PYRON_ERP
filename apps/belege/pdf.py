"""PDF-Erzeugung über die Django-Template-Engine + WeasyPrint.

Vorlagensuche: aktive DB-Vorlage der Belegart → aktive DB-Standardvorlage (Belegart leer)
→ Datei templates/belege/pdf/standard.html.
"""
from django.conf import settings
from django.template import Context, Template
from django.template.loader import get_template

from apps.core.models import Mandant

from .models import BelegVorlage


def vorlage_finden(belegart):
    return (BelegVorlage.objects.filter(aktiv=True, belegart=belegart).order_by("-geaendert_am").first()
            or BelegVorlage.objects.filter(aktiv=True, belegart="").order_by("-geaendert_am").first())


def beleg_kontext(beleg):
    from apps.belege.services import _partner_snapshot

    partner = beleg.partner_daten or _partner_snapshot(beleg.partner)
    return {
        "beleg": beleg,
        "positionen": list(beleg.positionen.select_related("artikel").order_by("position", "pk")),
        "partner": partner,
        "mandant": Mandant.aktuell(),
        "steuer_aufstellung": beleg.steuer_aufstellung().items(),
        "titel": beleg.get_belegart_display(),
        "entwurf": beleg.status in (beleg.Status.ENTWURF, beleg.Status.FREIGEGEBEN),
        "storniert": beleg.status == beleg.Status.STORNIERT,
    }


def html_rendern(beleg, vorlage=None):
    kontext = beleg_kontext(beleg)
    vorlage = vorlage or vorlage_finden(beleg.belegart)
    if vorlage:
        return Template(vorlage.html).render(Context(kontext))
    return get_template("belege/pdf/standard.html").render(kontext)


def beleg_pdf(beleg, vorlage=None):
    from weasyprint import HTML

    return HTML(string=html_rendern(beleg, vorlage), base_url=str(settings.BASE_DIR)).write_pdf()

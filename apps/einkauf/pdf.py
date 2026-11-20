"""Bestell-/Anfrage-PDF über die Django-Template-Engine + WeasyPrint (Vorlage templates/einkauf/pdf/bestellung.html)."""
from django.conf import settings
from django.template.loader import get_template

from apps.belege.services import _partner_snapshot
from apps.core.models import Mandant

VORLAGE = "einkauf/pdf/bestellung.html"


def html_rendern(bestellung):
    return get_template(VORLAGE).render({
        "b": bestellung,
        "partner": bestellung.partner_daten or _partner_snapshot(bestellung.lieferant),
        "positionen": list(bestellung.positionen.select_related("artikel").order_by("position", "pk")),
        "steuer_aufstellung": bestellung.steuer_aufstellung().items(),
        "mandant": Mandant.aktuell(),
        "titel": bestellung.get_typ_display(),
        "entwurf": bestellung.status == bestellung.Status.ENTWURF,
        "storniert": bestellung.status == bestellung.Status.STORNIERT,
    })


def bestellung_pdf(bestellung):
    from weasyprint import HTML

    return HTML(string=html_rendern(bestellung), base_url=str(settings.BASE_DIR)).write_pdf()

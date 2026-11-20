"""Wareneingangsbeleg-PDF (Vorlage templates/wareneingang/pdf/wareneingang.html)."""
from django.conf import settings
from django.template.loader import get_template

from apps.core.models import Mandant


def wareneingang_pdf(we):
    from weasyprint import HTML

    html = get_template("wareneingang/pdf/wareneingang.html").render({
        "we": we, "mandant": Mandant.aktuell(),
        "positionen": list(we.positionen.select_related("artikel", "charge", "lagerplatz", "bestellposition",
                                                        "lagerbewegung")),
    })
    return HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf()

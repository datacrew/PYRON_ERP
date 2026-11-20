"""PDF-Ausgaben der Produktion (Django-Templates + WeasyPrint): Laufkarte mit Barcodes, Akkord-Lohnübersicht."""
import base64
import io

from django.conf import settings
from django.template.loader import get_template
from django.utils import timezone

from apps.core.models import Mandant

from . import services


def barcode_datauri(code):
    """Code-128-Barcode als SVG-data-URI (ohne Klartext – der wird im Template groß darunter gedruckt)."""
    import barcode
    from barcode.writer import SVGWriter

    puffer = io.BytesIO()
    barcode.get("code128", code, writer=SVGWriter()).write(
        puffer, options={"write_text": False, "module_height": 9.0, "module_width": 0.25, "quiet_zone": 2.0})
    return "data:image/svg+xml;base64," + base64.b64encode(puffer.getvalue()).decode()


def _pdf(vorlage, kontext):
    from weasyprint import HTML

    html = get_template(vorlage).render({**kontext, "mandant": Mandant.aktuell(), "jetzt": timezone.localtime()})
    return HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf()


def laufkarte_pdf(fa):
    gaenge = [{"ag": ag, "barcode": barcode_datauri(ag.code), "vorgabe": ag.vorgabe_min()}
              for ag in fa.arbeitsgaenge.select_related("arbeitsplatz").order_by("position")]
    if fa.status == fa.Status.ENTWURF and fa.arbeitsplan:
        # Vorschau vor der Freigabe: Arbeitsgänge aus dem Arbeitsplan (ohne Stempel-Barcode)
        gaenge = [{"ag": ag, "barcode": None, "vorgabe": ag.ruestzeit_min + ag.stueckzeit_min * fa.menge}
                  for ag in fa.arbeitsplan.arbeitsgaenge.select_related("arbeitsplatz").order_by("position")]
    return _pdf("produktion/pdf/laufkarte.html", {
        "fa": fa, "barcode": barcode_datauri(fa.nummer), "gaenge": gaenge, "material": services.fehlteile(fa),
        "entwurf": fa.status == fa.Status.ENTWURF, "storniert": fa.status == fa.Status.STORNIERT,
    })


def akkord_pdf(auswertung):
    return _pdf("produktion/pdf/lohnuebersicht.html", {"a": auswertung})

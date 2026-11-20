"""Formatierung und Export (Excel, CSV, PDF) für Berichte."""
import csv
import io
from datetime import date, datetime
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from .filter import klartext

EINHEIT = {"geld": " €", "prozent": " %", "stunden": " h"}
STELLEN = {"geld": 2, "prozent": 1, "menge": 3, "zahl": 0, "stunden": 1}


def zahl_de(wert, stellen=2):
    text = f"{Decimal(wert):,.{stellen}f}"
    return text.replace(",", "§").replace(".", ",").replace("§", ".")


def formatieren(wert, typ, mit_einheit=True):
    """Deutsche Darstellung eines Zellwerts."""
    if wert is None or wert == "":
        return ""
    if isinstance(wert, (date, datetime)):
        return wert.strftime("%d.%m.%Y")
    if typ in STELLEN and isinstance(wert, (int, float, Decimal)):
        stellen = STELLEN[typ]
        if typ == "menge":
            wert = Decimal(wert)
            stellen = 0 if wert == wert.to_integral() else 3
        return zahl_de(wert, stellen) + (EINHEIT.get(typ, "") if mit_einheit else "")
    return str(wert)


def tabelle(bericht, ergebnis):
    """Für Web/PDF: Zeilen als Listen formatierter Zellen [(text, rechtsbündig)]."""
    zeilen = [[(formatieren(z.get(s.key), s.typ), s.numerisch) for s in bericht.spalten] for z in ergebnis["zeilen"]]
    summe = None
    if ergebnis["summen"] is not None:
        summe = [(formatieren(ergebnis["summen"].get(s.key), s.typ) if s.summe or s.key in ergebnis["summen"]
                  else ("Summe" if i == 0 else ""), s.numerisch) for i, s in enumerate(bericht.spalten)]
    return zeilen, summe


def dateiname(bericht, endung):
    return f"{bericht.code.replace('.', '_')}_{timezone.localdate():%Y%m%d}.{endung}"


def als_csv(bericht, ergebnis, werte):
    """CSV: Semikolon, UTF-8 mit BOM, deutsche Zahlen ohne Einheit."""
    puffer = io.StringIO()
    w = csv.writer(puffer, delimiter=";", lineterminator="\r\n")
    w.writerow([bericht.name])
    w.writerow(["; ".join(f"{k}: {v}" for k, v in klartext(bericht, werte))])
    w.writerow([s.label + (EINHEIT.get(s.typ, "").replace(" ", " in ") if s.typ in EINHEIT else "")
                for s in bericht.spalten])
    for z in ergebnis["zeilen"]:
        w.writerow([formatieren(z.get(s.key), s.typ, mit_einheit=False) for s in bericht.spalten])
    if ergebnis["summen"] is not None:
        w.writerow([formatieren(ergebnis["summen"].get(s.key), s.typ, False) if s.key in ergebnis["summen"]
                    else ("Summe" if i == 0 else "") for i, s in enumerate(bericht.spalten)])
    return ("\ufeff" + puffer.getvalue()).encode("utf-8")


XLSX_FORMATE = {"geld": '#,##0.00 "€"', "prozent": '0.0 "%"', "menge": "#,##0.###", "zahl": "#,##0",
                "stunden": '#,##0.0 "h"', "datum": "DD.MM.YYYY"}


def als_xlsx(bericht, ergebnis, werte):
    """Excel: Titel + Filterangaben im Kopf, formatierte Kopfzeile, Zahlenformate, Summenzeile (als Formel)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    from apps.core.models import Mandant

    wb = Workbook()
    ws = wb.active
    ws.title = bericht.name[:31].replace("/", "-")
    mandant = Mandant.aktuell()
    ws["A1"] = bericht.name
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"{mandant.name if mandant else ''} · erstellt {timezone.localtime():%d.%m.%Y %H:%M}"
    zeile = 3
    for label, text in klartext(bericht, werte):
        ws.cell(zeile, 1, label).font = Font(italic=True)
        ws.cell(zeile, 2, text)
        zeile += 1
    zeile += 1
    kopf = zeile
    fuellung = PatternFill("solid", fgColor="1F3864")
    for i, s in enumerate(bericht.spalten, start=1):
        c = ws.cell(kopf, i, s.label)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = fuellung
        c.alignment = Alignment(horizontal="right" if s.numerisch else "left", vertical="center", wrap_text=True)
    for z in ergebnis["zeilen"]:
        zeile += 1
        for i, s in enumerate(bericht.spalten, start=1):
            v = z.get(s.key)
            if isinstance(v, Decimal):
                v = float(v)
            elif v is not None and not isinstance(v, (int, float, date, datetime)):
                v = str(v)
            c = ws.cell(zeile, i, v)
            if s.typ in XLSX_FORMATE:
                c.number_format = XLSX_FORMATE[s.typ]
    erste, letzte = kopf + 1, zeile
    if ergebnis["summen"] is not None:
        zeile += 1
        linie = Border(top=Side(style="thin"), bottom=Side(style="double"))
        for i, s in enumerate(bericht.spalten, start=1):
            c = ws.cell(zeile, i)
            if s.summe and letzte >= erste:
                buchst = get_column_letter(i)
                c.value = f"=SUM({buchst}{erste}:{buchst}{letzte})"
            elif s.key in ergebnis["summen"]:
                c.value = float(ergebnis["summen"][s.key])
            elif s.summe:
                c.value = 0
            elif i == 1:
                c.value = "Summe"
            c.font = Font(bold=True)
            c.border = linie
            if s.typ in XLSX_FORMATE:
                c.number_format = XLSX_FORMATE[s.typ]
    for i, s in enumerate(bericht.spalten, start=1):
        laenge = max([len(s.label)] + [len(formatieren(z.get(s.key), s.typ)) for z in ergebnis["zeilen"][:500]])
        ws.column_dimensions[get_column_letter(i)].width = min(max(10, laenge + 2), 50)
    ws.freeze_panes = ws.cell(kopf + 1, 1)
    if letzte >= erste:
        ws.auto_filter.ref = f"A{kopf}:{get_column_letter(len(bericht.spalten))}{letzte}"
    ws.page_setup.orientation = "landscape" if bericht.quer else "portrait"
    ws.page_setup.fitToWidth = 1
    ws.print_title_rows = f"{kopf}:{kopf}"
    puffer = io.BytesIO()
    wb.save(puffer)
    return puffer.getvalue()


def als_pdf(bericht, ergebnis, werte):
    """PDF über WeasyPrint und das Template reporting/pdf/bericht.html (Querformat bei breiten Berichten)."""
    from django.template.loader import get_template
    from weasyprint import HTML

    from apps.core.models import Mandant

    zeilen, summe = tabelle(bericht, ergebnis)
    html = get_template("reporting/pdf/bericht.html").render({
        "b": bericht, "zeilen": zeilen, "summe": summe, "filter": klartext(bericht, werte),
        "hinweise": ergebnis["hinweise"], "mandant": Mandant.aktuell(), "jetzt": timezone.localtime(),
        "quer": bericht.quer})
    return HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf()


def als_json_zeilen(bericht, ergebnis):
    """Für die REST-API: Werte als Strings/Zahlen (Decimal → String, Datum → ISO)."""

    def wert(v):
        if isinstance(v, Decimal):
            return str(v)
        if isinstance(v, (date, datetime)):
            return v.isoformat()
        return v if isinstance(v, (int, float, str, bool)) or v is None else str(v)

    return {
        "zeilen": [{s.key: wert(z.get(s.key)) for s in bericht.spalten} for z in ergebnis["zeilen"]],
        "summen": {k: wert(v) for k, v in ergebnis["summen"].items()} if ergebnis["summen"] is not None else None,
        "hinweise": ergebnis["hinweise"],
    }

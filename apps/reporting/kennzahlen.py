"""Management-Cockpit: KPI-Kacheln und Diagrammdaten – jeweils nur bei Leserecht auf das Quellmodul."""
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.core.permissions import hat_recht

from .export import formatieren

NULL = Decimal(0)


def _vorjahr(d):
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29.02.
        return d.replace(year=d.year - 1, day=28)


def _veraenderung(aktuell, vorher):
    if not vorher:
        return None
    return ((aktuell - vorher) / abs(vorher) * 100).quantize(Decimal("0.1"))


def _kachel(name, wert, typ="geld", vergleich=None, hinweis="", stufe="", icon="bi-graph-up"):
    return {"name": name, "wert": wert, "text": formatieren(wert, typ) if wert is not None else "–",
            "vergleich": vergleich, "vergleich_text": formatieren(vergleich, "prozent") if vergleich is not None else "",
            "hinweis": hinweis, "stufe": stufe, "icon": icon}


def kacheln(user, heute=None):
    heute = heute or timezone.localdate()
    monat_start, jahr_start = heute.replace(day=1), date(heute.year, 1, 1)
    k = []
    if hat_recht(user, "vertrieb"):
        from apps.vertrieb import berichte as V

        um, um_vj = V.umsatz_summe(monat_start, heute), V.umsatz_summe(_vorjahr(monat_start), _vorjahr(heute))
        uj, uj_vj = V.umsatz_summe(jahr_start, heute), V.umsatz_summe(_vorjahr(jahr_start), _vorjahr(heute))
        k += [_kachel("Umsatz Monat", um, vergleich=_veraenderung(um, um_vj), hinweis="ggü. Vorjahresmonat",
                      icon="bi-currency-euro"),
              _kachel("Umsatz Jahr (kumuliert)", uj, vergleich=_veraenderung(uj, uj_vj), hinweis="ggü. Vorjahr",
                      icon="bi-calendar3"),
              _kachel("Auftragseingang Monat", V.auftragseingang(monat_start, heute), icon="bi-inbox"),
              _kachel("Auftragsbestand", V.auftragsbestand_wert(), icon="bi-list-check")]
    if hat_recht(user, "lager"):
        from apps.lager.berichte import lagerwert

        k.append(_kachel("Lagerwert", lagerwert(), icon="bi-box-seam"))
    if hat_recht(user, "fibu"):
        from apps.fibu.views import kennzahlen as fibu_kz

        f = fibu_kz()
        k += [_kachel("Offene Forderungen", f["debitoren"], icon="bi-arrow-down-left-circle"),
              _kachel("Offene Verbindlichkeiten", f["kreditoren"], icon="bi-arrow-up-right-circle"),
              _kachel("Überfällige Forderungen", f["deb_ueberfaellig"], hinweis=f"{f['deb_ueberfaellig_n']} Posten",
                      stufe="danger" if f["deb_ueberfaellig"] else "", icon="bi-exclamation-triangle"),
              _kachel("Liquidität (Bank)", f["liquiditaet"], stufe="danger" if f["liquiditaet"] < 0 else "",
                      icon="bi-bank")]
    if hat_recht(user, "produktion"):
        from apps.produktion import berichte as P

        von = heute - timedelta(days=30)
        gut, schlecht = P.ausschuss_summen(von, heute)
        quote = (schlecht / (gut + schlecht) * 100).quantize(Decimal("0.1")) if gut + schlecht else None
        k += [_kachel("Leistungsgrad Produktion", P.leistungsgrad_gesamt(von, heute), "prozent",
                      hinweis="letzte 30 Tage", icon="bi-speedometer"),
              _kachel("Ausschussquote", quote, "prozent", hinweis="letzte 30 Tage",
                      stufe="warning" if quote and quote > 3 else "", icon="bi-x-octagon")]
    return k


def diagramme(user, heute=None):
    heute = heute or timezone.localdate()
    d = {}
    if hat_recht(user, "vertrieb"):
        from apps.vertrieb import berichte as V

        monate = []
        m = date(heute.year, heute.month, 1)
        for _ in range(12):
            monate.insert(0, m)
            m = date(m.year - (m.month == 1), (m.month - 2) % 12 + 1, 1)
        werte = []
        for i, anfang in enumerate(monate):
            ende = monate[i + 1] - timedelta(days=1) if i + 1 < len(monate) else heute
            werte.append(float(V.umsatz_summe(anfang, ende)))
        d["umsatz"] = {"labels": [m.strftime("%m/%Y") for m in monate], "werte": werte}
        top = V.top_kunden(heute - timedelta(days=365), heute)
        d["top_kunden"] = {"labels": [z["kunde"] for z in top], "werte": [float(z["umsatz"]) for z in top]}
    if hat_recht(user, "fibu"):
        from apps.fibu.berichte import RASTER, altersstruktur

        a = altersstruktur("D", heute)
        d["op_alter"] = {"labels": [label for _, label, *_ in RASTER], "werte": [float(a[k]) for k, *_ in RASTER]}
    return d

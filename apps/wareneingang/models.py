"""Wareneingang gegen Bestellung inkl. Chargen, Qualitätsprüfung und Differenzprotokoll.

Ein Wareneingang wird in einem Schritt gebucht (keine Entwürfe): Nummer, Lagerzugang (frei oder Sperrbestand),
Fortschreibung der Bestellung und Ereignis `wareneingang.gebucht` laufen in einer Transaktion.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.stammdaten.models import Artikel, Geschaeftspartner


class WareneingangFehler(Exception):
    """Fachlicher Fehler bei Wareneingang oder QS-Entscheid."""


class Wareneingang(models.Model):
    nummer = models.CharField("WE-Nummer", max_length=40, unique=True)
    bestellung = models.ForeignKey("einkauf.Bestellung", on_delete=models.PROTECT, related_name="wareneingaenge",
                                   verbose_name="Bestellung")
    lieferant = models.ForeignKey(Geschaeftspartner, on_delete=models.PROTECT, related_name="wareneingaenge",
                                  verbose_name="Lieferant")
    lieferschein_nr = models.CharField("Lieferschein-Nr. des Lieferanten", max_length=50)
    datum = models.DateField("Eingangsdatum", default=timezone.localdate)
    lagerort = models.ForeignKey("lager.Lagerort", on_delete=models.PROTECT, verbose_name="Lagerort")
    bemerkung = models.TextField("Bemerkung", blank=True)
    hinweise = models.TextField("Differenzprotokoll", blank=True, editable=False)
    gebucht_von = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+", verbose_name="Gebucht von")
    gebucht_am = models.DateTimeField("Gebucht am", auto_now_add=True)

    class Meta:
        verbose_name = "Wareneingang"
        verbose_name_plural = "Wareneingänge"
        ordering = ["-datum", "-pk"]

    def __str__(self):
        return f"Wareneingang {self.nummer}"

    @property
    def qs_offen(self):
        return self.positionen.filter(qs_status=WEPosition.QS.OFFEN).count()

    @property
    def wert(self):
        return sum((p.wert for p in self.positionen.all()), 0)


class WEPosition(models.Model):
    class QS(models.TextChoices):
        OFFEN = "OFFEN", "Prüfung offen"
        FREIGEGEBEN = "FREIGEGEBEN", "Freigegeben"
        GESPERRT = "GESPERRT", "Gesperrt / reklamiert"

    wareneingang = models.ForeignKey(Wareneingang, on_delete=models.CASCADE, related_name="positionen")
    bestellposition = models.ForeignKey("einkauf.BestellPosition", on_delete=models.PROTECT,
                                        related_name="wareneingaenge", verbose_name="Bestellposition")
    artikel = models.ForeignKey(Artikel, on_delete=models.PROTECT, verbose_name="Artikel")
    menge = models.DecimalField("Gelieferte Menge", max_digits=14, decimal_places=3)
    einheit = models.CharField("Einheit", max_length=10, blank=True)
    lagerplatz = models.ForeignKey("lager.Lagerplatz", null=True, blank=True, on_delete=models.PROTECT,
                                   verbose_name="Lagerplatz")
    charge = models.ForeignKey("lager.Charge", null=True, blank=True, on_delete=models.PROTECT, verbose_name="Charge")
    offen_vorher = models.DecimalField("Offen laut Bestellung", max_digits=14, decimal_places=3)
    mengendifferenz = models.DecimalField("Mengendifferenz", max_digits=14, decimal_places=3, default=0,
                                          help_text="> 0 Überlieferung, < 0 Unterlieferung")
    bestellpreis = models.DecimalField("Bestellpreis netto", max_digits=14, decimal_places=4)
    lieferpreis = models.DecimalField("Preis laut Lieferschein", max_digits=14, decimal_places=4, null=True,
                                      blank=True)
    preisdifferenz = models.DecimalField("Preisdifferenz je Einheit", max_digits=14, decimal_places=4, default=0)
    hinweis = models.CharField("Hinweis", max_length=300, blank=True)
    qs_status = models.CharField("Prüfstatus", max_length=12, choices=QS.choices, default=QS.OFFEN)
    qs_erforderlich = models.BooleanField("Prüfung erforderlich", default=False)
    qs_bemerkung = models.CharField("Prüfbemerkung", max_length=300, blank=True)
    qs_von = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="+", verbose_name="Geprüft von")
    qs_am = models.DateTimeField("Geprüft am", null=True, blank=True)
    zuruckgesendet = models.BooleanField("An Lieferanten zurückgesendet", default=False)
    lagerbewegung = models.ForeignKey("lager.Lagerbewegung", null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="+", verbose_name="Lagerbewegung (Zugang)")

    class Meta:
        verbose_name = "Wareneingangsposition"
        verbose_name_plural = "Wareneingangspositionen"
        ordering = ["bestellposition__position", "pk"]

    def __str__(self):
        return f"{self.wareneingang.nummer} {self.artikel.artikelnummer} {self.menge}"

    @property
    def wert(self):
        return self.lagerbewegung.wert if self.lagerbewegung_id else 0

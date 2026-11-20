"""Einkauf: Bestellanforderungen, Anfragen und Bestellungen an Lieferanten.

Eigene Tabellen (nicht `belege.Beleg`): Bestellungen haben einen eigenen Lebenszyklus mit Liefermengen.
Nummern werden erst bei Freigabe über `apps.core.nummernkreise.naechste_nummer()` vergeben.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.stammdaten.models import Artikel, Geschaeftspartner, Zahlungsbedingung

ZWEI = Decimal("0.01")


class EinkaufFehler(Exception):
    """Fachlicher Fehler im Einkaufs-Workflow."""


class Bestellanforderung(models.Model):
    class Status(models.TextChoices):
        OFFEN = "OFFEN", "Offen"
        BESTELLT = "BESTELLT", "In Bestellung übernommen"
        ABGELEHNT = "ABGELEHNT", "Abgelehnt"

    nummer = models.CharField("BANF-Nr.", max_length=40, unique=True)
    artikel = models.ForeignKey(Artikel, on_delete=models.PROTECT, verbose_name="Artikel")
    menge = models.DecimalField("Menge", max_digits=14, decimal_places=3)
    bedarfstermin = models.DateField("Bedarfstermin", null=True, blank=True)
    begruendung = models.CharField("Begründung / Verwendung", max_length=300, blank=True)
    status = models.CharField("Status", max_length=10, choices=Status.choices, default=Status.OFFEN)
    bestellposition = models.ForeignKey("BestellPosition", null=True, blank=True, on_delete=models.SET_NULL,
                                        related_name="anforderungen", verbose_name="Bestellposition")
    angefordert_von = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                        related_name="+", verbose_name="Angefordert von")
    erstellt_am = models.DateTimeField("Erstellt am", auto_now_add=True)

    class Meta:
        verbose_name = "Bestellanforderung"
        verbose_name_plural = "Bestellanforderungen"
        ordering = ["-erstellt_am"]

    def __str__(self):
        return f"BANF {self.nummer}"


class Bestellung(models.Model):
    class Typ(models.TextChoices):
        ANFRAGE = "ANFRAGE", "Anfrage"
        BESTELLUNG = "BESTELLUNG", "Bestellung"

    class Status(models.TextChoices):
        ENTWURF = "ENTWURF", "Entwurf"
        ANGEFRAGT = "ANGEFRAGT", "Angefragt"
        BESTELLT = "BESTELLT", "Bestellt"
        TEILGELIEFERT = "TEILGELIEFERT", "Teilgeliefert"
        GELIEFERT = "GELIEFERT", "Vollständig geliefert"
        STORNIERT = "STORNIERT", "Storniert"

    OFFENE_STATI = (Status.BESTELLT, Status.TEILGELIEFERT)

    typ = models.CharField("Typ", max_length=10, choices=Typ.choices, default=Typ.BESTELLUNG)
    nummer = models.CharField("Nummer", max_length=40, blank=True, db_index=True)
    status = models.CharField("Status", max_length=15, choices=Status.choices, default=Status.ENTWURF)
    lieferant = models.ForeignKey(Geschaeftspartner, on_delete=models.PROTECT, related_name="einkaufsbestellungen",
                                  verbose_name="Lieferant", limit_choices_to={"ist_lieferant": True})
    partner_daten = models.JSONField("Lieferantenanschrift (Stand Freigabe)", default=dict, blank=True)
    datum = models.DateField("Datum", default=timezone.localdate)
    liefertermin = models.DateField("Gewünschter Liefertermin", null=True, blank=True,
                                    help_text="Standard für Positionen ohne eigenen Termin.")
    lieferort = models.ForeignKey("lager.Lagerort", null=True, blank=True, on_delete=models.PROTECT,
                                  verbose_name="Lieferung an Lager")
    zahlungsbedingung = models.ForeignKey(Zahlungsbedingung, null=True, blank=True, on_delete=models.PROTECT,
                                          verbose_name="Zahlungsbedingung")
    lieferbedingung = models.CharField("Lieferbedingung", max_length=100, blank=True, default="frei Haus")
    ihre_referenz = models.CharField("Ihr Angebot / Referenz", max_length=100, blank=True)
    kopftext = models.TextField("Kopftext", blank=True)
    fusstext = models.TextField("Fußtext", blank=True)
    anfrage = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="folgebestellungen",
                                verbose_name="Aus Anfrage")
    summe_netto = models.DecimalField("Summe netto", max_digits=14, decimal_places=2, default=0)
    summe_steuer = models.DecimalField("Steuer", max_digits=14, decimal_places=2, default=0)
    summe_brutto = models.DecimalField("Summe brutto", max_digits=14, decimal_places=2, default=0)
    erstellt_von = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+", verbose_name="Erstellt von")
    erstellt_am = models.DateTimeField("Erstellt am", auto_now_add=True)
    freigegeben_von = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                        related_name="+", verbose_name="Freigegeben von")
    freigegeben_am = models.DateTimeField("Freigegeben am", null=True, blank=True)
    storniert_am = models.DateTimeField("Storniert am", null=True, blank=True)
    storno_grund = models.CharField("Stornogrund", max_length=300, blank=True)

    class Meta:
        verbose_name = "Bestellung"
        verbose_name_plural = "Bestellungen"
        ordering = ["-datum", "-pk"]
        constraints = [models.UniqueConstraint(fields=["nummer"], condition=~Q(nummer=""),
                                               name="einkauf_bestellnummer_eindeutig")]

    def __str__(self):
        return f"{self.get_typ_display()} {self.nummer or '(Entwurf #%s)' % self.pk}"

    @property
    def ist_bearbeitbar(self):
        return self.status == self.Status.ENTWURF

    @property
    def ist_offen(self):
        return self.typ == self.Typ.BESTELLUNG and self.status in self.OFFENE_STATI

    def berechne_summen(self):
        netto, steuer = Decimal(0), Decimal(0)
        for p in self.positionen.all():
            netto += p.betrag_netto
            steuer += (p.betrag_netto * p.steuersatz / 100)
        self.summe_netto = netto.quantize(ZWEI, ROUND_HALF_UP)
        self.summe_steuer = steuer.quantize(ZWEI, ROUND_HALF_UP)
        self.summe_brutto = self.summe_netto + self.summe_steuer

    def steuer_aufstellung(self):
        werte = {}
        for p in self.positionen.all():
            basis, st = werte.get(p.steuersatz, (Decimal(0), Decimal(0)))
            werte[p.steuersatz] = (basis + p.betrag_netto, st + p.betrag_netto * p.steuersatz / 100)
        return {k: (v[0].quantize(ZWEI), v[1].quantize(ZWEI, ROUND_HALF_UP)) for k, v in sorted(werte.items())}


class BestellPosition(models.Model):
    bestellung = models.ForeignKey(Bestellung, on_delete=models.CASCADE, related_name="positionen")
    position = models.PositiveIntegerField("Pos.", default=10)
    artikel = models.ForeignKey(Artikel, on_delete=models.PROTECT, related_name="bestellpositionen",
                                verbose_name="Artikel")
    bezeichnung = models.CharField("Bezeichnung", max_length=200, blank=True)
    lieferanten_artikelnummer = models.CharField("Lief.-Art.-Nr.", max_length=50, blank=True)
    menge = models.DecimalField("Menge", max_digits=14, decimal_places=3)
    einheit = models.CharField("Einheit", max_length=10, blank=True)
    einzelpreis = models.DecimalField("Einzelpreis", max_digits=14, decimal_places=4, default=0)
    rabatt_prozent = models.DecimalField("Rabatt %", max_digits=5, decimal_places=2, default=0)
    steuersatz = models.DecimalField("USt %", max_digits=5, decimal_places=2, default=19)
    liefertermin = models.DateField("Liefertermin", null=True, blank=True)
    geliefert_menge = models.DecimalField("Geliefert", max_digits=14, decimal_places=3, default=0, editable=False)
    erledigt = models.BooleanField("Restmenge abgeschlossen", default=False, editable=False,
                                   help_text="Unterlieferung akzeptiert – keine weitere Lieferung erwartet.")

    class Meta:
        verbose_name = "Bestellposition"
        verbose_name_plural = "Bestellpositionen"
        ordering = ["position", "pk"]

    def __str__(self):
        return f"{self.bestellung} Pos. {self.position}"

    @property
    def betrag_netto(self):
        return (self.menge * self.einzelpreis * (1 - self.rabatt_prozent / 100)).quantize(ZWEI, ROUND_HALF_UP)

    @property
    def nettopreis(self):
        """Einzelpreis nach Rabatt (für den Wareneingang/Preisvergleich)."""
        return (self.einzelpreis * (1 - self.rabatt_prozent / 100)).quantize(Decimal("0.0001"), ROUND_HALF_UP)

    @property
    def offen_menge(self):
        if self.erledigt:
            return Decimal(0)
        return max(self.menge - self.geliefert_menge, Decimal(0))

    @property
    def termin(self):
        return self.liefertermin or self.bestellung.liefertermin

    @property
    def ueberfaellig(self):
        return bool(self.offen_menge and self.termin and self.termin < timezone.localdate())

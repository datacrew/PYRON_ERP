"""Lager: Lagerorte/-plätze, Chargen, Bestände, unveränderliches Bewegungsjournal, Inventur.

Bestände werden ausschließlich über `apps.lager.services.buchen()` verändert. Jede Veränderung erzeugt
eine Lagerbewegung; diese ist im Code und per PostgreSQL-Trigger gegen Änderung/Löschung geschützt.
Bewertung: gleitender Durchschnittspreis je Artikel (über alle Lagerorte, inkl. Sperrbestand).
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.stammdaten.models import Artikel, Geschaeftspartner


class LagerFehler(Exception):
    """Fachlicher Fehler bei einer Lagerbuchung (z. B. Bestand nicht ausreichend, Charge fehlt)."""


class Bestandsart(models.TextChoices):
    FREI = "FREI", "Frei verfügbar"
    GESPERRT = "GESPERRT", "Sperrbestand (QS)"


class Lagerort(models.Model):
    code = models.CharField("Kürzel", max_length=20, unique=True)
    bezeichnung = models.CharField("Bezeichnung", max_length=100)
    beschreibung = models.TextField("Beschreibung", blank=True)
    ist_wareneingang = models.BooleanField("Standard für Wareneingang", default=False)
    aktiv = models.BooleanField("Aktiv", default=True)

    class Meta:
        verbose_name = "Lagerort"
        verbose_name_plural = "Lagerorte"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.bezeichnung}"


class Lagerplatz(models.Model):
    lagerort = models.ForeignKey(Lagerort, on_delete=models.CASCADE, related_name="plaetze", verbose_name="Lagerort")
    code = models.CharField("Platz", max_length=30)
    bezeichnung = models.CharField("Bezeichnung", max_length=100, blank=True)
    aktiv = models.BooleanField("Aktiv", default=True)

    class Meta:
        verbose_name = "Lagerplatz"
        verbose_name_plural = "Lagerplätze"
        ordering = ["lagerort__code", "code"]
        constraints = [models.UniqueConstraint(fields=["lagerort", "code"], name="lager_platz_eindeutig")]

    def __str__(self):
        return f"{self.lagerort.code}/{self.code}"


class Charge(models.Model):
    artikel = models.ForeignKey(Artikel, on_delete=models.PROTECT, related_name="chargen", verbose_name="Artikel")
    chargennummer = models.CharField("Chargennummer", max_length=50)
    lieferant = models.ForeignKey(Geschaeftspartner, null=True, blank=True, on_delete=models.PROTECT,
                                  related_name="chargen", verbose_name="Lieferant",
                                  limit_choices_to={"ist_lieferant": True})
    lieferantencharge = models.CharField("Lieferantencharge", max_length=50, blank=True)
    herstelldatum = models.DateField("Herstelldatum", null=True, blank=True)
    mhd = models.DateField("Mindesthaltbarkeit", null=True, blank=True)
    bemerkung = models.CharField("Bemerkung", max_length=200, blank=True)
    erstellt_am = models.DateTimeField("Angelegt am", auto_now_add=True)

    class Meta:
        verbose_name = "Charge"
        verbose_name_plural = "Chargen"
        ordering = ["artikel__artikelnummer", "chargennummer"]
        constraints = [models.UniqueConstraint(fields=["artikel", "chargennummer"], name="lager_charge_eindeutig")]

    def __str__(self):
        return f"{self.chargennummer} ({self.artikel.artikelnummer})"

    @property
    def abgelaufen(self):
        return bool(self.mhd and self.mhd < timezone.localdate())


class ArtikelLager(models.Model):
    """Dispositions- und Bewertungsdaten eines Artikels (1:1 zum Artikelstamm)."""

    artikel = models.OneToOneField(Artikel, on_delete=models.PROTECT, related_name="lagerdaten",
                                   verbose_name="Artikel")
    mindestbestand = models.DecimalField("Mindestbestand", max_digits=14, decimal_places=3, default=0,
                                         help_text="Sicherheitsbestand – Unterschreitung ist kritisch.")
    meldebestand = models.DecimalField("Meldebestand", max_digits=14, decimal_places=3, default=0,
                                       help_text="Bei Unterschreitung (inkl. offener Bestellungen) Bestellvorschlag.")
    bestellmenge = models.DecimalField("Standard-Bestellmenge", max_digits=14, decimal_places=3, default=0)
    wiederbeschaffungszeit_tage = models.PositiveIntegerField("Wiederbeschaffungszeit (Tage)", default=0)
    bevorzugter_lieferant = models.ForeignKey(Geschaeftspartner, null=True, blank=True, on_delete=models.SET_NULL,
                                              verbose_name="Bevorzugter Lieferant",
                                              limit_choices_to={"ist_lieferant": True})
    standard_lagerort = models.ForeignKey(Lagerort, null=True, blank=True, on_delete=models.SET_NULL,
                                          verbose_name="Standard-Lagerort")
    qs_pflicht = models.BooleanField("Wareneingangsprüfung erforderlich", default=False,
                                     help_text="Wareneingänge werden zunächst in den Sperrbestand gebucht.")
    durchschnittspreis = models.DecimalField("Gleitender Durchschnittspreis", max_digits=14, decimal_places=4,
                                             default=0, editable=False)
    bestand_gesamt = models.DecimalField("Gesamtbestand", max_digits=14, decimal_places=3, default=0,
                                         editable=False)

    class Meta:
        verbose_name = "Dispositionsdaten"
        verbose_name_plural = "Dispositionsdaten"
        ordering = ["artikel__artikelnummer"]

    def __str__(self):
        return f"Disposition {self.artikel.artikelnummer}"

    @property
    def bestandswert(self):
        return (self.bestand_gesamt * self.durchschnittspreis).quantize(Decimal("0.01"))


class Bestand(models.Model):
    artikel = models.ForeignKey(Artikel, on_delete=models.PROTECT, related_name="bestaende", verbose_name="Artikel")
    lagerort = models.ForeignKey(Lagerort, on_delete=models.PROTECT, related_name="bestaende",
                                 verbose_name="Lagerort")
    lagerplatz = models.ForeignKey(Lagerplatz, null=True, blank=True, on_delete=models.PROTECT,
                                   verbose_name="Lagerplatz")
    charge = models.ForeignKey(Charge, null=True, blank=True, on_delete=models.PROTECT, related_name="bestaende",
                               verbose_name="Charge")
    bestandsart = models.CharField("Bestandsart", max_length=10, choices=Bestandsart.choices,
                                   default=Bestandsart.FREI)
    menge = models.DecimalField("Menge", max_digits=14, decimal_places=3, default=0)
    aktualisiert = models.DateTimeField("Zuletzt bewegt", auto_now=True)

    class Meta:
        verbose_name = "Bestand"
        verbose_name_plural = "Bestände"
        ordering = ["artikel__artikelnummer", "lagerort__code"]
        constraints = [
            models.UniqueConstraint(fields=["artikel", "lagerort", "lagerplatz", "charge", "bestandsart"],
                                    name="lager_bestand_eindeutig", nulls_distinct=False),
            models.CheckConstraint(condition=Q(menge__gte=0), name="lager_bestand_nicht_negativ"),
        ]

    def __str__(self):
        return f"{self.artikel.artikelnummer} @ {self.lagerplatz or self.lagerort.code}: {self.menge}"


class Lagerbewegung(models.Model):
    """Unveränderliches Bewegungsjournal. Korrekturen nur über Gegenbuchungen."""

    class Art(models.TextChoices):
        ZUGANG = "ZUGANG", "Zugang"
        ABGANG = "ABGANG", "Abgang"
        UMBUCHUNG = "UMBUCHUNG", "Umbuchung"
        INVENTUR = "INVENTUR", "Inventurkorrektur"

    zeitpunkt = models.DateTimeField("Zeitpunkt", auto_now_add=True, db_index=True)
    buchungsdatum = models.DateField("Buchungsdatum", default=timezone.localdate)
    art = models.CharField("Bewegungsart", max_length=10, choices=Art.choices)
    artikel = models.ForeignKey(Artikel, on_delete=models.PROTECT, related_name="lagerbewegungen",
                                verbose_name="Artikel")
    charge = models.ForeignKey(Charge, null=True, blank=True, on_delete=models.PROTECT, verbose_name="Charge")
    menge = models.DecimalField("Menge", max_digits=14, decimal_places=3)
    von_lagerort = models.ForeignKey(Lagerort, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                     verbose_name="Von Lagerort")
    von_lagerplatz = models.ForeignKey(Lagerplatz, null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="+", verbose_name="Von Platz")
    von_bestandsart = models.CharField("Von Bestandsart", max_length=10, choices=Bestandsart.choices, blank=True)
    nach_lagerort = models.ForeignKey(Lagerort, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                      verbose_name="Nach Lagerort")
    nach_lagerplatz = models.ForeignKey(Lagerplatz, null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="+", verbose_name="Nach Platz")
    nach_bestandsart = models.CharField("Nach Bestandsart", max_length=10, choices=Bestandsart.choices,
                                        blank=True)
    bewertungspreis = models.DecimalField("Bewertungspreis je Einheit", max_digits=14, decimal_places=4)
    wert = models.DecimalField("Wertänderung", max_digits=14, decimal_places=2)
    durchschnittspreis_neu = models.DecimalField("Ø-Preis danach", max_digits=14, decimal_places=4)
    bestand_neu = models.DecimalField("Gesamtbestand danach", max_digits=14, decimal_places=3)
    quelle = models.CharField("Quelle", max_length=30, default="manuell")
    quelle_id = models.PositiveBigIntegerField("Quell-ID", null=True, blank=True)
    beleg_nummer = models.CharField("Belegnummer", max_length=40, blank=True)
    grund = models.CharField("Grund / Bemerkung", max_length=300, blank=True)
    benutzer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="+", verbose_name="Benutzer")

    class Meta:
        verbose_name = "Lagerbewegung"
        verbose_name_plural = "Lagerbewegungen"
        ordering = ["-zeitpunkt", "-pk"]

    def __str__(self):
        return f"#{self.pk} {self.get_art_display()} {self.artikel.artikelnummer} {self.menge}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise LagerFehler("Lagerbewegungen sind unveränderlich – bitte eine Gegenbuchung erfassen.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise LagerFehler("Lagerbewegungen dürfen nicht gelöscht werden.")

    @property
    def von_text(self):
        return _ort_text(self.von_lagerort, self.von_lagerplatz, self.von_bestandsart)

    @property
    def nach_text(self):
        return _ort_text(self.nach_lagerort, self.nach_lagerplatz, self.nach_bestandsart)


def _ort_text(ort, platz, art):
    if not ort:
        return ""
    text = str(platz) if platz else ort.code
    return f"{text} (gesperrt)" if art == Bestandsart.GESPERRT else text


class Inventur(models.Model):
    class Status(models.TextChoices):
        OFFEN = "OFFEN", "In Zählung"
        GEBUCHT = "GEBUCHT", "Gebucht"

    nummer = models.CharField("Inventurnummer", max_length=40, unique=True)
    lagerort = models.ForeignKey(Lagerort, on_delete=models.PROTECT, verbose_name="Lagerort")
    stichtag = models.DateField("Stichtag", default=timezone.localdate)
    bezeichnung = models.CharField("Bezeichnung", max_length=100, blank=True)
    status = models.CharField("Status", max_length=10, choices=Status.choices, default=Status.OFFEN)
    erstellt_von = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                     related_name="+", verbose_name="Erstellt von")
    erstellt_am = models.DateTimeField("Erstellt am", auto_now_add=True)
    gebucht_von = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+", verbose_name="Gebucht von")
    gebucht_am = models.DateTimeField("Gebucht am", null=True, blank=True)

    class Meta:
        verbose_name = "Inventur"
        verbose_name_plural = "Inventuren"
        ordering = ["-stichtag", "-pk"]

    def __str__(self):
        return f"Inventur {self.nummer}"


class InventurPosition(models.Model):
    inventur = models.ForeignKey(Inventur, on_delete=models.CASCADE, related_name="positionen")
    artikel = models.ForeignKey(Artikel, on_delete=models.PROTECT, verbose_name="Artikel")
    lagerplatz = models.ForeignKey(Lagerplatz, null=True, blank=True, on_delete=models.PROTECT,
                                   verbose_name="Lagerplatz")
    charge = models.ForeignKey(Charge, null=True, blank=True, on_delete=models.PROTECT, verbose_name="Charge")
    bestandsart = models.CharField("Bestandsart", max_length=10, choices=Bestandsart.choices,
                                   default=Bestandsart.FREI)
    sollmenge = models.DecimalField("Buchbestand", max_digits=14, decimal_places=3, default=0)
    istmenge = models.DecimalField("Gezählt", max_digits=14, decimal_places=3, null=True, blank=True)
    differenz = models.DecimalField("Gebuchte Differenz", max_digits=14, decimal_places=3, null=True, blank=True)

    class Meta:
        verbose_name = "Inventurposition"
        verbose_name_plural = "Inventurpositionen"
        ordering = ["artikel__artikelnummer", "lagerplatz__code", "pk"]

    @property
    def abweichung(self):
        return None if self.istmenge is None else self.istmenge - self.sollmenge

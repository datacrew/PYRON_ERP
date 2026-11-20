"""Fertigung: Fertigungsaufträge, Material, Arbeitsgänge, BDE (Anwesenheit/Stempelungen), Rückmeldungen,
Materialentnahmen und Fertigmeldungen. Geschäftslogik liegt in `services.py`."""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.lager.models import Charge, Lagerbewegung, Lagerort, Lagerplatz
from apps.stammdaten.models import Arbeitsplan, Arbeitsplatz, Artikel, Lohnart, Mitarbeiter, Stueckliste

NULL = Decimal(0)


class ProduktionFehler(Exception):
    pass


class Fertigungsauftrag(models.Model):
    class Status(models.TextChoices):
        ENTWURF = "ENTWURF", "Entwurf"
        FREIGEGEBEN = "FREIGEGEBEN", "Freigegeben"
        IN_ARBEIT = "IN_ARBEIT", "In Arbeit"
        TEILGEFERTIGT = "TEILGEFERTIGT", "Rückgemeldet / teilgefertigt"
        ABGESCHLOSSEN = "ABGESCHLOSSEN", "Abgeschlossen"
        STORNIERT = "STORNIERT", "Storniert"

    OFFENE_STATI = [Status.FREIGEGEBEN, Status.IN_ARBEIT, Status.TEILGEFERTIGT]

    nummer = models.CharField("FA-Nummer", max_length=30, unique=True)
    artikel = models.ForeignKey(Artikel, on_delete=models.PROTECT, related_name="fertigungsauftraege",
                                verbose_name="Erzeugnis")
    stueckliste = models.ForeignKey(Stueckliste, null=True, blank=True, on_delete=models.PROTECT,
                                    verbose_name="Stückliste")
    arbeitsplan = models.ForeignKey(Arbeitsplan, null=True, blank=True, on_delete=models.PROTECT,
                                    verbose_name="Arbeitsplan")
    menge = models.DecimalField("Auftragsmenge", max_digits=12, decimal_places=3)
    starttermin = models.DateField("Starttermin", null=True, blank=True)
    termin = models.DateField("Endtermin", null=True, blank=True)
    status = models.CharField("Status", max_length=15, choices=Status.choices, default=Status.ENTWURF)
    uebergeordnet = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="unterauftraege", verbose_name="Übergeordneter Auftrag")
    ziel_lagerort = models.ForeignKey(Lagerort, null=True, blank=True, on_delete=models.PROTECT,
                                      verbose_name="Ziel-Lagerort")
    bemerkung = models.TextField("Bemerkung", blank=True)
    gut_menge = models.DecimalField("Fertig gemeldet", max_digits=12, decimal_places=3, default=0)
    ausschuss_menge = models.DecimalField("Ausschuss (letzter AG)", max_digits=12, decimal_places=3, default=0)
    erstellt_am = models.DateTimeField("Angelegt am", auto_now_add=True)
    erstellt_von = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+", verbose_name="Angelegt von")
    freigegeben_am = models.DateTimeField("Freigegeben am", null=True, blank=True)
    freigegeben_von = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="+", verbose_name="Freigegeben von")
    abgeschlossen_am = models.DateTimeField("Abgeschlossen am", null=True, blank=True)
    storno_grund = models.CharField("Stornogrund", max_length=300, blank=True)

    class Meta:
        verbose_name = "Fertigungsauftrag"
        verbose_name_plural = "Fertigungsaufträge"
        ordering = ["-erstellt_am", "-pk"]

    def __str__(self):
        return f"{self.nummer} {self.artikel.artikelnummer}"

    @property
    def ist_offen(self):
        return self.status in self.OFFENE_STATI

    @property
    def offen_menge(self):
        return max(self.menge - self.gut_menge, NULL)

    @property
    def ueberfaellig(self):
        return bool(self.ist_offen and self.termin and self.termin < timezone.localdate())

    @property
    def fortschritt_prozent(self):
        return int(min(self.gut_menge / self.menge * 100, 100)) if self.menge else 0


class FAMaterial(models.Model):
    """Materialbedarf eines Auftrags (bei Freigabe aus der Stückliste kopiert)."""

    auftrag = models.ForeignKey(Fertigungsauftrag, on_delete=models.CASCADE, related_name="materialien")
    position = models.PositiveIntegerField("Pos.", default=10)
    artikel = models.ForeignKey(Artikel, on_delete=models.PROTECT, related_name="+", verbose_name="Material")
    soll_menge = models.DecimalField("Soll-Menge", max_digits=14, decimal_places=3)
    entnommen_menge = models.DecimalField("Entnommen", max_digits=14, decimal_places=3, default=0)
    unterauftrag = models.ForeignKey(Fertigungsauftrag, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="deckt_material", verbose_name="Unterauftrag")

    class Meta:
        verbose_name = "Auftragsmaterial"
        verbose_name_plural = "Auftragsmaterial"
        ordering = ["position"]

    def __str__(self):
        return f"{self.auftrag.nummer}/{self.position} {self.artikel.artikelnummer}"

    @property
    def offen_menge(self):
        return max(self.soll_menge - self.entnommen_menge, NULL)


class FAArbeitsgang(models.Model):
    """Arbeitsgang eines Auftrags (bei Freigabe aus dem Arbeitsplan kopiert). Stempelcode: <FA-Nr>-<AG-Nr>."""

    class Status(models.TextChoices):
        OFFEN = "OFFEN", "Offen"
        LAEUFT = "LAEUFT", "Läuft"
        UNTERBROCHEN = "UNTERBROCHEN", "Unterbrochen"
        ERLEDIGT = "ERLEDIGT", "Erledigt"

    auftrag = models.ForeignKey(Fertigungsauftrag, on_delete=models.CASCADE, related_name="arbeitsgaenge")
    position = models.PositiveIntegerField("AG-Nr.")
    bezeichnung = models.CharField("Arbeitsgang", max_length=200)
    arbeitsplatz = models.ForeignKey(Arbeitsplatz, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+", verbose_name="Arbeitsplatz")
    ruestzeit_min = models.DecimalField("Rüstzeit (min)", max_digits=8, decimal_places=2, default=0)
    stueckzeit_min = models.DecimalField("te je Stück (min)", max_digits=8, decimal_places=3, default=0)
    lohngruppe = models.CharField("Lohngruppe", max_length=20, blank=True)
    lohnart = models.CharField("Lohnart", max_length=10, choices=Lohnart.choices, default=Lohnart.AKKORD)
    status = models.CharField("Status", max_length=15, choices=Status.choices, default=Status.OFFEN)
    gut_menge = models.DecimalField("Gutmenge", max_digits=12, decimal_places=3, default=0)
    ausschuss_menge = models.DecimalField("Ausschuss", max_digits=12, decimal_places=3, default=0)
    nacharbeit_menge = models.DecimalField("Nacharbeit", max_digits=12, decimal_places=3, default=0)

    class Meta:
        verbose_name = "Auftragsarbeitsgang"
        verbose_name_plural = "Auftragsarbeitsgänge"
        ordering = ["auftrag", "position"]
        constraints = [models.UniqueConstraint(fields=["auftrag", "position"], name="produktion_ag_eindeutig")]

    def __str__(self):
        return f"{self.code} {self.bezeichnung}"

    @property
    def code(self):
        return f"{self.auftrag.nummer}-{self.position}"

    @property
    def ist_akkord(self):
        return self.lohnart == Lohnart.AKKORD

    def vorgabe_min(self, menge=None):
        """Vorgabezeit in Minuten = Rüstzeit + Menge × te (Standard: Auftragsmenge)."""
        menge = self.auftrag.menge if menge is None else menge
        return self.ruestzeit_min + self.stueckzeit_min * Decimal(menge)


class Anwesenheit(models.Model):
    """Kommen/Gehen. Je Mitarbeiter höchstens eine offene Anwesenheit."""

    mitarbeiter = models.ForeignKey(Mitarbeiter, on_delete=models.PROTECT, related_name="anwesenheiten")
    kommen = models.DateTimeField("Kommen")
    gehen = models.DateTimeField("Gehen", null=True, blank=True)
    bemerkung = models.CharField("Bemerkung", max_length=200, blank=True)

    class Meta:
        verbose_name = "Anwesenheit"
        verbose_name_plural = "Anwesenheiten"
        ordering = ["-kommen"]
        constraints = [
            models.UniqueConstraint(fields=["mitarbeiter"], condition=Q(gehen__isnull=True),
                                    name="produktion_anwesenheit_eine_offene"),
            models.CheckConstraint(condition=Q(gehen__isnull=True) | Q(gehen__gte=models.F("kommen")),
                                   name="produktion_anwesenheit_zeitfolge"),
        ]

    def __str__(self):
        return f"{self.mitarbeiter} {timezone.localtime(self.kommen):%d.%m.%Y %H:%M}"

    @property
    def dauer_min(self):
        return _minuten(self.kommen, self.gehen or timezone.now())


def _minuten(von, bis):
    return (Decimal((bis - von).total_seconds()) / 60).quantize(Decimal("0.01"))


class Stempelung(models.Model):
    """Auftragsstempelung je Arbeitsgang. Je Mitarbeiter höchstens eine offene Stempelung (keine Überlappung)."""

    class Taetigkeit(models.TextChoices):
        RUESTEN = "RUESTEN", "Rüsten"
        FERTIGEN = "FERTIGEN", "Fertigen"

    class EndeArt(models.TextChoices):
        UNTERBRECHUNG = "UNTERBRECHUNG", "Unterbrechung"
        ENDE = "ENDE", "Ende"

    class Quelle(models.TextChoices):
        TERMINAL = "TERMINAL", "Terminal"
        API = "API", "Externes Terminal (API)"
        MANUELL = "MANUELL", "Manuell nachgetragen"

    mitarbeiter = models.ForeignKey(Mitarbeiter, on_delete=models.PROTECT, related_name="stempelungen")
    arbeitsgang = models.ForeignKey(FAArbeitsgang, on_delete=models.PROTECT, related_name="stempelungen")
    taetigkeit = models.CharField("Tätigkeit", max_length=10, choices=Taetigkeit.choices,
                                  default=Taetigkeit.FERTIGEN)
    beginn = models.DateTimeField("Beginn")
    ende = models.DateTimeField("Ende", null=True, blank=True)
    ende_art = models.CharField("Abschluss", max_length=15, choices=EndeArt.choices, blank=True)
    quelle = models.CharField("Erfasst über", max_length=10, choices=Quelle.choices, default=Quelle.TERMINAL)
    korrigiert = models.BooleanField("Korrigiert", default=False)
    korrektur_grund = models.CharField("Korrekturgrund", max_length=300, blank=True)

    class Meta:
        verbose_name = "Stempelung"
        verbose_name_plural = "Stempelungen"
        ordering = ["-beginn", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["mitarbeiter"], condition=Q(ende__isnull=True),
                                    name="produktion_stempelung_eine_offene"),
            models.CheckConstraint(condition=Q(ende__isnull=True) | Q(ende__gte=models.F("beginn")),
                                   name="produktion_stempelung_zeitfolge"),
        ]

    def __str__(self):
        return f"{self.mitarbeiter.personalnummer} {self.arbeitsgang.code} {timezone.localtime(self.beginn):%d.%m. %H:%M}"

    @property
    def dauer_min(self):
        return _minuten(self.beginn, self.ende or timezone.now())


class Rueckmeldung(models.Model):
    class Grund(models.TextChoices):
        MATERIAL = "MATERIAL", "Materialfehler"
        MASS = "MASS", "Maßabweichung"
        BEARBEITUNG = "BEARBEITUNG", "Bearbeitungsfehler"
        EINRICHTEN = "EINRICHTEN", "Einrichteausschuss"
        BESCHAEDIGUNG = "BESCHAEDIGUNG", "Beschädigung / Handling"
        SONSTIGES = "SONSTIGES", "Sonstiges"

    arbeitsgang = models.ForeignKey(FAArbeitsgang, on_delete=models.PROTECT, related_name="rueckmeldungen")
    mitarbeiter = models.ForeignKey(Mitarbeiter, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="rueckmeldungen")
    stempelung = models.ForeignKey(Stempelung, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    zeitpunkt = models.DateTimeField("Zeitpunkt", default=timezone.now)
    gut_menge = models.DecimalField("Gutmenge", max_digits=12, decimal_places=3, default=0)
    ausschuss_menge = models.DecimalField("Ausschuss", max_digits=12, decimal_places=3, default=0)
    ausschuss_grund = models.CharField("Ausschussgrund", max_length=15, choices=Grund.choices, blank=True)
    nacharbeit_menge = models.DecimalField("Nacharbeit", max_digits=12, decimal_places=3, default=0)
    bemerkung = models.CharField("Bemerkung", max_length=300, blank=True)
    erfasst_von = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")

    class Meta:
        verbose_name = "Rückmeldung"
        verbose_name_plural = "Rückmeldungen"
        ordering = ["-zeitpunkt", "-pk"]

    def __str__(self):
        return f"{self.arbeitsgang.code}: {self.gut_menge} gut / {self.ausschuss_menge} Ausschuss"


class Materialentnahme(models.Model):
    auftrag = models.ForeignKey(Fertigungsauftrag, on_delete=models.PROTECT, related_name="entnahmen")
    material = models.ForeignKey(FAMaterial, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="entnahmen")
    artikel = models.ForeignKey(Artikel, on_delete=models.PROTECT, related_name="+")
    charge = models.ForeignKey(Charge, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    lagerort = models.ForeignKey(Lagerort, on_delete=models.PROTECT, related_name="+")
    lagerplatz = models.ForeignKey(Lagerplatz, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    menge = models.DecimalField("Menge", max_digits=14, decimal_places=3)
    wert = models.DecimalField("Wert", max_digits=14, decimal_places=2)
    bewegung = models.OneToOneField(Lagerbewegung, on_delete=models.PROTECT, related_name="+")
    zeitpunkt = models.DateTimeField("Zeitpunkt", auto_now_add=True)
    benutzer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="+")

    class Meta:
        verbose_name = "Materialentnahme"
        verbose_name_plural = "Materialentnahmen"
        ordering = ["-zeitpunkt", "-pk"]

    def __str__(self):
        return f"{self.auftrag.nummer}: {self.menge} {self.artikel.artikelnummer}"


class Fertigmeldung(models.Model):
    auftrag = models.ForeignKey(Fertigungsauftrag, on_delete=models.PROTECT, related_name="fertigmeldungen")
    menge = models.DecimalField("Menge", max_digits=12, decimal_places=3)
    charge = models.ForeignKey(Charge, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    lagerort = models.ForeignKey(Lagerort, on_delete=models.PROTECT, related_name="+")
    lagerplatz = models.ForeignKey(Lagerplatz, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    materialkosten = models.DecimalField("Materialkosten", max_digits=14, decimal_places=2, default=0)
    lohnkosten = models.DecimalField("Lohnkosten", max_digits=14, decimal_places=2, default=0)
    platzkosten = models.DecimalField("Arbeitsplatzkosten", max_digits=14, decimal_places=2, default=0)
    herstellkosten_stueck = models.DecimalField("Herstellkosten je Einheit", max_digits=14, decimal_places=4)
    bewegung = models.OneToOneField(Lagerbewegung, on_delete=models.PROTECT, related_name="+")
    zeitpunkt = models.DateTimeField("Zeitpunkt", auto_now_add=True)
    benutzer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="+")

    class Meta:
        verbose_name = "Fertigmeldung"
        verbose_name_plural = "Fertigmeldungen"
        ordering = ["-zeitpunkt", "-pk"]

    def __str__(self):
        return f"{self.auftrag.nummer}: {self.menge} fertig"

    @property
    def herstellkosten(self):
        return self.materialkosten + self.lohnkosten + self.platzkosten

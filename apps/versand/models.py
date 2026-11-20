"""Versand: Regionen (PLZ-Gebiete, Liefertage), Fahrzeuge, Touren mit Stopps (Lieferscheine),
optionale Geokoordinaten je PLZ (lokale Tabelle, kein externer Dienst) und LVS-Protokoll."""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

WOCHENTAGE = [(1, "Mo"), (2, "Di"), (3, "Mi"), (4, "Do"), (5, "Fr"), (6, "Sa"), (7, "So")]


class VersandFehler(Exception):
    """Fachlicher Fehler in Tourenplanung oder LVS-Schnittstelle."""


class Region(models.Model):
    """Liefergebiet. `plz_bereiche`: kommagetrennte PLZ-Präfixe oder Bereiche, z. B. „70-71, 73, 75000-75999“."""

    code = models.SlugField("Code", max_length=20, unique=True)
    name = models.CharField("Name", max_length=100)
    plz_bereiche = models.CharField("PLZ-Bereiche", max_length=500,
                                    help_text="Präfixe oder Bereiche, kommagetrennt: 70, 71, 72000-72999")
    liefertage = models.CharField("Liefertage (1=Mo … 7=So)", max_length=20, default="1,2,3,4,5",
                                  help_text="Kommagetrennt, z. B. 2,4 für Di und Do")
    land = models.CharField("Land", max_length=2, default="DE")
    sortierung = models.CharField("Sortierung der Stopps", max_length=10, default="PLZ",
                                  choices=[("PLZ", "nach PLZ"), ("ENTFERNUNG", "nach Entfernung (Geokoordinaten)")])
    standard_fahrzeug = models.ForeignKey("Fahrzeug", null=True, blank=True, on_delete=models.SET_NULL,
                                          related_name="+", verbose_name="Standard-Fahrzeug")
    aktiv = models.BooleanField("Aktiv", default=True)

    class Meta:
        verbose_name = "Region / Liefergebiet"
        verbose_name_plural = "Regionen / Liefergebiete"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} – {self.name}"

    def bereiche(self):
        """[(von, bis)] als Strings gleicher Länge für den Präfixvergleich."""
        ergebnis = []
        for teil in (self.plz_bereiche or "").replace(";", ",").split(","):
            teil = teil.strip().replace(" ", "")
            if not teil:
                continue
            von, _, bis = teil.partition("-")
            ergebnis.append((von, bis or von))
        return ergebnis

    def passt(self, plz, land="DE"):
        plz = (plz or "").strip()
        if not plz or (land or "DE") != self.land:
            return 0
        bester = 0
        for von, bis in self.bereiche():
            n = len(von)
            teil = plz[:n]
            if len(teil) == n and von <= teil <= bis[:n]:
                bester = max(bester, n)
        return bester  # Länge des passenden Präfixes = Spezifität

    def tage(self):
        return {int(x) for x in self.liefertage.replace(" ", "").split(",") if x.isdigit()}

    def liefert_am(self, datum):
        return datum.isoweekday() in self.tage()

    @property
    def liefertage_text(self):
        namen = dict(WOCHENTAGE)
        return ", ".join(namen[t] for t in sorted(self.tage()) if t in namen)


class PlzKoordinate(models.Model):
    """Optionale lokale Geokoordinaten (Ortsmittelpunkt) je PLZ – Pflege per Import/Admin, kein Online-Dienst."""

    land = models.CharField("Land", max_length=2, default="DE")
    plz = models.CharField("PLZ", max_length=10)
    ort = models.CharField("Ort", max_length=100, blank=True)
    breite = models.DecimalField("Breitengrad", max_digits=9, decimal_places=6)
    laenge = models.DecimalField("Längengrad", max_digits=9, decimal_places=6)

    class Meta:
        verbose_name = "PLZ-Koordinate"
        verbose_name_plural = "PLZ-Koordinaten"
        ordering = ["land", "plz"]
        constraints = [models.UniqueConstraint(fields=["land", "plz"], name="versand_plz_koord")]

    def __str__(self):
        return f"{self.plz} {self.ort}"


class Fahrzeug(models.Model):
    kennzeichen = models.CharField("Kennzeichen", max_length=20, unique=True)
    bezeichnung = models.CharField("Bezeichnung", max_length=100)
    max_kg = models.DecimalField("Max. Zuladung (kg)", max_digits=10, decimal_places=2)
    max_paletten = models.DecimalField("Max. Palettenstellplätze", max_digits=6, decimal_places=1)
    fahrer = models.CharField("Fahrer", max_length=100, blank=True)
    aktiv = models.BooleanField("Aktiv", default=True)

    class Meta:
        verbose_name = "Fahrzeug"
        verbose_name_plural = "Fahrzeuge"
        ordering = ["kennzeichen"]

    def __str__(self):
        return f"{self.kennzeichen} ({self.bezeichnung})"


class Tour(models.Model):
    class Status(models.TextChoices):
        GEPLANT = "GEPLANT", "Geplant"
        FREIGEGEBEN = "FREIGEGEBEN", "Freigegeben (Ladeliste)"
        UNTERWEGS = "UNTERWEGS", "Verladen / unterwegs"
        ABGESCHLOSSEN = "ABGESCHLOSSEN", "Abgeschlossen"

    nummer = models.CharField("Tournummer", max_length=30, unique=True, blank=True)
    datum = models.DateField("Lieferdatum", default=timezone.localdate, db_index=True)
    region = models.ForeignKey(Region, on_delete=models.PROTECT, related_name="touren", verbose_name="Region")
    fahrzeug = models.ForeignKey(Fahrzeug, null=True, blank=True, on_delete=models.PROTECT, related_name="touren",
                                 verbose_name="Fahrzeug")
    fahrer = models.CharField("Fahrer", max_length=100, blank=True)
    status = models.CharField("Status", max_length=15, choices=Status.choices, default=Status.GEPLANT)
    bemerkung = models.TextField("Bemerkung", blank=True)
    erstellt_von = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    erstellt_am = models.DateTimeField("Erstellt am", auto_now_add=True)

    class Meta:
        verbose_name = "Tour"
        verbose_name_plural = "Touren"
        ordering = ["-datum", "nummer"]

    def __str__(self):
        return self.nummer

    def save(self, *args, **kwargs):
        if not self.nummer:
            from apps.core.nummernkreise import naechste_nummer

            self.nummer = naechste_nummer("TOUR", self.datum)
        super().save(*args, **kwargs)

    def summen(self):
        kg, pal, pk = Decimal(0), Decimal(0), 0
        for s in self.stopps.select_related("lieferung"):
            kg += s.lieferung.gewicht
            pal += s.lieferung.paletten
            pk += s.lieferung.packstuecke
        return {"kg": kg, "paletten": pal, "packstuecke": pk}

    def auslastung(self):
        """dict(kg, paletten, kg_prozent, pal_prozent, ueberladen, meldungen)."""
        s = self.summen()
        f = self.fahrzeug
        kg_p = (s["kg"] / f.max_kg * 100).quantize(Decimal("0.1")) if f and f.max_kg else None
        pal_p = (s["paletten"] / f.max_paletten * 100).quantize(Decimal("0.1")) if f and f.max_paletten else None
        meldungen = []
        if f and s["kg"] > f.max_kg:
            meldungen.append(f"Gewicht {s['kg']:.0f} kg > Zuladung {f.max_kg:.0f} kg")
        if f and s["paletten"] > f.max_paletten:
            meldungen.append(f"Paletten {s['paletten']:.1f} > Stellplätze {f.max_paletten:.1f}")
        return {**s, "kg_prozent": kg_p, "pal_prozent": pal_p, "ueberladen": bool(meldungen),
                "meldungen": meldungen, "prozent": max([x for x in (kg_p, pal_p) if x is not None], default=None)}


class TourStopp(models.Model):
    tour = models.ForeignKey(Tour, on_delete=models.CASCADE, related_name="stopps")
    lieferung = models.OneToOneField("vertrieb.Lieferung", on_delete=models.PROTECT, related_name="tourstopp",
                                     verbose_name="Lieferschein")
    reihenfolge = models.PositiveIntegerField("Reihenfolge", default=10)
    entfernung_km = models.DecimalField("Entfernung vom Vorgänger (km)", max_digits=8, decimal_places=1,
                                        null=True, blank=True)

    class Meta:
        verbose_name = "Tourstopp"
        verbose_name_plural = "Tourstopps"
        ordering = ["reihenfolge", "pk"]

    def __str__(self):
        return f"{self.tour} #{self.reihenfolge}: {self.lieferung}"


class LvsProtokoll(models.Model):
    """Protokoll der LVS-Schnittstelle (Export Kommissionieraufträge, Import Rückmeldungen)."""

    class Richtung(models.TextChoices):
        EXPORT = "EXPORT", "Export (an LVS)"
        IMPORT = "IMPORT", "Import (Rückmeldung)"

    richtung = models.CharField("Richtung", max_length=6, choices=Richtung.choices)
    format = models.CharField("Format", max_length=4, default="JSON")
    erfolgreich = models.BooleanField("Erfolgreich", default=True)
    anzahl = models.PositiveIntegerField("Anzahl Aufträge", default=0)
    inhalt = models.TextField("Inhalt", blank=True)
    meldungen = models.TextField("Meldungen / Fehler", blank=True)
    benutzer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="+")
    zeitpunkt = models.DateTimeField("Zeitpunkt", auto_now_add=True)

    class Meta:
        verbose_name = "LVS-Protokoll"
        verbose_name_plural = "LVS-Protokoll"
        ordering = ["-zeitpunkt"]

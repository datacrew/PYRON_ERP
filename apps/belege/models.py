"""Einheitliches Belegmodell mit GoBD-Workflow.

Status:  ENTWURF → FREIGEGEBEN → FESTGESCHRIEBEN → (STORNIERT)
* ENTWURF:          frei bearbeitbar, noch ohne Belegnummer, löschbar.
* FREIGEGEBEN:      lückenlose Belegnummer vergeben, Inhalt gesperrt (Code), nicht löschbar.
* FESTGESCHRIEBEN:  Prüfsumme (SHA-256-Kette) berechnet, PDF archiviert; unveränderbar
                    (Code-Sperre + PostgreSQL-Trigger).
* STORNIERT:        nur Storno-Vermerk; Inhalt und Prüfsumme bleiben unverändert.
Die Workflow-Logik liegt in services.py.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.stammdaten.models import Artikel, Geschaeftspartner, Zahlungsbedingung

ZWEI = Decimal("0.01")


class BelegGesperrt(Exception):
    """Versuch, einen nicht mehr veränderbaren Beleg zu ändern oder zu löschen."""


class BelegVorlage(models.Model):
    """Im Admin/der Verwaltung editierbare PDF-Vorlage (Django-Template-Syntax, HTML + CSS)."""

    name = models.CharField("Name", max_length=100)
    belegart = models.CharField("Belegart", max_length=20, blank=True,
                                help_text="Leer = Standardvorlage für alle Belegarten.")
    html = models.TextField("HTML-Vorlage (Django-Template)")
    aktiv = models.BooleanField("Aktiv", default=True)
    geaendert_am = models.DateTimeField("Geändert am", auto_now=True)

    class Meta:
        verbose_name = "Belegvorlage"
        verbose_name_plural = "Belegvorlagen"
        ordering = ["belegart", "name"]

    def __str__(self):
        return f"{self.name} ({self.get_belegart_anzeige()})"

    def get_belegart_anzeige(self):
        return dict(Beleg.Belegart.choices).get(self.belegart, "alle Belegarten")


def _archiv_pfad(instanz, dateiname):
    return f"archiv/belege/{instanz.belegdatum:%Y}/{instanz.belegart.lower()}/{dateiname}"


class Beleg(models.Model):
    class Belegart(models.TextChoices):
        ANGEBOT = "ANGEBOT", "Angebot"
        AUFTRAG = "AUFTRAG", "Auftragsbestätigung"
        LIEFERSCHEIN = "LIEFERSCHEIN", "Lieferschein"
        RECHNUNG = "RECHNUNG", "Rechnung"
        GUTSCHRIFT = "GUTSCHRIFT", "Gutschrift"
        ANFRAGE = "ANFRAGE", "Anfrage"
        BESTELLUNG = "BESTELLUNG", "Bestellung"
        WARENEINGANG = "WARENEINGANG", "Wareneingangsbeleg"

    class Status(models.TextChoices):
        ENTWURF = "ENTWURF", "Entwurf"
        FREIGEGEBEN = "FREIGEGEBEN", "Freigegeben"
        FESTGESCHRIEBEN = "FESTGESCHRIEBEN", "Festgeschrieben"
        STORNIERT = "STORNIERT", "Storniert"

    EINKAUFSARTEN = {"ANFRAGE", "BESTELLUNG", "WARENEINGANG"}

    belegart = models.CharField("Belegart", max_length=20, choices=Belegart.choices)
    nummer = models.CharField("Belegnummer", max_length=40, unique=True, null=True, blank=True)
    status = models.CharField("Status", max_length=20, choices=Status.choices, default=Status.ENTWURF,
                              db_index=True)
    partner = models.ForeignKey(Geschaeftspartner, on_delete=models.PROTECT, related_name="belege",
                                verbose_name="Geschäftspartner")
    partner_daten = models.JSONField("Partnerdaten (Stand Freigabe)", default=dict, blank=True)
    belegdatum = models.DateField("Belegdatum", default=timezone.localdate)
    leistungsdatum = models.DateField("Leistungs-/Lieferdatum", null=True, blank=True)
    zahlungsbedingung = models.ForeignKey(Zahlungsbedingung, null=True, blank=True, on_delete=models.PROTECT,
                                          verbose_name="Zahlungsbedingung")
    ihre_referenz = models.CharField("Ihre Referenz / Bestellnummer", max_length=100, blank=True)
    betreff = models.CharField("Betreff", max_length=200, blank=True)
    kopftext = models.TextField("Kopftext", blank=True)
    fusstext = models.TextField("Fußtext", blank=True)
    waehrung = models.CharField("Währung", max_length=3, default="EUR")
    summe_netto = models.DecimalField("Summe netto", max_digits=14, decimal_places=2, default=0)
    summe_steuer = models.DecimalField("Umsatzsteuer", max_digits=14, decimal_places=2, default=0)
    summe_brutto = models.DecimalField("Summe brutto", max_digits=14, decimal_places=2, default=0)

    erstellt_von = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+", verbose_name="Erstellt von")
    erstellt_am = models.DateTimeField("Erstellt am", auto_now_add=True)
    freigegeben_von = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                        on_delete=models.SET_NULL, related_name="+", verbose_name="Freigegeben von")
    freigegeben_am = models.DateTimeField("Freigegeben am", null=True, blank=True)
    festgeschrieben_von = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                            on_delete=models.SET_NULL, related_name="+",
                                            verbose_name="Festgeschrieben von")
    festgeschrieben_am = models.DateTimeField("Festgeschrieben am", null=True, blank=True)
    storniert_von = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="+", verbose_name="Storniert von")
    storniert_am = models.DateTimeField("Storniert am", null=True, blank=True)
    storno_grund = models.TextField("Stornogrund", blank=True)

    # GoBD: Prüfsummenkette und Archiv
    ketten_index = models.PositiveIntegerField("Ketten-Index", null=True, blank=True, unique=True)
    pruefsumme = models.CharField("Prüfsumme (SHA-256)", max_length=64, blank=True)
    vorgaenger_pruefsumme = models.CharField("Prüfsumme Vorgänger", max_length=64, blank=True)
    pdf = models.FileField("Archiv-PDF", upload_to=_archiv_pfad, blank=True)
    pdf_pruefsumme = models.CharField("Prüfsumme PDF", max_length=64, blank=True)

    class Meta:
        verbose_name = "Beleg"
        verbose_name_plural = "Belege"
        ordering = ["-belegdatum", "-pk"]

    def __str__(self):
        return f"{self.get_belegart_display()} {self.nummer or f'Entwurf #{self.pk}'}"

    # ---------------------------------------------------------------- Sperren
    @property
    def ist_bearbeitbar(self):
        return self.status == self.Status.ENTWURF

    def _db_status(self):
        if not self.pk:
            return None
        return Beleg.objects.filter(pk=self.pk).values_list("status", flat=True).first()

    def save(self, *args, **kwargs):
        alt = self._db_status()
        if alt and alt != self.Status.ENTWURF and not getattr(self, "_workflow_uebergang", False):
            raise BelegGesperrt(f"{self} ist {self.get_status_display().lower()} und kann nicht geändert werden.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._db_status() not in (None, self.Status.ENTWURF):
            raise BelegGesperrt(f"{self} hat bereits eine Belegnummer und kann nur storniert werden.")
        return super().delete(*args, **kwargs)

    # ---------------------------------------------------------------- Beträge
    def steuer_aufstellung(self):
        """{Steuersatz: [Netto, Steuer]} – Steuer je Satz auf die Nettosumme gerundet (§ 14 UStG)."""
        gruppen = {}
        for p in self.positionen.all():
            gruppen.setdefault(p.steuersatz, Decimal(0))
            gruppen[p.steuersatz] += p.betrag_netto
        return {
            satz: [netto, (netto * satz / 100).quantize(ZWEI, ROUND_HALF_UP)]
            for satz, netto in sorted(gruppen.items(), reverse=True)
        }

    def berechne_summen(self):
        aufstellung = self.steuer_aufstellung()
        self.summe_netto = sum((n for n, _ in aufstellung.values()), Decimal(0))
        self.summe_steuer = sum((s for _, s in aufstellung.values()), Decimal(0))
        self.summe_brutto = self.summe_netto + self.summe_steuer

    @property
    def ist_einkauf(self):
        return self.belegart in self.EINKAUFSARTEN


class BelegPosition(models.Model):
    beleg = models.ForeignKey(Beleg, on_delete=models.CASCADE, related_name="positionen")
    position = models.PositiveIntegerField("Pos.", default=9999)
    artikel = models.ForeignKey(Artikel, null=True, blank=True, on_delete=models.PROTECT, verbose_name="Artikel")
    bezeichnung = models.CharField("Bezeichnung", max_length=300, blank=True)
    menge = models.DecimalField("Menge", max_digits=12, decimal_places=3, default=1)
    einheit = models.CharField("Einheit", max_length=10, blank=True)
    einzelpreis = models.DecimalField("Einzelpreis netto", max_digits=12, decimal_places=4, default=0)
    rabatt_prozent = models.DecimalField("Rabatt %", max_digits=5, decimal_places=2, default=0)
    steuersatz = models.DecimalField("USt %", max_digits=5, decimal_places=2, default=Decimal("19.00"))
    betrag_netto = models.DecimalField("Gesamt netto", max_digits=14, decimal_places=2, default=0)

    class Meta:
        verbose_name = "Belegposition"
        verbose_name_plural = "Belegpositionen"
        ordering = ["position", "pk"]

    def __str__(self):
        return f"{self.position}: {self.bezeichnung}"

    def berechne(self):
        faktor = (Decimal(100) - (self.rabatt_prozent or 0)) / Decimal(100)
        self.betrag_netto = (self.menge * self.einzelpreis * faktor).quantize(ZWEI, ROUND_HALF_UP)

    def _pruefe_beleg(self):
        status = Beleg.objects.filter(pk=self.beleg_id).values_list("status", flat=True).first()
        if status and status != Beleg.Status.ENTWURF:
            raise BelegGesperrt("Positionen freigegebener Belege können nicht geändert werden.")

    def save(self, *args, **kwargs):
        self._pruefe_beleg()
        if self.artikel_id:
            a = self.artikel
            self.bezeichnung = self.bezeichnung or a.bezeichnung
            self.einheit = self.einheit or a.einheit.code
        self.berechne()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._pruefe_beleg()
        return super().delete(*args, **kwargs)

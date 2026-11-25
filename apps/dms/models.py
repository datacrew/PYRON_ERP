"""Dokumenten-Management (DMS): revisionssichere Ablage, Versionen, Protokoll, Stapel und Lernregeln.

GoBD-Grundsätze (dreifach abgesichert: Code, Oberfläche, PostgreSQL-Trigger in Migration 0002):
* Jede Datei wird beim Eingang mit SHA-256 versiegelt; Versionszeilen sind unveränderbar (nur INSERT).
* Das durchsuchbare PDF/A aus der OCR ist eine eigene, abgeleitete Version – das Original bleibt erhalten.
* Archivierte Dokumente sind unlöschbar; Nummer, Art, OCR-Text und Archivierungsdaten sind eingefroren,
  die Aufbewahrungsfrist kann nur verlängert werden.
* Das Dokumentprotokoll ist ein reines Anfügeprotokoll.
"""
import hashlib
from datetime import date

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.utils import timezone

BENUTZER = settings.AUTH_USER_MODEL


class DmsGesperrt(Exception):
    """Unzulässige Änderung oder Löschung eines archivierten Dokuments."""


class Dokumentart(models.TextChoices):
    EINGANGSRECHNUNG = "ER", "Eingangsrechnung"
    AUSGANGSRECHNUNG = "AR", "Ausgangsrechnung"
    VERTRAG = "VERTRAG", "Vertrag"
    LIEFERSCHEIN = "LIEFERSCHEIN", "Lieferschein"
    SONSTIGES = "SONSTIGES", "Sonstiges"


# Aufbewahrungsfristen in Jahren (§ 147 AO / § 257 HGB, Stand Bürokratieentlastungsgesetz IV ab 2025:
# Buchungsbelege 8 Jahre, Handels- und Geschäftsbriefe 6 Jahre). Verträge konservativ 10 Jahre.
# Vor Produktivbetrieb mit dem Steuerberater abstimmen; Übersteuerung über settings.ERP_DMS_AUFBEWAHRUNG.
AUFBEWAHRUNG_STANDARD = {"ER": 8, "AR": 8, "VERTRAG": 10, "LIEFERSCHEIN": 6, "SONSTIGES": 6}


def aufbewahrung_jahre(art):
    return {**AUFBEWAHRUNG_STANDARD, **getattr(settings, "ERP_DMS_AUFBEWAHRUNG", {})}.get(art, 10)


def aufbewahren_bis(art, stichtag):
    """Frist beginnt mit dem Schluss des Kalenderjahres, in dem das Dokument entstanden ist (§ 147 Abs. 4 AO)."""
    return date(stichtag.year + aufbewahrung_jahre(art), 12, 31)


class Tag(models.Model):
    name = models.CharField("Name", max_length=60, unique=True)
    farbe = models.CharField("Farbe", max_length=20, default="secondary",
                             choices=[(f, f) for f in ("primary", "secondary", "success", "danger", "warning",
                                                       "info", "dark")])

    class Meta:
        verbose_name = "Schlagwort"
        verbose_name_plural = "Schlagwörter"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Stapel(models.Model):
    """Verarbeitungsstapel: ein Mehrfach-Upload oder ein Lauf des Eingangsordners."""

    class Quelle(models.TextChoices):
        UPLOAD = "UPLOAD", "Mehrfach-Upload"
        EINGANGSORDNER = "EINGANGSORDNER", "Eingangsordner (Hotfolder)"
        TRENNUNG = "TRENNUNG", "Trennung Sammel-PDF"

    nummer = models.CharField("Stapelnummer", max_length=30, unique=True)
    bezeichnung = models.CharField("Bezeichnung", max_length=200, blank=True)
    quelle = models.CharField("Quelle", max_length=20, choices=Quelle.choices, default=Quelle.UPLOAD)
    art = models.CharField("Dokumentart (Vorgabe)", max_length=15, choices=Dokumentart.choices,
                           default=Dokumentart.EINGANGSRECHNUNG)
    automatisch_trennen = models.BooleanField("Sammel-PDFs an Trennblättern teilen", default=True)
    erstellt_von = models.ForeignKey(BENUTZER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    erstellt_am = models.DateTimeField("Erstellt am", default=timezone.now)

    class Meta:
        verbose_name = "Stapel"
        verbose_name_plural = "Stapel"
        ordering = ["-erstellt_am", "-pk"]

    def __str__(self):
        return f"{self.nummer} {self.bezeichnung}".strip()

    def fortschritt(self):
        """{status: anzahl}, gesamt, fertig (nicht mehr in Warteschlange/OCR) und Prozent."""
        zaehlung = dict(self.dokumente.values_list("status").annotate(n=models.Count("pk")))
        gesamt = sum(zaehlung.values())
        offen = zaehlung.get(Dokument.Status.NEU, 0) + zaehlung.get(Dokument.Status.OCR, 0)
        fertig = gesamt - offen
        return {"zaehlung": zaehlung, "gesamt": gesamt, "fertig": fertig, "offen": offen,
                "prozent": round(100 * fertig / gesamt) if gesamt else 100}


class Dokument(models.Model):
    class Status(models.TextChoices):
        NEU = "NEU", "Neu (in Warteschlange)"
        OCR = "OCR", "In OCR"
        ERKANNT = "ERKANNT", "Erkannt"
        PRUEFEN = "PRUEFEN", "Zu prüfen"
        GEBUCHT = "GEBUCHT", "Gebucht"
        ABGELEGT = "ABGELEGT", "Abgelegt"
        FEHLER = "FEHLER", "Fehler"

    # Nach der Archivierung unveränderbare Spalten (auch per Trigger geschützt)
    EINGEFROREN = ("nummer", "art", "ocr_text", "archiviert_am", "erstellt_am")

    nummer = models.CharField("Dokumentnummer", max_length=30, unique=True)
    titel = models.CharField("Titel", max_length=200)
    art = models.CharField("Dokumentart", max_length=15, choices=Dokumentart.choices,
                           default=Dokumentart.SONSTIGES)
    status = models.CharField("Verarbeitungsstatus", max_length=10, choices=Status.choices, default=Status.NEU,
                              db_index=True)
    stapel = models.ForeignKey(Stapel, null=True, blank=True, on_delete=models.PROTECT, related_name="dokumente")
    eltern = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="teile",
                               verbose_name="Aufgeteilt aus (Sammel-PDF)")
    seiten_von = models.PositiveIntegerField("Seiten im Sammel-PDF von", null=True, blank=True)
    seiten_bis = models.PositiveIntegerField("Seiten im Sammel-PDF bis", null=True, blank=True)
    aufgeteilt = models.BooleanField("In Einzeldokumente aufgeteilt", default=False)
    partner = models.ForeignKey("stammdaten.Geschaeftspartner", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="dokumente", verbose_name="Geschäftspartner")
    tags = models.ManyToManyField(Tag, blank=True, related_name="dokumente", verbose_name="Schlagwörter")
    belegdatum = models.DateField("Dokumentdatum", null=True, blank=True)
    bemerkung = models.TextField("Bemerkung", blank=True)
    metadaten = models.JSONField("Weitere Metadaten", default=dict, blank=True)
    # Verknüpfungen zu bestehenden Belegen/Buchungen (Rückverweis Dokument ↔ Buchung)
    beleg = models.ForeignKey("belege.Beleg", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="dms_dokumente", verbose_name="Beleg")
    buchung = models.ForeignKey("fibu.Buchung", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="dms_dokumente", verbose_name="Buchung")
    eingangsrechnung = models.ForeignKey("fibu.Eingangsrechnung", null=True, blank=True, on_delete=models.PROTECT,
                                         related_name="dms_dokumente", verbose_name="Eingangsrechnung")
    # OCR / Erkennung
    seiten = models.PositiveIntegerField("Seiten", default=0)
    textquelle = models.CharField("Textquelle", max_length=12, blank=True,
                                  help_text="zugferd, xrechnung, textlayer, ocr")
    ocr_text = models.TextField("Volltext (OCR/Textlayer)", blank=True)
    suchvektor = SearchVectorField(null=True, editable=False)
    erkennung = models.JSONField("Erkannte Felder", default=dict, blank=True)
    konfidenz = models.DecimalField("Gesamtkonfidenz", max_digits=4, decimal_places=2, null=True, blank=True)
    fehlermeldung = models.TextField("Fehlermeldung", blank=True)
    # Revisionssicherheit
    archiviert = models.BooleanField("Archiviert (revisionssicher)", default=False)
    archiviert_am = models.DateTimeField("Archiviert am", null=True, blank=True)
    archiviert_von = models.ForeignKey(BENUTZER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    aufbewahren_bis = models.DateField("Aufbewahren bis", null=True, blank=True)
    erstellt_von = models.ForeignKey(BENUTZER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    erstellt_am = models.DateTimeField("Eingang", default=timezone.now)

    class Meta:
        verbose_name = "Dokument"
        verbose_name_plural = "Dokumente"
        ordering = ["-erstellt_am", "-pk"]
        indexes = [GinIndex(fields=["suchvektor"], name="dms_dokument_suche")]

    def __str__(self):
        return f"{self.nummer} {self.titel}"

    @property
    def original(self):
        return self.versionen.filter(art=DokumentVersion.Art.ORIGINAL).order_by("-version", "-pk").first()

    @property
    def anzeigeversion(self):
        """Durchsuchbares PDF/A der aktuellen Version, sonst das Original."""
        orig = self.original
        if not orig:
            return None
        return orig.ableitungen.order_by("-pk").first() or orig

    @property
    def erkannte_felder(self):
        return (self.erkennung or {}).get("felder", {})

    def wert(self, feld):
        return (self.erkannte_felder.get(feld) or {}).get("wert")

    def save(self, *args, **kwargs):
        if self.pk and not getattr(self, "_archivierung", False):
            alt = Dokument.objects.filter(pk=self.pk).values("archiviert", "aufbewahren_bis",
                                                             *self.EINGEFROREN).first()
            if alt and alt["archiviert"]:
                if not self.archiviert:
                    raise DmsGesperrt(f"Dokument {self.nummer} ist archiviert – Archivierung ist nicht umkehrbar.")
                for feld in self.EINGEFROREN:
                    if getattr(self, feld) != alt[feld]:
                        raise DmsGesperrt(f"Dokument {self.nummer} ist archiviert – „{feld}“ ist unveränderbar.")
                if alt["aufbewahren_bis"] and (not self.aufbewahren_bis
                                               or self.aufbewahren_bis < alt["aufbewahren_bis"]):
                    raise DmsGesperrt("Die Aufbewahrungsfrist kann nur verlängert werden.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if Dokument.objects.filter(pk=self.pk, archiviert=True).exists():
            raise DmsGesperrt(f"Dokument {self.nummer} ist archiviert und darf nicht gelöscht werden.")
        return super().delete(*args, **kwargs)


def _ablage_pfad(instanz, dateiname):
    jetzt = timezone.localtime()
    return f"dms/{jetzt:%Y/%m}/{instanz.sha256[:2]}/{instanz.sha256}_{dateiname[-80:]}"


class DokumentVersion(models.Model):
    """Unveränderbare Dateiversion (nur INSERT). Ableitungen (PDF/A aus OCR) verweisen auf ihr Original."""

    class Art(models.TextChoices):
        ORIGINAL = "ORIGINAL", "Original (Eingang)"
        PDFA = "PDFA", "Durchsuchbares PDF/A (OCR)"

    dokument = models.ForeignKey(Dokument, on_delete=models.CASCADE, related_name="versionen")
    version = models.PositiveIntegerField("Version", default=1)
    art = models.CharField("Art", max_length=10, choices=Art.choices, default=Art.ORIGINAL)
    quelle = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="ableitungen",
                               verbose_name="Abgeleitet von")
    datei = models.FileField("Datei", upload_to=_ablage_pfad, max_length=255)
    dateiname = models.CharField("Dateiname", max_length=255)
    mime = models.CharField("MIME-Typ", max_length=100, blank=True)
    groesse = models.PositiveBigIntegerField("Größe (Bytes)", default=0)
    sha256 = models.CharField("SHA-256", max_length=64, db_index=True)
    kommentar = models.CharField("Kommentar", max_length=255, blank=True)
    erstellt_von = models.ForeignKey(BENUTZER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    erstellt_am = models.DateTimeField("Erstellt am", default=timezone.now)

    class Meta:
        verbose_name = "Dokumentversion"
        verbose_name_plural = "Dokumentversionen"
        ordering = ["dokument", "version", "pk"]
        unique_together = [("dokument", "version", "art")]

    def __str__(self):
        return f"{self.dokument.nummer} V{self.version} {self.get_art_display()}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise DmsGesperrt("Dokumentversionen sind unveränderbar – bitte eine neue Version anlegen.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if Dokument.objects.filter(pk=self.dokument_id, archiviert=True).exists():
            raise DmsGesperrt("Versionen archivierter Dokumente dürfen nicht gelöscht werden.")
        return super().delete(*args, **kwargs)

    def hash_pruefen(self):
        """True, wenn die gespeicherte Datei noch exakt dem versiegelten SHA-256 entspricht."""
        h = hashlib.sha256()
        with self.datei.open("rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest() == self.sha256


class DokumentProtokoll(models.Model):
    """Anfügeprotokoll je Dokument (Eingang, OCR, Korrekturen, Verbuchung, Archivierung …)."""

    dokument = models.ForeignKey(Dokument, on_delete=models.CASCADE, related_name="protokoll")
    zeitpunkt = models.DateTimeField(default=timezone.now)
    benutzer = models.ForeignKey(BENUTZER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    benutzername = models.CharField(max_length=150, blank=True)
    aktion = models.CharField("Aktion", max_length=40)
    text = models.TextField("Text", blank=True)

    class Meta:
        verbose_name = "Dokumentprotokoll"
        verbose_name_plural = "Dokumentprotokoll"
        ordering = ["zeitpunkt", "pk"]

    def __str__(self):
        return f"{self.zeitpunkt:%d.%m.%Y %H:%M} {self.aktion}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise DmsGesperrt("Protokolleinträge sind unveränderbar.")
        super().save(*args, **kwargs)


class Kontierungsregel(models.Model):
    """Lieferantenspezifische Lernregel: Kontierungsvorschlag aus früheren Buchungen bzw. manueller Vorgabe.

    Ohne Stichwort gilt die Regel für alle Positionen des Lieferanten; mit Stichwort nur für Positionen,
    deren Beschreibung das Stichwort enthält (hat Vorrang)."""

    partner = models.ForeignKey("stammdaten.Geschaeftspartner", on_delete=models.CASCADE,
                                related_name="dms_kontierungsregeln", verbose_name="Lieferant",
                                limit_choices_to={"ist_lieferant": True})
    stichwort = models.CharField("Stichwort in der Positionsbeschreibung", max_length=60, blank=True)
    konto = models.ForeignKey("stammdaten.Konto", on_delete=models.PROTECT, related_name="+",
                              verbose_name="Aufwandskonto")
    steuerschluessel = models.CharField("Steuerschlüssel", max_length=10, blank=True)
    anzahl = models.PositiveIntegerField("Anzahl Verwendungen", default=0)
    gelernt = models.BooleanField("Automatisch gelernt", default=True)
    zuletzt = models.DateTimeField("Zuletzt verwendet", default=timezone.now)

    class Meta:
        verbose_name = "DMS-Kontierungsregel"
        verbose_name_plural = "DMS-Kontierungsregeln (Lernregeln)"
        ordering = ["partner__name", "stichwort", "-anzahl"]
        unique_together = [("partner", "stichwort", "konto", "steuerschluessel")]

    def __str__(self):
        return f"{self.partner.name} {self.stichwort or '*'} → {self.konto}"

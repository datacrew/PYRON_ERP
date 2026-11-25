"""Finanzbuchhaltung (Phase 4): Hauptbuch, Nebenbücher, Bank, Mahnwesen.

Beträge in Euro mit 2 Nachkommastellen. Saldo-Konvention: Soll − Haben (positiv = Sollsaldo).
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.bankdaten import bic_validator, iban_validator
from apps.stammdaten.models import Artikel, Geschaeftspartner, Konto

NULL = Decimal("0.00")
BENUTZER = settings.AUTH_USER_MODEL


class FibuGesperrt(Exception):
    """Änderung einer festgeschriebenen Buchung oder Buchung in gesperrter Periode."""


# ------------------------------------------------------------------ Geschäftsjahr / Perioden

class Geschaeftsjahr(models.Model):
    jahr = models.PositiveIntegerField("Geschäftsjahr", unique=True)
    beginn = models.DateField("Beginn")
    ende = models.DateField("Ende")
    abgeschlossen = models.BooleanField("Abgeschlossen", default=False)
    saldenvortrag_am = models.DateTimeField("Saldenvortrag ins Folgejahr am", null=True, blank=True)

    class Meta:
        verbose_name = "Geschäftsjahr"
        verbose_name_plural = "Geschäftsjahre"
        ordering = ["-jahr"]

    def __str__(self):
        return f"GJ {self.jahr}"


class Periode(models.Model):
    geschaeftsjahr = models.ForeignKey(Geschaeftsjahr, on_delete=models.CASCADE, related_name="perioden")
    nummer = models.PositiveSmallIntegerField("Periode")
    beginn = models.DateField("Beginn")
    ende = models.DateField("Ende")
    gesperrt = models.BooleanField("Gesperrt", default=False)
    gesperrt_von = models.ForeignKey(BENUTZER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    gesperrt_am = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Buchungsperiode"
        verbose_name_plural = "Buchungsperioden"
        ordering = ["beginn"]
        unique_together = [("geschaeftsjahr", "nummer")]

    def __str__(self):
        return f"{self.nummer:02d}/{self.geschaeftsjahr.jahr}"


# ------------------------------------------------------------------ Steuerschlüssel / Kontierung

class Steuerschluessel(models.Model):
    class Art(models.TextChoices):
        UST = "UST", "Umsatzsteuer (Ausgang)"
        VST = "VST", "Vorsteuer (Eingang)"
        IGE = "IGE", "Innergemeinschaftlicher Erwerb"
        RC = "RC", "Reverse Charge § 13b UStG"
        IGL = "IGL", "Innergemeinschaftliche Lieferung (steuerfrei)"
        FREI = "FREI", "Steuerfrei / nicht steuerbar"

    kontenrahmen = models.CharField("Kontenrahmen", max_length=5, choices=Konto.KONTENRAHMEN)
    code = models.CharField("Schlüssel", max_length=10)
    bezeichnung = models.CharField("Bezeichnung", max_length=120)
    art = models.CharField("Art", max_length=5, choices=Art.choices)
    satz = models.DecimalField("Steuersatz %", max_digits=5, decimal_places=2, default=0)
    steuerkonto = models.ForeignKey(Konto, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                    verbose_name="Steuerkonto (USt bzw. VSt)")
    gegenkonto = models.ForeignKey(Konto, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                   verbose_name="Gegen-Steuerkonto (USt bei igE/§ 13b)",
                                   help_text="Nur igE/§ 13b: Umsatzsteuer, die der Vorsteuer gegenübersteht")
    kz_bemessung = models.CharField("UStVA-Kz Bemessungsgrundlage", max_length=4, blank=True)
    kz_steuer = models.CharField("UStVA-Kz Steuer", max_length=4, blank=True)
    kz_vorsteuer = models.CharField("UStVA-Kz Vorsteuer", max_length=4, blank=True)
    datev_bu = models.CharField("DATEV BU-Schlüssel", max_length=4, blank=True)
    aktiv = models.BooleanField("Aktiv", default=True)

    class Meta:
        verbose_name = "Steuerschlüssel"
        verbose_name_plural = "Steuerschlüssel"
        ordering = ["kontenrahmen", "code"]
        unique_together = [("kontenrahmen", "code")]

    def __str__(self):
        return f"{self.code} – {self.bezeichnung}"

    @property
    def zwei_seitig(self):
        """igE und § 13b: Steuer wird gleichzeitig als USt geschuldet und als VSt abgezogen."""
        return self.art in (self.Art.IGE, self.Art.RC)


class Kontierungsregel(models.Model):
    class Vorgang(models.TextChoices):
        ERLOES = "ERLOES", "Erlöse (Ausgangsrechnung)"
        AUFWAND = "AUFWAND", "Aufwand (Eingangsrechnung ohne Bestellung)"
        BESTAND = "BESTAND", "Bestandskonto (Wareneingang / Entnahme / Fertigmeldung)"
        MATERIAL = "MATERIAL", "Materialaufwand (Entnahme für Fertigung)"
        BESTANDSVERAENDERUNG = "BV", "Bestandsveränderung (Fertigmeldung)"
        SKONTO_GEWAEHRT = "SKONTO_G", "Gewährte Skonti"
        SKONTO_ERHALTEN = "SKONTO_E", "Erhaltene Skonti"

    kontenrahmen = models.CharField("Kontenrahmen", max_length=5, choices=Konto.KONTENRAHMEN)
    vorgang = models.CharField("Vorgang", max_length=10, choices=Vorgang.choices)
    artikelart = models.CharField("Artikelart", max_length=20, choices=Artikel.Art.choices, blank=True,
                                  help_text="Leer = alle Artikelarten")
    steuerschluessel = models.CharField("Steuerschlüssel", max_length=10, blank=True,
                                        help_text="Leer = alle Steuerschlüssel")
    konto = models.ForeignKey(Konto, on_delete=models.PROTECT, verbose_name="Konto")

    class Meta:
        verbose_name = "Kontierungsregel"
        verbose_name_plural = "Kontierungsregeln"
        ordering = ["kontenrahmen", "vorgang", "artikelart", "steuerschluessel"]
        unique_together = [("kontenrahmen", "vorgang", "artikelart", "steuerschluessel")]

    def __str__(self):
        return f"{self.get_vorgang_display()} {self.artikelart or '*'}/{self.steuerschluessel or '*'} → {self.konto}"


# ------------------------------------------------------------------ Buchungen (Hauptbuch)

class Buchung(models.Model):
    class Status(models.TextChoices):
        ERFASST = "ERFASST", "Gebucht (nicht festgeschrieben)"
        FESTGESCHRIEBEN = "FESTGESCHRIEBEN", "Festgeschrieben"

    nummer = models.CharField("Buchungsnummer", max_length=30, unique=True)
    periode = models.ForeignKey(Periode, on_delete=models.PROTECT, related_name="buchungen")
    belegdatum = models.DateField("Belegdatum")
    belegnr = models.CharField("Belegnummer", max_length=40)
    text = models.CharField("Buchungstext", max_length=200)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ERFASST)
    quelle = models.CharField("Quelle", max_length=30, blank=True,
                              help_text="Automatik: beleg, wareneingang, eingangsrechnung, bank, …")
    quelle_id = models.CharField("Quell-ID", max_length=60, blank=True)
    storno_von = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="storniert_durch", verbose_name="Storno von")
    erfasst_von = models.ForeignKey(BENUTZER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    erfasst_am = models.DateTimeField(default=timezone.now)
    festgeschrieben_von = models.ForeignKey(BENUTZER, null=True, blank=True, on_delete=models.SET_NULL,
                                            related_name="+")
    festgeschrieben_am = models.DateTimeField(null=True, blank=True)
    ketten_index = models.PositiveIntegerField(null=True, blank=True, unique=True)
    vorgaenger_pruefsumme = models.CharField(max_length=64, blank=True)
    pruefsumme = models.CharField(max_length=64, blank=True)

    class Meta:
        verbose_name = "Buchung"
        verbose_name_plural = "Buchungsjournal"
        ordering = ["-belegdatum", "-pk"]
        constraints = [models.UniqueConstraint(fields=["quelle", "quelle_id"], condition=~models.Q(quelle=""),
                                               name="fibu_buchung_quelle_eindeutig")]

    def __str__(self):
        return f"{self.nummer} {self.text}"

    @property
    def festgeschrieben(self):
        return self.status == self.Status.FESTGESCHRIEBEN

    @property
    def summe(self):
        return sum((z.soll for z in self.zeilen.all()), NULL)

    def save(self, *args, **kwargs):
        if self.pk and not getattr(self, "_festschreibung", False):
            alt = Buchung.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if alt == self.Status.FESTGESCHRIEBEN:
                raise FibuGesperrt(f"Buchung {self.nummer} ist festgeschrieben – Korrektur nur per Storno.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status == self.Status.FESTGESCHRIEBEN:
            raise FibuGesperrt(f"Buchung {self.nummer} ist festgeschrieben und darf nicht gelöscht werden.")
        return super().delete(*args, **kwargs)


class Buchungszeile(models.Model):
    buchung = models.ForeignKey(Buchung, on_delete=models.CASCADE, related_name="zeilen")
    position = models.PositiveSmallIntegerField("Pos.", default=1)
    konto = models.ForeignKey(Konto, on_delete=models.PROTECT, related_name="buchungszeilen")
    soll = models.DecimalField("Soll", max_digits=14, decimal_places=2, default=0)
    haben = models.DecimalField("Haben", max_digits=14, decimal_places=2, default=0)
    text = models.CharField("Text", max_length=200, blank=True)
    steuerschluessel = models.ForeignKey(Steuerschluessel, null=True, blank=True, on_delete=models.PROTECT,
                                         related_name="+", verbose_name="Steuerschlüssel")
    ist_steuer = models.BooleanField("Automatische Steuerzeile", default=False)
    zu_zeile = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE,
                                 related_name="steuerzeilen", verbose_name="Steuer zu Zeile")
    partner = models.ForeignKey(Geschaeftspartner, null=True, blank=True, on_delete=models.PROTECT,
                                related_name="+")

    class Meta:
        verbose_name = "Buchungszeile"
        verbose_name_plural = "Buchungszeilen"
        ordering = ["buchung", "position", "pk"]

    def __str__(self):
        return f"{self.konto.nummer} S {self.soll} H {self.haben}"

    @property
    def betrag(self):
        return self.soll - self.haben

    def save(self, *args, **kwargs):
        if Buchung.objects.filter(pk=self.buchung_id, status=Buchung.Status.FESTGESCHRIEBEN).exists():
            raise FibuGesperrt("Zeilen festgeschriebener Buchungen sind unveränderbar.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if Buchung.objects.filter(pk=self.buchung_id, status=Buchung.Status.FESTGESCHRIEBEN).exists():
            raise FibuGesperrt("Zeilen festgeschriebener Buchungen sind unveränderbar.")
        return super().delete(*args, **kwargs)


class AutomatikProtokoll(models.Model):
    """Ergebnis automatischer Buchungen aus den Vorsystemen – Fehler bleiben zum Nachbuchen sichtbar."""
    quelle = models.CharField(max_length=30)
    quelle_id = models.CharField(max_length=60)
    erfolgreich = models.BooleanField(default=False)
    meldung = models.CharField(max_length=500, blank=True)
    buchung = models.ForeignKey(Buchung, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    zeitpunkt = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Automatik-Protokoll"
        verbose_name_plural = "Automatik-Protokoll"
        ordering = ["-zeitpunkt"]
        unique_together = [("quelle", "quelle_id")]

    def __str__(self):
        return f"{self.quelle} {self.quelle_id}: {'OK' if self.erfolgreich else self.meldung}"


# ------------------------------------------------------------------ Eingangsrechnungen

class Eingangsrechnung(models.Model):
    class Status(models.TextChoices):
        ERFASST = "ERFASST", "Erfasst"
        GEBUCHT = "GEBUCHT", "Gebucht"
        STORNIERT = "STORNIERT", "Storniert"

    nummer = models.CharField("Interne Nummer", max_length=30, unique=True)
    lieferant = models.ForeignKey(Geschaeftspartner, on_delete=models.PROTECT, related_name="eingangsrechnungen",
                                  limit_choices_to={"ist_lieferant": True})
    rechnungsnummer = models.CharField("Rechnungsnummer des Lieferanten", max_length=60)
    rechnungsdatum = models.DateField("Rechnungsdatum")
    eingangsdatum = models.DateField("Eingangsdatum", default=timezone.localdate)
    faellig_am = models.DateField("Fällig am", null=True, blank=True)
    bestellung = models.ForeignKey("einkauf.Bestellung", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="eingangsrechnungen")
    zahlungsbedingung = models.ForeignKey("stammdaten.Zahlungsbedingung", null=True, blank=True,
                                          on_delete=models.PROTECT)
    # Skonto laut Rechnung (z. B. aus „14 Tage 2 % Skonto“ oder ZUGFeRD/XRechnung BT-20) – hat Vorrang vor der
    # Zahlungsbedingung des Lieferanten und wird in den offenen Posten übernommen.
    skonto_prozent = models.DecimalField("Skontosatz %", max_digits=5, decimal_places=2, default=0)
    skonto_tage = models.PositiveSmallIntegerField("Skontofrist (Tage)", null=True, blank=True)
    skonto_bis = models.DateField("Skonto bis", null=True, blank=True)
    skontobetrag = models.DecimalField("Skontobetrag", max_digits=14, decimal_places=2, default=0)
    summe_netto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    summe_steuer = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    summe_brutto = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ERFASST)
    pruefung = models.JSONField("Ergebnis Rechnungsprüfung", default=list, blank=True)
    abweichung = models.BooleanField("Abweichung festgestellt", default=False)
    abweichung_akzeptiert = models.BooleanField("Abweichung freigegeben", default=False)
    buchung = models.OneToOneField(Buchung, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    erfasst_von = models.ForeignKey(BENUTZER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = "Eingangsrechnung"
        verbose_name_plural = "Eingangsrechnungen"
        ordering = ["-rechnungsdatum", "-pk"]
        unique_together = [("lieferant", "rechnungsnummer")]

    def __str__(self):
        return f"{self.nummer} ({self.rechnungsnummer})"


class ERPosition(models.Model):
    rechnung = models.ForeignKey(Eingangsrechnung, on_delete=models.CASCADE, related_name="positionen")
    position = models.PositiveSmallIntegerField(default=10)
    bestellposition = models.ForeignKey("einkauf.BestellPosition", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="+")
    artikel = models.ForeignKey(Artikel, null=True, blank=True, on_delete=models.PROTECT)
    bezeichnung = models.CharField(max_length=200, blank=True)
    menge = models.DecimalField(max_digits=12, decimal_places=3, default=1)
    einzelpreis = models.DecimalField("Einzelpreis netto", max_digits=12, decimal_places=4, default=0)
    steuerschluessel = models.ForeignKey(Steuerschluessel, on_delete=models.PROTECT, related_name="+")
    konto = models.ForeignKey(Konto, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                              help_text="Nur ohne Bestellbezug: Aufwands-/Anlagekonto (sonst Kontierungsregel)")
    betrag_netto = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        ordering = ["position", "pk"]

    def save(self, *args, **kwargs):
        self.betrag_netto = (self.menge * self.einzelpreis).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)


# ------------------------------------------------------------------ Offene Posten

class OffenerPosten(models.Model):
    class Art(models.TextChoices):
        DEBITOR = "D", "Debitor (Forderung)"
        KREDITOR = "K", "Kreditor (Verbindlichkeit)"

    art = models.CharField(max_length=1, choices=Art.choices)
    partner = models.ForeignKey(Geschaeftspartner, on_delete=models.PROTECT, related_name="offene_posten")
    konto = models.ForeignKey(Konto, on_delete=models.PROTECT, related_name="+", verbose_name="Personenkonto")
    buchung = models.ForeignKey(Buchung, on_delete=models.PROTECT, related_name="offene_posten")
    beleg = models.ForeignKey("belege.Beleg", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    eingangsrechnung = models.ForeignKey(Eingangsrechnung, null=True, blank=True, on_delete=models.PROTECT,
                                         related_name="offene_posten")
    belegnr = models.CharField("Belegnummer", max_length=60)
    fremdbelegnr = models.CharField("Fremdbelegnummer", max_length=60, blank=True)
    belegdatum = models.DateField()
    faellig_am = models.DateField("Fällig am")
    skonto_bis = models.DateField("Skonto bis", null=True, blank=True)
    skonto_prozent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    skontobetrag = models.DecimalField("Skontobetrag", max_digits=14, decimal_places=2, default=0,
                                       help_text="Möglicher Skontoabzug bei Zahlung bis „Skonto bis“ (0 = Satz × Betrag)")
    betrag = models.DecimalField("Betrag brutto", max_digits=14, decimal_places=2)
    offen = models.DecimalField("Offen", max_digits=14, decimal_places=2)
    steuer_aufteilung = models.JSONField(default=list, blank=True,
                                         help_text="[[Steuerschlüssel, netto, steuer], …] für Skonto-Korrektur")
    mahnsperre = models.BooleanField("Mahnsperre", default=False)
    mahnstufe = models.PositiveSmallIntegerField("Mahnstufe", default=0)
    letzte_mahnung = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = "Offener Posten"
        verbose_name_plural = "Offene Posten"
        ordering = ["faellig_am", "pk"]

    def __str__(self):
        return f"{self.belegnr} {self.partner.name} offen {self.offen}"

    @property
    def ausgeglichen(self):
        return self.offen == 0

    def tage_ueberfaellig(self, stichtag=None):
        return ((stichtag or timezone.localdate()) - self.faellig_am).days

    def skonto_betrag(self, datum=None):
        datum = datum or timezone.localdate()
        if self.skonto_prozent and self.skonto_bis and datum <= self.skonto_bis and self.offen == self.betrag:
            # Gespeicherter Skontobetrag (z. B. abweichende Skontobasis laut E-Rechnung) hat Vorrang
            return self.skontobetrag or (self.betrag * self.skonto_prozent / 100).quantize(Decimal("0.01"))
        return NULL


class Ausgleich(models.Model):
    class Art(models.TextChoices):
        ZAHLUNG = "ZAHLUNG", "Zahlung"
        SKONTO = "SKONTO", "Skonto"
        VERRECHNUNG = "VERRECHNUNG", "Verrechnung"
        STORNO = "STORNO", "Storno"
        RUECKLAST = "RUECKLAST", "Rücklastschrift / Rückläufer"

    op = models.ForeignKey(OffenerPosten, on_delete=models.PROTECT, related_name="ausgleiche")
    buchung = models.ForeignKey(Buchung, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    datum = models.DateField()
    art = models.CharField(max_length=12, choices=Art.choices)
    betrag = models.DecimalField(max_digits=14, decimal_places=2)
    benutzer = models.ForeignKey(BENUTZER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = "OP-Ausgleich"
        verbose_name_plural = "OP-Ausgleiche"
        ordering = ["datum", "pk"]


# ------------------------------------------------------------------ Bank

class Bankkonto(models.Model):
    bezeichnung = models.CharField(max_length=100)
    iban = models.CharField("IBAN", max_length=34, unique=True, validators=[iban_validator])
    bic = models.CharField("BIC", max_length=11, blank=True, validators=[bic_validator])
    konto = models.ForeignKey(Konto, on_delete=models.PROTECT, verbose_name="Sachkonto")
    aktiv = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Bankkonto"
        verbose_name_plural = "Bankkonten"
        ordering = ["bezeichnung"]

    def __str__(self):
        return f"{self.bezeichnung} ({self.iban})"


class Bankimport(models.Model):
    nummer = models.CharField(max_length=30, unique=True)
    bankkonto = models.ForeignKey(Bankkonto, on_delete=models.PROTECT, related_name="importe")
    format = models.CharField(max_length=10, choices=[("CAMT053", "CAMT.053"), ("MT940", "MT940")])
    dateiname = models.CharField(max_length=200)
    datei_hash = models.CharField("SHA-256 der Datei", max_length=64, unique=True)
    von = models.DateField(null=True, blank=True)
    bis = models.DateField(null=True, blank=True)
    anfangssaldo = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    endsaldo = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    anzahl_neu = models.PositiveIntegerField(default=0)
    anzahl_dubletten = models.PositiveIntegerField(default=0)
    importiert_von = models.ForeignKey(BENUTZER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    importiert_am = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Bankimport"
        verbose_name_plural = "Bankimporte"
        ordering = ["-importiert_am"]

    def __str__(self):
        return f"{self.nummer} {self.dateiname}"


class Bankumsatz(models.Model):
    class Status(models.TextChoices):
        OFFEN = "OFFEN", "Nicht zugeordnet"
        VERRECHNUNG = "VERRECHNUNG", "Auf Verrechnungskonto (ungeklärt)"
        GEBUCHT = "GEBUCHT", "Zugeordnet und gebucht"

    bankimport = models.ForeignKey(Bankimport, on_delete=models.PROTECT, related_name="umsaetze")
    bankkonto = models.ForeignKey(Bankkonto, on_delete=models.PROTECT, related_name="umsaetze")
    buchungsdatum = models.DateField()
    valuta = models.DateField(null=True, blank=True)
    betrag = models.DecimalField("Betrag (+ Eingang / − Ausgang)", max_digits=14, decimal_places=2)
    waehrung = models.CharField(max_length=3, default="EUR")
    name = models.CharField("Auftraggeber/Empfänger", max_length=200, blank=True)
    iban = models.CharField("IBAN Gegenseite", max_length=34, blank=True)
    verwendungszweck = models.TextField(blank=True)
    referenz = models.CharField("Referenz (EndToEnd/Bank)", max_length=100, blank=True)
    gvc = models.CharField("Geschäftsvorfall-Code", max_length=40, blank=True)
    sammlerreferenz = models.CharField("Sammler-Referenz (PmtInfId/MsgId)", max_length=100, blank=True)
    rueckgabegrund = models.CharField("Rückgabegrund (ISO-Code)", max_length=10, blank=True,
                                      help_text="z. B. AC04, MD06, AM04 bei Rücklastschriften")
    dubletten_schluessel = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OFFEN)
    buchung = models.ForeignKey(Buchung, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    zuordnung_buchung = models.ForeignKey(Buchung, null=True, blank=True, on_delete=models.PROTECT,
                                          related_name="+", help_text="Spätere Zuordnung vom Verrechnungskonto")

    class Meta:
        verbose_name = "Bankumsatz"
        verbose_name_plural = "Bankumsätze"
        ordering = ["-buchungsdatum", "-pk"]

    def __str__(self):
        return f"{self.buchungsdatum:%d.%m.%Y} {self.betrag} {self.name}"


# ------------------------------------------------------------------ Mahnwesen

class Basiszinssatz(models.Model):
    """Basiszinssatz nach § 247 BGB (halbjährlich, Deutsche Bundesbank) – Einstellung für Verzugszinsen."""
    gueltig_ab = models.DateField("Gültig ab", unique=True)
    satz = models.DecimalField("Basiszinssatz %", max_digits=5, decimal_places=2)

    class Meta:
        verbose_name = "Basiszinssatz"
        verbose_name_plural = "Basiszinssätze"
        ordering = ["-gueltig_ab"]

    def __str__(self):
        return f"ab {self.gueltig_ab:%d.%m.%Y}: {self.satz} %"


class Mahnstufe(models.Model):
    stufe = models.PositiveSmallIntegerField("Stufe", unique=True)
    bezeichnung = models.CharField(max_length=60)
    tage = models.PositiveIntegerField("Tage", help_text="Stufe 1: Tage nach Fälligkeit; sonst Tage seit letzter Mahnung")
    frist_tage = models.PositiveIntegerField("Zahlungsfrist (Tage)", default=10)
    gebuehr = models.DecimalField("Mahngebühr €", max_digits=8, decimal_places=2, default=0)
    zinsen = models.BooleanField("Verzugszinsen berechnen", default=False)
    text = models.TextField("Mahntext", blank=True)

    class Meta:
        verbose_name = "Mahnstufe"
        verbose_name_plural = "Mahnstufen"
        ordering = ["stufe"]

    def __str__(self):
        return f"{self.stufe}: {self.bezeichnung}"


class Mahnlauf(models.Model):
    class Status(models.TextChoices):
        VORSCHLAG = "VORSCHLAG", "Vorschlag"
        AUSGEFUEHRT = "AUSGEFUEHRT", "Ausgeführt"

    nummer = models.CharField(max_length=30, unique=True)
    stichtag = models.DateField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.VORSCHLAG)
    erstellt_von = models.ForeignKey(BENUTZER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    erstellt_am = models.DateTimeField(default=timezone.now)
    ausgefuehrt_am = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Mahnlauf"
        verbose_name_plural = "Mahnläufe"
        ordering = ["-stichtag", "-pk"]

    def __str__(self):
        return self.nummer


class Mahnung(models.Model):
    mahnlauf = models.ForeignKey(Mahnlauf, on_delete=models.CASCADE, related_name="mahnungen")
    partner = models.ForeignKey(Geschaeftspartner, on_delete=models.PROTECT, related_name="mahnungen")
    stufe = models.ForeignKey(Mahnstufe, on_delete=models.PROTECT)
    summe_offen = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    gebuehr = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    zinsen = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    frist = models.DateField("Zahlungsfrist")
    zinssatz = models.DecimalField("Verzugszinssatz % p. a.", max_digits=5, decimal_places=2, default=0)

    class Meta:
        verbose_name = "Mahnung"
        verbose_name_plural = "Mahnungen"
        ordering = ["partner__name"]

    def __str__(self):
        return f"{self.mahnlauf.nummer} {self.partner.name} ({self.stufe.bezeichnung})"

    @property
    def gesamt(self):
        return self.summe_offen + self.gebuehr + self.zinsen


class MahnPosition(models.Model):
    mahnung = models.ForeignKey(Mahnung, on_delete=models.CASCADE, related_name="positionen")
    op = models.ForeignKey(OffenerPosten, on_delete=models.PROTECT, related_name="mahnpositionen")
    stufe = models.PositiveSmallIntegerField()
    offen = models.DecimalField(max_digits=14, decimal_places=2)
    tage_verzug = models.IntegerField()
    zinsen = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        ordering = ["op__faellig_am"]

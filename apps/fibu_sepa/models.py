"""SEPA-Zahlungsverkehr: Mandate, Zahlungs-/Lastschriftläufe, Posten und Rücklastschriften.

Unveränderbarkeit: Sobald ein Lauf exportiert ist (XML erzeugt, Prüfsumme gespeichert, Geldtransit gebucht),
sind Kopf und Posten fachlich eingefroren – geschützt im ORM (save/delete) und per PostgreSQL-Trigger
(Migration 0002). Danach dürfen sich nur noch Status, Abgleich- und Rücklastschrift-Verweise ändern.
"""
from datetime import date

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.bankdaten import bic_validator, iban_validator
from apps.stammdaten.models import Geschaeftspartner

BENUTZER = settings.AUTH_USER_MODEL
MANDAT_LAUFZEIT_MONATE = 36  # Verfall nach 36 Monaten ohne Einzug (SEPA-Rulebook / DK-Bedingungen)


class SepaGesperrt(Exception):
    """Änderung eines exportierten Zahlungslaufs."""


def monate_addieren(d, monate):
    jahr, monat = divmod(d.month - 1 + monate, 12)
    jahr, monat = d.year + jahr, monat + 1
    import calendar

    return date(jahr, monat, min(d.day, calendar.monthrange(jahr, monat)[1]))


class SepaMandat(models.Model):
    class Typ(models.TextChoices):
        CORE = "CORE", "SEPA-Basislastschrift (CORE)"
        B2B = "B2B", "SEPA-Firmenlastschrift (B2B)"

    class Art(models.TextChoices):
        WIEDERKEHREND = "RCUR", "Wiederkehrende Lastschriften"
        EINMALIG = "OOFF", "Einmalige Lastschrift"

    class Status(models.TextChoices):
        AKTIV = "AKTIV", "Aktiv"
        WIDERRUFEN = "WIDERRUFEN", "Widerrufen"
        GESPERRT = "GESPERRT", "Gesperrt (nach Rücklastschrift prüfen)"
        ABGELAUFEN = "ABGELAUFEN", "Verfallen (36 Monate ohne Einzug)"
        BEENDET = "BEENDET", "Beendet (letzte/einmalige Lastschrift eingezogen)"

    partner = models.ForeignKey(Geschaeftspartner, on_delete=models.PROTECT, related_name="sepa_mandate",
                                limit_choices_to={"ist_kunde": True}, verbose_name="Kunde")
    mandatsreferenz = models.CharField("Mandatsreferenz", max_length=35, unique=True, blank=True,
                                       help_text="Leer lassen für automatische Vergabe (Nummernkreis SEPA_MANDAT)")
    typ = models.CharField("Verfahren", max_length=4, choices=Typ.choices, default=Typ.CORE)
    art = models.CharField("Mandatsart", max_length=4, choices=Art.choices, default=Art.WIEDERKEHREND)
    unterschrieben_am = models.DateField("Unterschrieben am")
    kontoinhaber = models.CharField("Kontoinhaber", max_length=70, blank=True,
                                    help_text="Leer = Name des Kunden")
    iban = models.CharField("IBAN des Zahlungspflichtigen", max_length=34, validators=[iban_validator])
    bic = models.CharField("BIC", max_length=11, blank=True, validators=[bic_validator])
    status = models.CharField("Status", max_length=10, choices=Status.choices, default=Status.AKTIV)
    letzte_lastschrift = models.BooleanField("Nächster Einzug ist der letzte (FNAL)", default=False)
    letzte_nutzung = models.DateField("Letzter Einzug", null=True, blank=True)
    widerrufen_am = models.DateField("Widerrufen am", null=True, blank=True)
    bemerkung = models.CharField("Bemerkung", max_length=200, blank=True)
    erstellt_am = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "SEPA-Mandat"
        verbose_name_plural = "SEPA-Mandate"
        ordering = ["partner__name", "-unterschrieben_am"]

    def __str__(self):
        return f"{self.mandatsreferenz} ({self.partner.name})"

    def save(self, *args, **kwargs):
        if not self.mandatsreferenz:
            from apps.core.nummernkreise import naechste_nummer

            self.mandatsreferenz = naechste_nummer("SEPA_MANDAT")
        self.iban = self.iban.replace(" ", "").upper()
        self.bic = self.bic.replace(" ", "").upper()
        super().save(*args, **kwargs)

    @property
    def name_zahlungspflichtiger(self):
        return self.kontoinhaber or self.partner.name

    def verfallsdatum(self):
        """Ein Mandat verfällt, wenn 36 Monate nach dem letzten Einzug (bzw. der Unterschrift) nicht eingezogen wurde."""
        return monate_addieren(self.letzte_nutzung or self.unterschrieben_am, MANDAT_LAUFZEIT_MONATE)

    def ist_verfallen(self, stichtag=None):
        return (stichtag or timezone.localdate()) > self.verfallsdatum()

    def ist_gueltig(self, stichtag=None):
        stichtag = stichtag or timezone.localdate()
        return (self.status == self.Status.AKTIV and self.unterschrieben_am <= stichtag
                and not self.ist_verfallen(stichtag))

    def wurde_genutzt(self):
        """Mindestens ein exportierter, nicht zurückgegebener Einzug → Folgelastschriften RCUR."""
        return self.posten.filter(lauf__exportiert_am__isnull=False, ausgewaehlt=True).exclude(
            lauf__status=Zahlungslauf.Status.STORNIERT).exclude(status=ZahlungslaufPosten.Status.ZURUECK).exists()

    def naechste_sequenz(self):
        """FRST (Erstlastschrift), RCUR (Folge), FNAL (letzte), OOFF (einmalig) – automatisch bestimmt."""
        if self.art == self.Art.EINMALIG:
            return "OOFF"
        if not self.wurde_genutzt():
            return "FRST"
        return "FNAL" if self.letzte_lastschrift else "RCUR"


class Zahlungslauf(models.Model):
    class Art(models.TextChoices):
        UEBERWEISUNG = "UEBERWEISUNG", "Überweisung (pain.001)"
        LASTSCHRIFT = "LASTSCHRIFT", "Lastschrift (pain.008)"

    class Status(models.TextChoices):
        ENTWURF = "ENTWURF", "Entwurf (Vorschlag)"
        FREIGEGEBEN = "FREIGEGEBEN", "Freigegeben"
        EXPORTIERT = "EXPORTIERT", "Exportiert und auf Geldtransit gebucht"
        AUSGEFUEHRT = "AUSGEFUEHRT", "Ausgeführt (mit Kontoauszug abgeglichen)"
        VERWORFEN = "VERWORFEN", "Verworfen"
        STORNIERT = "STORNIERT", "Storniert"

    nummer = models.CharField("Laufnummer", max_length=30, unique=True)
    art = models.CharField("Art", max_length=12, choices=Art.choices)
    status = models.CharField("Status", max_length=12, choices=Status.choices, default=Status.ENTWURF)
    bankkonto = models.ForeignKey("fibu.Bankkonto", on_delete=models.PROTECT, related_name="sepa_laeufe",
                                  verbose_name="Eigenes Bankkonto")
    ausfuehrungsdatum = models.DateField("Ausführungs- bzw. Einzugsdatum")
    beruecksichtigen_bis = models.DateField("Fälligkeiten berücksichtigen bis", null=True, blank=True)
    skonto_nutzen = models.BooleanField("Skonto ziehen", default=True)
    sammelbuchung = models.BooleanField("Sammelbuchung (BatchBooking)", default=True)
    msg_id = models.CharField("Message-ID", max_length=35, blank=True)
    summe = models.DecimalField("Summe", max_digits=14, decimal_places=2, default=0)
    anzahl = models.PositiveIntegerField("Anzahl Zahlungen", default=0)
    xml_inhalt = models.TextField("XML-Datei", blank=True)
    xml_dateiname = models.CharField("Dateiname", max_length=100, blank=True)
    xml_sha256 = models.CharField("SHA-256 der Datei", max_length=64, blank=True)
    xsd_geprueft = models.BooleanField("Gegen XSD geprüft", default=False)
    buchung = models.ForeignKey("fibu.Buchung", null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                verbose_name="Geldtransit-Buchung")
    storno_buchung = models.ForeignKey("fibu.Buchung", null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="+")
    erstellt_von = models.ForeignKey(BENUTZER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    erstellt_am = models.DateTimeField(default=timezone.now)
    freigegeben_von = models.ForeignKey(BENUTZER, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    freigegeben_am = models.DateTimeField(null=True, blank=True)
    exportiert_von = models.ForeignKey(BENUTZER, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    exportiert_am = models.DateTimeField(null=True, blank=True)
    storniert_von = models.ForeignKey(BENUTZER, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    storniert_am = models.DateTimeField(null=True, blank=True)
    bemerkung = models.CharField("Bemerkung", max_length=200, blank=True)

    # Felder, die nach dem Export eingefroren sind (identisch im DB-Trigger)
    EINGEFROREN = ("nummer", "art", "bankkonto_id", "ausfuehrungsdatum", "skonto_nutzen", "sammelbuchung", "msg_id",
                   "summe", "anzahl", "xml_inhalt", "xml_dateiname", "xml_sha256", "xsd_geprueft", "buchung_id",
                   "freigegeben_von_id", "freigegeben_am", "exportiert_von_id", "exportiert_am", "erstellt_von_id",
                   "erstellt_am")

    class Meta:
        verbose_name = "Zahlungslauf"
        verbose_name_plural = "Zahlungsläufe"
        ordering = ["-erstellt_am", "-pk"]

    def __str__(self):
        return f"{self.nummer} ({self.get_art_display()})"

    @property
    def ist_lastschrift(self):
        return self.art == self.Art.LASTSCHRIFT

    @property
    def exportiert(self):
        return self.exportiert_am is not None

    @property
    def bearbeitbar(self):
        return self.status == self.Status.ENTWURF

    def save(self, *args, **kwargs):
        if self.pk and not getattr(self, "_export", False):
            alt = Zahlungslauf.objects.filter(pk=self.pk).first()
            if alt and alt.exportiert_am:
                geaendert = [f for f in self.EINGEFROREN if getattr(alt, f) != getattr(self, f)]
                if geaendert:
                    raise SepaGesperrt(f"Zahlungslauf {self.nummer} ist exportiert und unveränderbar "
                                       f"({', '.join(geaendert)}).")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.exportiert_am or self.status != self.Status.ENTWURF:
            raise SepaGesperrt(f"Zahlungslauf {self.nummer} kann nicht gelöscht werden – bitte verwerfen.")
        return super().delete(*args, **kwargs)

    def ausgewaehlte(self):
        return self.posten.filter(ausgewaehlt=True)


class ZahlungslaufPosten(models.Model):
    class Status(models.TextChoices):
        OFFEN = "OFFEN", "Offen"
        ABGEGLICHEN = "ABGEGLICHEN", "Mit Kontoauszug abgeglichen"
        ZURUECK = "ZURUECK", "Zurückgegeben (Rücklastschrift/Rückläufer)"

    lauf = models.ForeignKey(Zahlungslauf, on_delete=models.CASCADE, related_name="posten")
    op = models.ForeignKey("fibu.OffenerPosten", on_delete=models.PROTECT, related_name="sepa_posten")
    partner = models.ForeignKey(Geschaeftspartner, on_delete=models.PROTECT, related_name="+")
    mandat = models.ForeignKey(SepaMandat, null=True, blank=True, on_delete=models.PROTECT, related_name="posten")
    ausgewaehlt = models.BooleanField("Ausgewählt", default=True)
    name = models.CharField("Empfänger/Zahlungspflichtiger", max_length=70)
    iban = models.CharField("IBAN", max_length=34, blank=True)
    bic = models.CharField("BIC", max_length=11, blank=True)
    offen = models.DecimalField("Offen lt. OP", max_digits=14, decimal_places=2)
    skonto = models.DecimalField("Skonto", max_digits=14, decimal_places=2, default=0)
    betrag = models.DecimalField("Zahlbetrag", max_digits=14, decimal_places=2)
    verwendungszweck = models.CharField("Verwendungszweck", max_length=140)
    end_to_end_id = models.CharField("End-to-End-ID", max_length=35, blank=True)
    pmtinf_id = models.CharField("Payment-Information-ID", max_length=35, blank=True)
    sequenz = models.CharField("Sequenz", max_length=4, blank=True)
    mandatsreferenz = models.CharField("Mandatsreferenz", max_length=35, blank=True)
    mandat_datum = models.DateField("Mandat unterschrieben am", null=True, blank=True)
    lastschrift_typ = models.CharField("Verfahren", max_length=4, blank=True)
    hinweis = models.CharField("Hinweis", max_length=200, blank=True)
    status = models.CharField("Status", max_length=12, choices=Status.choices, default=Status.OFFEN)
    bankumsatz = models.ForeignKey("fibu.Bankumsatz", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+", verbose_name="Abgeglichen mit Bankumsatz")
    abgleich_buchung = models.ForeignKey("fibu.Buchung", null=True, blank=True, on_delete=models.PROTECT,
                                         related_name="+")

    EINGEFROREN = ("lauf_id", "op_id", "partner_id", "mandat_id", "ausgewaehlt", "name", "iban", "bic", "offen",
                   "skonto", "betrag", "verwendungszweck", "end_to_end_id", "pmtinf_id", "sequenz", "mandatsreferenz",
                   "mandat_datum", "lastschrift_typ")

    class Meta:
        verbose_name = "Zahlungslauf-Posten"
        verbose_name_plural = "Zahlungslauf-Posten"
        ordering = ["lauf", "partner__name", "pk"]

    def __str__(self):
        return f"{self.lauf.nummer}: {self.name} {self.betrag}"

    def save(self, *args, **kwargs):
        if Zahlungslauf.objects.filter(pk=self.lauf_id, exportiert_am__isnull=False).exists():
            alt = ZahlungslaufPosten.objects.filter(pk=self.pk).first() if self.pk else None
            if alt is None or any(getattr(alt, f) != getattr(self, f) for f in self.EINGEFROREN):
                raise SepaGesperrt("Posten eines exportierten Zahlungslaufs sind unveränderbar.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if Zahlungslauf.objects.filter(pk=self.lauf_id, exportiert_am__isnull=False).exists():
            raise SepaGesperrt("Posten eines exportierten Zahlungslaufs sind unveränderbar.")
        return super().delete(*args, **kwargs)


class Ruecklastschrift(models.Model):
    """Rücklastschrift (Lastschrift) bzw. Rückläufer (Überweisung) aus dem Kontoauszug."""

    GRUENDE = {
        "AC01": "IBAN fehlerhaft", "AC04": "Konto erloschen", "AC06": "Konto gesperrt",
        "AC13": "Zahlungspflichtiger ist Verbraucher (B2B nicht zulässig)", "AG01": "Lastschrift unzulässig",
        "AG02": "Ungültiger Transaktionscode", "AM04": "Deckung unzureichend", "AM05": "Doppelte Einreichung",
        "BE05": "Gläubiger unbekannt", "CNOR": "Bank des Gläubigers nicht erreichbar",
        "DNOR": "Bank des Zahlungspflichtigen nicht erreichbar", "FF01": "Ungültiges Dateiformat",
        "MD01": "Kein gültiges Mandat", "MD02": "Mandatsdaten fehlen/fehlerhaft",
        "MD06": "Erstattung auf Wunsch des Zahlungspflichtigen (Widerspruch)", "MD07": "Zahlungspflichtiger verstorben",
        "MS02": "Rückgabe durch Zahlungspflichtigen (ohne Angabe von Gründen)", "MS03": "Grund nicht angegeben",
        "RC01": "BIC fehlerhaft", "RR01": "Regulatorische Gründe", "RR02": "Name/Adresse fehlt",
        "RR03": "Name/Adresse Gläubiger fehlt", "RR04": "Regulatorische Gründe", "SL01": "Spezifische Dienstleistung der Bank",
        "FOCR": "Rückruf auf Wunsch", "DUPL": "Doppelte Zahlung", "TECH": "Technische Gründe", "FRAD": "Betrugsverdacht",
    }
    # Diese Gründe machen das Mandat unbrauchbar → Mandat sperren (Prüfung durch die Buchhaltung)
    MANDAT_SPERREN = {"AC01", "AC04", "AC06", "AC13", "MD01", "MD02", "MD07", "BE05"}
    # MD06 bewusst NICHT sperrend: Der Kunde hat innerhalb der 8-Wochen-Frist (CORE) einer an sich autorisierten
    # Lastschrift widersprochen – das Mandat bleibt rechtlich wirksam. Es wird nur vermerkt, damit die
    # Buchhaltung die Ursache klärt (Reklamation, Doppelabbuchung). Gesperrt wird nur bei Gründen, die das
    # Mandat selbst unbrauchbar machen (z. B. AC04 Konto erloschen, MD01 kein gültiges Mandat).
    MANDAT_VERMERKEN = {"MD06"}

    posten = models.ForeignKey(ZahlungslaufPosten, on_delete=models.PROTECT, related_name="rueckgaben")
    bankumsatz = models.OneToOneField("fibu.Bankumsatz", on_delete=models.PROTECT, related_name="sepa_rueckgabe")
    grund = models.CharField("Rückgabegrund", max_length=4, blank=True)
    betrag = models.DecimalField("Zurückgegebener Betrag", max_digits=14, decimal_places=2)
    gebuehr = models.DecimalField("Rücklastschriftgebühr", max_digits=10, decimal_places=2, default=0)
    gebuehr_weiterbelastet = models.BooleanField("Gebühr an Kunden weiterbelastet", default=False)
    buchung = models.ForeignKey("fibu.Buchung", on_delete=models.PROTECT, related_name="+")
    gebuehr_op = models.ForeignKey("fibu.OffenerPosten", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="+")
    erfasst_von = models.ForeignKey(BENUTZER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    erfasst_am = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Rücklastschrift"
        verbose_name_plural = "Rücklastschriften"
        ordering = ["-erfasst_am"]

    def __str__(self):
        return f"{self.posten.end_to_end_id} {self.grund} {self.betrag}"

    @property
    def grund_text(self):
        return self.GRUENDE.get(self.grund, "Unbekannter Grund" if self.grund else "Kein Grund angegeben")

"""Vertrieb: CRM (Leads, Aktivitäten, Verkaufschancen), Kundenpreise, Kundenaufträge, Lieferungen
(Lieferscheine), Onlineshop-Anbindung und EDI/EDIFACT.

Belegnummern, PDF-Archiv und GoBD-Festschreibung kommen aus dem Belegmodul (apps.belege):
* Angebot, Auftragsbestätigung, Lieferschein, Rechnung und Gutschrift sind `Beleg`-Objekte.
* Diese App verwaltet den Prozess darum herum (offene Mengen, Teillieferungen, Lagerabgang, Versand).
"""
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.belege.models import Beleg
from apps.stammdaten.models import Artikel, Geschaeftspartner, Zahlungsbedingung

ZWEI = Decimal("0.01")
NULL = Decimal(0)


class VertriebFehler(Exception):
    """Fachlicher Fehler im Verkaufsprozess."""


# ================================================================== CRM

class Lead(models.Model):
    class Status(models.TextChoices):
        NEU = "NEU", "Neu"
        QUALIFIZIERT = "QUALIFIZIERT", "Qualifiziert"
        ANGEBOT = "ANGEBOT", "Angebot"
        GEWONNEN = "GEWONNEN", "Gewonnen"
        VERLOREN = "VERLOREN", "Verloren"

    firma = models.CharField("Firma", max_length=200)
    ansprechpartner = models.CharField("Ansprechpartner", max_length=200, blank=True)
    email = models.EmailField("E-Mail", blank=True)
    telefon = models.CharField("Telefon", max_length=50, blank=True)
    strasse = models.CharField("Straße", max_length=200, blank=True)
    plz = models.CharField("PLZ", max_length=10, blank=True)
    ort = models.CharField("Ort", max_length=100, blank=True)
    land = models.CharField("Land (ISO)", max_length=2, default="DE")
    quelle = models.CharField("Quelle", max_length=100, blank=True, help_text="z. B. Messe, Website, Empfehlung")
    status = models.CharField("Status", max_length=15, choices=Status.choices, default=Status.NEU, db_index=True)
    zustaendig = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", verbose_name="Zuständiger Vertriebsmitarbeiter")
    partner = models.ForeignKey(Geschaeftspartner, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="leads", verbose_name="Umgewandelt in Kunde")
    bemerkung = models.TextField("Bemerkung", blank=True)
    erstellt_am = models.DateTimeField("Erstellt am", auto_now_add=True)

    class Meta:
        verbose_name = "Lead"
        verbose_name_plural = "Leads / Interessenten"
        ordering = ["-erstellt_am", "-pk"]

    def __str__(self):
        return self.firma


class Aktivitaet(models.Model):
    class Art(models.TextChoices):
        ANRUF = "ANRUF", "Anruf"
        BESUCH = "BESUCH", "Besuch"
        EMAIL = "EMAIL", "E-Mail"
        NOTIZ = "NOTIZ", "Notiz"

    art = models.CharField("Art", max_length=10, choices=Art.choices, default=Art.ANRUF)
    lead = models.ForeignKey(Lead, null=True, blank=True, on_delete=models.CASCADE, related_name="aktivitaeten",
                             verbose_name="Lead")
    partner = models.ForeignKey(Geschaeftspartner, null=True, blank=True, on_delete=models.CASCADE,
                                related_name="aktivitaeten", verbose_name="Kunde")
    betreff = models.CharField("Betreff", max_length=200)
    notiz = models.TextField("Notiz", blank=True)
    zeitpunkt = models.DateTimeField("Zeitpunkt", default=timezone.now)
    wiedervorlage = models.DateField("Wiedervorlage am", null=True, blank=True)
    erledigt = models.BooleanField("Wiedervorlage erledigt", default=False)
    zustaendig = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", verbose_name="Zuständig")

    class Meta:
        verbose_name = "Aktivität"
        verbose_name_plural = "Aktivitäten / Kontakthistorie"
        ordering = ["-zeitpunkt", "-pk"]

    def __str__(self):
        return f"{self.get_art_display()}: {self.betreff}"

    @property
    def bezug(self):
        return self.partner or self.lead


class Verkaufschance(models.Model):
    class Phase(models.TextChoices):
        QUALIFIZIERUNG = "QUALIFIZIERUNG", "Qualifizierung"
        ANGEBOT = "ANGEBOT", "Angebot"
        VERHANDLUNG = "VERHANDLUNG", "Verhandlung"
        GEWONNEN = "GEWONNEN", "Gewonnen"
        VERLOREN = "VERLOREN", "Verloren"

    OFFENE_PHASEN = ["QUALIFIZIERUNG", "ANGEBOT", "VERHANDLUNG"]

    titel = models.CharField("Titel", max_length=200)
    lead = models.ForeignKey(Lead, null=True, blank=True, on_delete=models.SET_NULL, related_name="chancen",
                             verbose_name="Lead")
    partner = models.ForeignKey(Geschaeftspartner, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="verkaufschancen", verbose_name="Kunde")
    phase = models.CharField("Phase", max_length=15, choices=Phase.choices, default=Phase.QUALIFIZIERUNG)
    wert = models.DecimalField("Erwarteter Auftragswert (netto)", max_digits=14, decimal_places=2, default=0)
    wahrscheinlichkeit = models.PositiveSmallIntegerField("Wahrscheinlichkeit %", default=20)
    abschluss_erwartet = models.DateField("Erwarteter Abschluss", null=True, blank=True)
    zustaendig = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", verbose_name="Zuständig")
    angebot = models.ForeignKey(Beleg, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                verbose_name="Angebot", limit_choices_to={"belegart": "ANGEBOT"})

    class Meta:
        verbose_name = "Verkaufschance"
        verbose_name_plural = "Verkaufschancen"
        ordering = ["abschluss_erwartet", "pk"]

    def __str__(self):
        return self.titel

    @property
    def gewichteter_wert(self):
        return (self.wert * Decimal(min(self.wahrscheinlichkeit, 100)) / 100).quantize(ZWEI)


class Kundenpreis(models.Model):
    """Kundenspezifischer Preis bzw. Rabatt, optional als Staffel (ab Menge). Partner leer = Staffel für alle."""

    partner = models.ForeignKey(Geschaeftspartner, null=True, blank=True, on_delete=models.CASCADE,
                                related_name="kundenpreise", verbose_name="Kunde (leer = alle Kunden)",
                                limit_choices_to={"ist_kunde": True})
    artikel = models.ForeignKey(Artikel, on_delete=models.CASCADE, related_name="kundenpreise",
                                verbose_name="Artikel")
    ab_menge = models.DecimalField("Ab Menge", max_digits=12, decimal_places=3, default=1)
    preis = models.DecimalField("Preis netto (leer = Listenpreis)", max_digits=12, decimal_places=4,
                                null=True, blank=True)
    rabatt_prozent = models.DecimalField("Rabatt %", max_digits=5, decimal_places=2, default=0)
    gueltig_ab = models.DateField("Gültig ab", null=True, blank=True)
    gueltig_bis = models.DateField("Gültig bis", null=True, blank=True)

    class Meta:
        verbose_name = "Kundenpreis / Staffel"
        verbose_name_plural = "Kundenpreise und Staffeln"
        ordering = ["artikel__artikelnummer", "partner__name", "ab_menge"]

    def __str__(self):
        wer = self.partner.name if self.partner_id else "alle Kunden"
        return f"{self.artikel.artikelnummer} / {wer} ab {Decimal(str(self.ab_menge)).normalize():f}"


# ================================================================== Auftragsabwicklung

class Auftrag(models.Model):
    class Status(models.TextChoices):
        ERFASST = "ERFASST", "Erfasst"
        BESTAETIGT = "BESTAETIGT", "Bestätigt"
        TEILGELIEFERT = "TEILGELIEFERT", "Teilgeliefert"
        GELIEFERT = "GELIEFERT", "Geliefert"
        ABGESCHLOSSEN = "ABGESCHLOSSEN", "Berechnet / abgeschlossen"
        STORNIERT = "STORNIERT", "Storniert"

    class Quelle(models.TextChoices):
        MANUELL = "MANUELL", "Manuell"
        ANGEBOT = "ANGEBOT", "Aus Angebot"
        SHOP = "SHOP", "Onlineshop (REST-API)"
        EDI = "EDI", "EDI/EDIFACT"

    OFFENE_STATI = ["ERFASST", "BESTAETIGT", "TEILGELIEFERT"]

    nummer = models.CharField("Auftragsnummer", max_length=40, unique=True, blank=True)
    partner = models.ForeignKey(Geschaeftspartner, on_delete=models.PROTECT, related_name="auftraege",
                                verbose_name="Kunde", limit_choices_to={"ist_kunde": True})
    status = models.CharField("Status", max_length=15, choices=Status.choices, default=Status.ERFASST,
                              db_index=True)
    quelle = models.CharField("Quelle", max_length=10, choices=Quelle.choices, default=Quelle.MANUELL)
    bestelldatum = models.DateField("Auftragsdatum", default=timezone.localdate)
    liefertermin = models.DateField("Wunschliefertermin", null=True, blank=True)
    ihre_referenz = models.CharField("Bestellnummer des Kunden", max_length=100, blank=True)
    zahlungsbedingung = models.ForeignKey(Zahlungsbedingung, null=True, blank=True, on_delete=models.PROTECT,
                                          verbose_name="Zahlungsbedingung")
    zustaendig = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", verbose_name="Zuständig")
    liefer_name = models.CharField("Lieferadresse: Name", max_length=200, blank=True,
                                   help_text="Leer = Lieferadresse bzw. Hauptadresse des Kunden")
    liefer_strasse = models.CharField("Lieferadresse: Straße", max_length=200, blank=True)
    liefer_plz = models.CharField("Lieferadresse: PLZ", max_length=10, blank=True)
    liefer_ort = models.CharField("Lieferadresse: Ort", max_length=100, blank=True)
    liefer_land = models.CharField("Lieferadresse: Land", max_length=2, blank=True)
    bemerkung = models.TextField("Bemerkung", blank=True)
    angebot = models.ForeignKey(Beleg, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                verbose_name="Angebot", limit_choices_to={"belegart": "ANGEBOT"})
    ab_beleg = models.ForeignKey(Beleg, null=True, blank=True, on_delete=models.PROTECT, related_name="+",
                                 verbose_name="Auftragsbestätigung", editable=False)
    erstellt_am = models.DateTimeField("Erstellt am", auto_now_add=True)

    class Meta:
        verbose_name = "Kundenauftrag"
        verbose_name_plural = "Kundenaufträge"
        ordering = ["-bestelldatum", "-pk"]

    def __str__(self):
        return f"{self.nummer} {self.partner.name}"

    def save(self, *args, **kwargs):
        if not self.nummer:
            from apps.core.nummernkreise import naechste_nummer

            self.nummer = naechste_nummer("VK_AUFTRAG", self.bestelldatum)
        super().save(*args, **kwargs)

    @property
    def summe_netto(self):
        return sum((p.betrag_netto for p in self.positionen.all()), NULL)

    @property
    def lieferadresse(self):
        """dict(name, strasse, plz, ort, land) – abweichende Lieferadresse oder Adresse des Kunden."""
        if self.liefer_plz:
            return {"name": self.liefer_name or self.partner.name, "strasse": self.liefer_strasse,
                    "plz": self.liefer_plz, "ort": self.liefer_ort, "land": self.liefer_land or "DE"}
        adressen = {a.typ: a for a in self.partner.adressen.all()}
        a = adressen.get("LIEFER") or adressen.get("HAUPT") or next(iter(adressen.values()), None)
        return {"name": self.partner.name, "strasse": a.strasse if a else "", "plz": a.plz if a else "",
                "ort": a.ort if a else "", "land": a.land if a else "DE"}

    @property
    def ist_bearbeitbar(self):
        return self.status == self.Status.ERFASST


class AuftragPosition(models.Model):
    auftrag = models.ForeignKey(Auftrag, on_delete=models.CASCADE, related_name="positionen")
    position = models.PositiveIntegerField("Pos.", default=10)
    artikel = models.ForeignKey(Artikel, on_delete=models.PROTECT, verbose_name="Artikel")
    bezeichnung = models.CharField("Bezeichnung", max_length=300, blank=True)
    kundenartikelnummer = models.CharField("Kunden-Art.-Nr.", max_length=40, blank=True)
    menge = models.DecimalField("Menge", max_digits=12, decimal_places=3)
    einheit = models.CharField("Einheit", max_length=10, blank=True)
    einzelpreis = models.DecimalField("Einzelpreis netto (leer = Preisfindung)", max_digits=12, decimal_places=4,
                                      null=True, blank=True)
    rabatt_prozent = models.DecimalField("Rabatt %", max_digits=5, decimal_places=2, null=True, blank=True)
    steuersatz = models.DecimalField("USt %", max_digits=5, decimal_places=2, null=True, blank=True)
    liefertermin = models.DateField("Liefertermin", null=True, blank=True)
    menge_geliefert = models.DecimalField("Geliefert", max_digits=12, decimal_places=3, default=0, editable=False)
    menge_berechnet = models.DecimalField("Berechnet", max_digits=12, decimal_places=3, default=0, editable=False)
    fertigungsauftrag = models.ForeignKey("produktion.Fertigungsauftrag", null=True, blank=True,
                                          on_delete=models.SET_NULL, related_name="+", editable=False,
                                          verbose_name="Fertigungsauftrag")

    class Meta:
        verbose_name = "Auftragsposition"
        verbose_name_plural = "Auftragspositionen"
        ordering = ["position", "pk"]

    def __str__(self):
        return f"{self.position}: {self.artikel.artikelnummer} × {Decimal(str(self.menge)).normalize():f}"

    def save(self, *args, **kwargs):
        if self.auftrag_id and self.pk is None and not self.auftrag.ist_bearbeitbar \
                and not getattr(self, "_intern", False):
            raise VertriebFehler("Positionen bestätigter Aufträge können nicht ergänzt werden.")
        a = self.artikel
        self.bezeichnung = self.bezeichnung or a.bezeichnung
        self.einheit = self.einheit or a.einheit.code
        if self.einzelpreis is None or self.rabatt_prozent is None:
            from .services import preis_finden

            preis, rabatt = preis_finden(self.auftrag.partner, a, self.menge, self.auftrag.bestelldatum)
            if self.einzelpreis is None:
                self.einzelpreis = preis
            if self.rabatt_prozent is None:
                self.rabatt_prozent = rabatt
        if self.steuersatz is None:
            self.steuersatz = a.steuersatz.satz
        self.liefertermin = self.liefertermin or self.auftrag.liefertermin
        super().save(*args, **kwargs)

    @property
    def betrag_netto(self):
        faktor = (Decimal(100) - (self.rabatt_prozent or 0)) / 100
        return (self.menge * (self.einzelpreis or 0) * faktor).quantize(ZWEI, ROUND_HALF_UP)

    @property
    def offen_liefern(self):
        return max(self.menge - self.menge_geliefert, NULL)

    @property
    def rueckstand(self):
        return bool(self.offen_liefern > 0 and self.liefertermin and self.liefertermin < timezone.localdate())


class Lieferung(models.Model):
    """Lieferschein zu einem Auftrag. Der zugehörige Beleg (LIEFERSCHEIN) liefert Nummer, PDF und Archiv.
    Status = Versandstatus; `GEBUCHT` heißt: Lagerabgang gebucht, Lieferschein festgeschrieben."""

    class Status(models.TextChoices):
        ERFASST = "ERFASST", "Erfasst"
        KOMMISSIONIERUNG = "KOMMISSIONIERUNG", "An LVS übergeben"
        KOMMISSIONIERT = "KOMMISSIONIERT", "Kommissioniert (LVS-Rückmeldung)"
        GEBUCHT = "GEBUCHT", "Warenausgang gebucht / versandbereit"
        VERLADEN = "VERLADEN", "Verladen"
        ZUGESTELLT = "ZUGESTELLT", "Zugestellt"
        STORNIERT = "STORNIERT", "Storniert"

    OFFEN_FUER_BUCHUNG = ["ERFASST", "KOMMISSIONIERUNG", "KOMMISSIONIERT"]

    auftrag = models.ForeignKey(Auftrag, on_delete=models.PROTECT, related_name="lieferungen",
                                verbose_name="Auftrag")
    beleg = models.OneToOneField(Beleg, on_delete=models.PROTECT, related_name="lieferung",
                                 verbose_name="Lieferschein-Beleg")
    status = models.CharField("Versandstatus", max_length=20, choices=Status.choices, default=Status.ERFASST,
                              db_index=True)
    lieferdatum = models.DateField("Lieferdatum", default=timezone.localdate)
    lagerort = models.ForeignKey("lager.Lagerort", on_delete=models.PROTECT, verbose_name="Versand-Lagerort")
    name = models.CharField("Empfänger", max_length=200, blank=True)
    strasse = models.CharField("Straße", max_length=200, blank=True)
    plz = models.CharField("PLZ", max_length=10, blank=True, db_index=True)
    ort = models.CharField("Ort", max_length=100, blank=True)
    land = models.CharField("Land", max_length=2, default="DE")
    gewicht_kg = models.DecimalField("Gewicht (kg)", max_digits=10, decimal_places=2, null=True, blank=True,
                                     help_text="Leer = aus Artikelgewichten berechnet")
    paletten = models.DecimalField("Paletten(stellplätze)", max_digits=6, decimal_places=1, default=0)
    packstuecke = models.PositiveIntegerField("Packstücke", default=0)
    rechnung = models.ForeignKey(Beleg, null=True, blank=True, on_delete=models.SET_NULL, related_name="lieferungen",
                                 verbose_name="Rechnung")
    erstellt_am = models.DateTimeField("Erstellt am", auto_now_add=True)
    zugestellt_am = models.DateTimeField("Zugestellt am", null=True, blank=True)

    class Meta:
        verbose_name = "Lieferschein"
        verbose_name_plural = "Lieferscheine"
        ordering = ["-lieferdatum", "-pk"]

    def __str__(self):
        return self.nummer

    @property
    def nummer(self):
        return self.beleg.nummer or f"LS-Entwurf {self.pk}"

    @property
    def kommissionier_referenz(self):
        return f"KOM-{self.pk}"

    @property
    def gewicht(self):
        if self.gewicht_kg is not None:
            return self.gewicht_kg
        summe = NULL
        for p in self.positionen.select_related("auftrag_position__artikel"):
            summe += (p.auftrag_position.artikel.gewicht_kg or 0) * p.menge
        return summe.quantize(ZWEI)


class LieferPosition(models.Model):
    lieferung = models.ForeignKey(Lieferung, on_delete=models.CASCADE, related_name="positionen")
    auftrag_position = models.ForeignKey(AuftragPosition, on_delete=models.PROTECT, related_name="lieferpositionen")
    menge = models.DecimalField("Liefermenge", max_digits=12, decimal_places=3)
    charge = models.ForeignKey("lager.Charge", null=True, blank=True, on_delete=models.PROTECT,
                               verbose_name="Charge")
    kommissioniert = models.DecimalField("Kommissioniert (LVS)", max_digits=12, decimal_places=3, null=True,
                                         blank=True)
    packstueck = models.CharField("Packstück", max_length=50, blank=True)
    lagerbewegung = models.ForeignKey("lager.Lagerbewegung", null=True, blank=True, on_delete=models.PROTECT,
                                      related_name="+", verbose_name="Lagerbewegung")

    class Meta:
        verbose_name = "Lieferscheinposition"
        verbose_name_plural = "Lieferscheinpositionen"
        ordering = ["auftrag_position__position", "pk"]


# ================================================================== Onlineshop

class Shop(models.Model):
    """Onlineshop mit eigenem API-Token (gespeichert wird nur der SHA-256-Hash)."""

    name = models.CharField("Name", max_length=100, unique=True)
    aktiv = models.BooleanField("Aktiv", default=True)
    token_hash = models.CharField("Token-Hash", max_length=64, blank=True, editable=False, db_index=True)
    token_anfang = models.CharField("Token (Anfang)", max_length=8, blank=True, editable=False)
    zahlungsbedingung = models.ForeignKey(Zahlungsbedingung, null=True, blank=True, on_delete=models.PROTECT,
                                          verbose_name="Zahlungsbedingung für Neukunden")
    lagerort = models.ForeignKey("lager.Lagerort", null=True, blank=True, on_delete=models.SET_NULL,
                                 verbose_name="Bestand aus Lagerort (leer = alle)")
    auto_bestaetigen = models.BooleanField("Aufträge automatisch bestätigen", default=True)

    class Meta:
        verbose_name = "Onlineshop"
        verbose_name_plural = "Onlineshops"
        ordering = ["name"]

    def __str__(self):
        return self.name


class ShopBestellung(models.Model):
    shop = models.ForeignKey(Shop, on_delete=models.PROTECT, related_name="bestellungen")
    externe_nummer = models.CharField("Externe Bestellnummer", max_length=100)
    auftrag = models.OneToOneField(Auftrag, on_delete=models.PROTECT, related_name="shop_bestellung")
    rohdaten = models.JSONField("Übermittelte Daten", default=dict)
    eingegangen_am = models.DateTimeField("Eingegangen am", auto_now_add=True)

    class Meta:
        verbose_name = "Shop-Bestellung"
        verbose_name_plural = "Shop-Bestellungen"
        ordering = ["-eingegangen_am"]
        constraints = [models.UniqueConstraint(fields=["shop", "externe_nummer"], name="vertrieb_shop_extnr")]

    def __str__(self):
        return f"{self.shop} {self.externe_nummer}"


# ================================================================== EDI / EDIFACT

class EdiPartner(models.Model):
    partner = models.OneToOneField(Geschaeftspartner, on_delete=models.CASCADE, related_name="edi",
                                   verbose_name="Geschäftspartner")
    gln = models.CharField("GLN/ILN des Partners", max_length=13, unique=True)
    rechnungsempfaenger_gln = models.CharField("GLN Rechnungsempfänger (leer = Partner)", max_length=13, blank=True)
    ordrsp = models.BooleanField("ORDRSP senden", default=True)
    desadv = models.BooleanField("DESADV senden", default=True)
    invoic = models.BooleanField("INVOIC senden", default=True)
    testbetrieb = models.BooleanField("Testbetrieb (UNB-Testkennzeichen)", default=False)
    aktiv = models.BooleanField("Aktiv", default=True)

    class Meta:
        verbose_name = "EDI-Partner"
        verbose_name_plural = "EDI-Partner"
        ordering = ["partner__name"]

    def __str__(self):
        return f"{self.partner.name} ({self.gln})"


class EdiArtikelZuordnung(models.Model):
    edi_partner = models.ForeignKey(EdiPartner, on_delete=models.CASCADE, related_name="artikel")
    kundenartikelnummer = models.CharField("Kunden-Art.-Nr.", max_length=40)
    artikel = models.ForeignKey(Artikel, on_delete=models.CASCADE, verbose_name="Artikel")

    class Meta:
        verbose_name = "Kundenartikelnummer"
        verbose_name_plural = "Kundenartikelnummern"
        constraints = [models.UniqueConstraint(fields=["edi_partner", "kundenartikelnummer"],
                                               name="vertrieb_edi_kdartnr")]

    def __str__(self):
        return f"{self.kundenartikelnummer} → {self.artikel.artikelnummer}"


class EdiNachricht(models.Model):
    class Richtung(models.TextChoices):
        EIN = "EIN", "Eingang"
        AUS = "AUS", "Ausgang"

    class Typ(models.TextChoices):
        ORDERS = "ORDERS", "ORDERS (Bestellung)"
        ORDRSP = "ORDRSP", "ORDRSP (Auftragsbestätigung)"
        DESADV = "DESADV", "DESADV (Lieferavis)"
        INVOIC = "INVOIC", "INVOIC (Rechnung)"

    class Status(models.TextChoices):
        NEU = "NEU", "Neu"
        VERARBEITET = "VERARBEITET", "Verarbeitet"
        FEHLER = "FEHLER", "Fehler"
        ERZEUGT = "ERZEUGT", "Erzeugt (bereit zur Übertragung)"
        UEBERTRAGEN = "UEBERTRAGEN", "Übertragen"

    richtung = models.CharField("Richtung", max_length=3, choices=Richtung.choices)
    typ = models.CharField("Nachrichtentyp", max_length=6, choices=Typ.choices)
    status = models.CharField("Status", max_length=12, choices=Status.choices, default=Status.NEU, db_index=True)
    edi_partner = models.ForeignKey(EdiPartner, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="nachrichten", verbose_name="EDI-Partner")
    referenz = models.CharField("Referenz (Belegnummer)", max_length=100, blank=True)
    kontrollnummer = models.CharField("Übertragungsnummer (UNB)", max_length=14, blank=True)
    dateiname = models.CharField("Dateiname", max_length=200, blank=True)
    inhalt = models.TextField("Inhalt")
    fehler = models.TextField("Fehlerprotokoll", blank=True)
    auftrag = models.ForeignKey(Auftrag, null=True, blank=True, on_delete=models.SET_NULL, related_name="edi",
                                verbose_name="Auftrag")
    beleg = models.ForeignKey(Beleg, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                              verbose_name="Beleg")
    erstellt_am = models.DateTimeField("Erstellt am", auto_now_add=True)
    verarbeitet_am = models.DateTimeField("Verarbeitet am", null=True, blank=True)

    class Meta:
        verbose_name = "EDI-Nachricht"
        verbose_name_plural = "EDI-Nachrichten"
        ordering = ["-erstellt_am", "-pk"]

    def __str__(self):
        return f"{self.get_richtung_display()} {self.typ} {self.referenz or self.pk}"

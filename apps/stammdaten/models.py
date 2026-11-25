import re
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.bankdaten import bic_validator, iban_validator


class Einheit(models.Model):
    code = models.CharField("Kürzel", max_length=10, unique=True)
    bezeichnung = models.CharField("Bezeichnung", max_length=50)

    class Meta:
        verbose_name = "Einheit"
        verbose_name_plural = "Einheiten"
        ordering = ["code"]

    def __str__(self):
        return self.code


class Steuersatz(models.Model):
    code = models.CharField("Code", max_length=20, unique=True)
    bezeichnung = models.CharField("Bezeichnung", max_length=100)
    satz = models.DecimalField("Satz in %", max_digits=5, decimal_places=2)
    aktiv = models.BooleanField("Aktiv", default=True)

    class Meta:
        verbose_name = "Steuersatz"
        verbose_name_plural = "Steuersätze"
        ordering = ["-satz", "code"]

    def __str__(self):
        return f"{self.bezeichnung} ({self.satz:.0f} %)"


class Konto(models.Model):
    KONTENRAHMEN = [("SKR03", "SKR03"), ("SKR04", "SKR04")]
    KONTOARTEN = [("aktiv", "Aktivkonto"), ("passiv", "Passivkonto"), ("aufwand", "Aufwandskonto"),
                  ("ertrag", "Ertragskonto"), ("steuer", "Steuerkonto"), ("neutral", "Abschluss/Neutral"),
                  ("debitor", "Debitor (Personenkonto)"), ("kreditor", "Kreditor (Personenkonto)")]

    kontenrahmen = models.CharField("Kontenrahmen", max_length=5, choices=KONTENRAHMEN)
    nummer = models.CharField("Kontonummer", max_length=10)
    bezeichnung = models.CharField("Bezeichnung", max_length=200)
    kontoart = models.CharField("Kontoart", max_length=10, choices=KONTOARTEN)
    aktiv = models.BooleanField("Aktiv", default=True)
    # Phase 4 – Finanzbuchhaltung (Positionscodes siehe apps/fibu/gliederung.py)
    bilanz_position = models.CharField("Bilanzposition (§ 266 HGB)", max_length=20, blank=True,
                                       help_text="z. B. B.II.1; Wechselkonten „Aktiv|Passiv“, z. B. B.II.4|P.C.8")
    guv_position = models.CharField("GuV-Position (§ 275 HGB, GKV)", max_length=10, blank=True)
    automatik = models.CharField("Automatikfunktion", max_length=30, blank=True, db_index=True,
                                 help_text="Rolle für automatische Buchungen, z. B. WERE, BANK_VERRECHNUNG")
    steuer_automatik = models.CharField("Steuer-Automatik (Schlüssel)", max_length=10, blank=True,
                                        help_text="Steuerschlüssel, der bei Buchung auf dieses Konto vorbelegt wird "
                                                  "(DATEV-Automatikkonto – kein BU-Schlüssel im Export)")
    partner = models.ForeignKey("Geschaeftspartner", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="personenkonten", verbose_name="Geschäftspartner (Personenkonto)")

    class Meta:
        verbose_name = "Sachkonto"
        verbose_name_plural = "Kontenrahmen"
        ordering = ["kontenrahmen", "nummer"]
        unique_together = [("kontenrahmen", "nummer")]

    def __str__(self):
        return f"{self.nummer} {self.bezeichnung}"


class Zahlungsbedingung(models.Model):
    code = models.CharField("Code", max_length=20, unique=True)
    bezeichnung = models.CharField("Text auf Belegen", max_length=200)
    tage_netto = models.PositiveIntegerField("Zahlungsziel (Tage netto)", default=30)
    skonto_tage = models.PositiveIntegerField("Skontofrist (Tage)", default=0)
    skonto_prozent = models.DecimalField("Skonto %", max_digits=5, decimal_places=2, default=0)

    class Meta:
        verbose_name = "Zahlungsbedingung"
        verbose_name_plural = "Zahlungsbedingungen"
        ordering = ["code"]

    def __str__(self):
        return self.bezeichnung


class Artikel(models.Model):
    class Art(models.TextChoices):
        ROHSTOFF = "ROHSTOFF", "Rohstoff"
        HALBZEUG = "HALBZEUG", "Halbzeug"
        WERKZEUG = "WERKZEUG", "Werkzeug"
        BETRIEBSMITTEL = "BETRIEBSMITTEL", "Betriebsmittel"
        FERTIGARTIKEL = "FERTIGARTIKEL", "Fertigartikel"
        HANDELSWARE = "HANDELSWARE", "Handelsware"

    artikelnummer = models.CharField("Artikelnummer", max_length=40, unique=True)
    bezeichnung = models.CharField("Bezeichnung", max_length=200)
    beschreibung = models.TextField("Beschreibung", blank=True)
    art = models.CharField("Artikelart", max_length=20, choices=Art.choices, default=Art.ROHSTOFF)
    einheit = models.ForeignKey(Einheit, on_delete=models.PROTECT, verbose_name="Einheit")
    chargenpflicht = models.BooleanField("Chargenpflichtig", default=False)
    steuersatz = models.ForeignKey(Steuersatz, on_delete=models.PROTECT, verbose_name="Steuersatz")
    einkaufspreis = models.DecimalField("Einkaufspreis (netto)", max_digits=12, decimal_places=4, default=0)
    verkaufspreis = models.DecimalField("Verkaufspreis (netto)", max_digits=12, decimal_places=4, default=0)
    gewicht_kg = models.DecimalField("Gewicht (kg)", max_digits=10, decimal_places=3, null=True, blank=True)
    ean = models.CharField("EAN/GTIN", max_length=14, blank=True)
    zolltarifnummer = models.CharField("Zolltarifnummer", max_length=11, blank=True)
    aktiv = models.BooleanField("Aktiv", default=True)

    class Meta:
        verbose_name = "Artikel"
        verbose_name_plural = "Artikel"
        ordering = ["artikelnummer"]

    def __str__(self):
        return f"{self.artikelnummer} {self.bezeichnung}"

    @property
    def aktive_stueckliste(self):
        return self.stuecklisten.filter(aktiv=True).order_by("-version").first()


USTID_MUSTER = re.compile(r"^[A-Z]{2}[A-Z0-9+*]{2,12}$")
USTID_DE = re.compile(r"^DE\d{9}$")


def ustid_pruefen(wert):
    if not wert:
        return
    wert = wert.replace(" ", "").upper()
    if not USTID_MUSTER.match(wert) or (wert.startswith("DE") and not USTID_DE.match(wert)):
        raise ValidationError("Ungültiges Format der USt-IdNr. (z. B. DE123456789).")


class Geschaeftspartner(models.Model):
    nummer = models.CharField("Partnernummer", max_length=20, unique=True, blank=True,
                              help_text="Leer lassen für automatische Vergabe.")
    name = models.CharField("Name / Firma", max_length=200)
    name2 = models.CharField("Namenszusatz", max_length=200, blank=True)
    ist_kunde = models.BooleanField("Kunde", default=False)
    ist_lieferant = models.BooleanField("Lieferant", default=False)
    ust_id = models.CharField("USt-IdNr.", max_length=20, blank=True, validators=[ustid_pruefen])
    steuernummer = models.CharField("Steuernummer", max_length=30, blank=True)
    leitweg_id = models.CharField("Leitweg-ID (XRechnung)", max_length=50, blank=True)
    email = models.EmailField("E-Mail", blank=True)
    email_rechnung = models.EmailField("E-Mail für Rechnungen", blank=True)
    telefon = models.CharField("Telefon", max_length=50, blank=True)
    zahlungsbedingung = models.ForeignKey(Zahlungsbedingung, null=True, blank=True, on_delete=models.PROTECT,
                                          verbose_name="Zahlungsbedingung")
    debitorenkonto = models.CharField("Debitorenkonto", max_length=10, blank=True)
    kreditorenkonto = models.CharField("Kreditorenkonto", max_length=10, blank=True)
    gln = models.CharField("GLN (EDI)", max_length=13, blank=True)
    iban = models.CharField("IBAN", max_length=34, blank=True, validators=[iban_validator],
                            help_text="Bankverbindung für Zahlungsabgleich und SEPA-Überweisungen")
    bic = models.CharField("BIC", max_length=11, blank=True, validators=[bic_validator],
                           help_text="Optional (IBAN-only im SEPA-Raum)")
    mahnsperre = models.BooleanField("Mahnsperre", default=False)
    aktiv = models.BooleanField("Aktiv", default=True)

    class Meta:
        verbose_name = "Geschäftspartner"
        verbose_name_plural = "Geschäftspartner"
        ordering = ["name"]

    def __str__(self):
        return f"{self.nummer} {self.name}".strip()

    def clean(self):
        if not (self.ist_kunde or self.ist_lieferant):
            raise ValidationError("Ein Geschäftspartner muss Kunde und/oder Lieferant sein.")
        if self.ust_id:
            self.ust_id = self.ust_id.replace(" ", "").upper()
        # Bankverbindung einheitlich ohne Leerzeichen speichern (Prüfung über die Feld-Validatoren)
        self.iban = (self.iban or "").replace(" ", "").upper()
        self.bic = (self.bic or "").replace(" ", "").upper()

    def save(self, *args, **kwargs):
        if not self.nummer:
            from apps.core.nummernkreise import naechste_nummer

            self.nummer = naechste_nummer("PARTNER")
        super().save(*args, **kwargs)

    @property
    def rechnungsadresse(self):
        adressen = list(self.adressen.all())
        for typ in ("RECHNUNG", "HAUPT"):
            for a in adressen:
                if a.typ == typ:
                    return a
        return adressen[0] if adressen else None

    @property
    def rollen_text(self):
        return " / ".join(t for t, b in (("Kunde", self.ist_kunde), ("Lieferant", self.ist_lieferant)) if b)


class Adresse(models.Model):
    TYPEN = [("HAUPT", "Hauptadresse"), ("RECHNUNG", "Rechnungsadresse"), ("LIEFER", "Lieferadresse")]

    partner = models.ForeignKey(Geschaeftspartner, on_delete=models.CASCADE, related_name="adressen")
    typ = models.CharField("Typ", max_length=10, choices=TYPEN, default="HAUPT")
    zusatz = models.CharField("Zusatz", max_length=200, blank=True)
    strasse = models.CharField("Straße", max_length=200)
    plz = models.CharField("PLZ", max_length=10)
    ort = models.CharField("Ort", max_length=100)
    land = models.CharField("Land (ISO)", max_length=2, default="DE")

    class Meta:
        verbose_name = "Adresse"
        verbose_name_plural = "Adressen"

    def __str__(self):
        return f"{self.strasse}, {self.plz} {self.ort}"


class Ansprechpartner(models.Model):
    partner = models.ForeignKey(Geschaeftspartner, on_delete=models.CASCADE, related_name="ansprechpartner")
    anrede = models.CharField("Anrede", max_length=20, blank=True)
    vorname = models.CharField("Vorname", max_length=100, blank=True)
    nachname = models.CharField("Nachname", max_length=100)
    funktion = models.CharField("Funktion", max_length=100, blank=True)
    email = models.EmailField("E-Mail", blank=True)
    telefon = models.CharField("Telefon", max_length=50, blank=True)

    class Meta:
        verbose_name = "Ansprechpartner"
        verbose_name_plural = "Ansprechpartner"

    def __str__(self):
        return f"{self.vorname} {self.nachname}".strip()


class StuecklistenFehler(Exception):
    pass


class Stueckliste(models.Model):
    artikel = models.ForeignKey(Artikel, on_delete=models.PROTECT, related_name="stuecklisten",
                                verbose_name="Erzeugnis")
    version = models.PositiveIntegerField("Version", default=1)
    bezeichnung = models.CharField("Bezeichnung", max_length=200, blank=True)
    basismenge = models.DecimalField("Basismenge", max_digits=12, decimal_places=3, default=1)
    aktiv = models.BooleanField("Aktiv", default=True)

    class Meta:
        verbose_name = "Stückliste"
        verbose_name_plural = "Stücklisten"
        ordering = ["artikel__artikelnummer", "-version"]
        unique_together = [("artikel", "version")]

    def __str__(self):
        return f"{self.artikel.artikelnummer} V{self.version} {self.bezeichnung}".strip()

    def aufloesen(self, menge=Decimal("1"), ebene=1, _pfad=None):
        """Mehrstufige Auflösung. Liefert [(Ebene, Artikel, Menge, hat_Unterstückliste)].
        Zyklische Stücklisten werden erkannt."""
        pfad = (_pfad or set()) | {self.artikel_id}
        ergebnis = []
        faktor = Decimal(menge) / (self.basismenge or 1)
        for pos in self.positionen.select_related("komponente__einheit").order_by("position"):
            if pos.komponente_id in pfad:
                raise StuecklistenFehler(f"Zyklus in Stückliste bei {pos.komponente}")
            bedarf = (pos.menge * faktor).quantize(Decimal("0.001"))
            unter = pos.komponente.aktive_stueckliste
            ergebnis.append((ebene, pos.komponente, bedarf, unter is not None))
            if unter:
                ergebnis.extend(unter.aufloesen(bedarf, ebene + 1, pfad))
        return ergebnis

    def rohstoffbedarf(self, menge=Decimal("1")):
        """Summierter Bedarf der untersten Ebene (Artikel ohne eigene Stückliste)."""
        summen = {}
        for _, artikel, bedarf, hat_unter in self.aufloesen(menge):
            if not hat_unter:
                summen[artikel] = summen.get(artikel, Decimal(0)) + bedarf
        return summen


class StuecklistenPosition(models.Model):
    stueckliste = models.ForeignKey(Stueckliste, on_delete=models.CASCADE, related_name="positionen")
    position = models.PositiveIntegerField("Pos.", default=10)
    komponente = models.ForeignKey(Artikel, on_delete=models.PROTECT, related_name="verwendungen",
                                   verbose_name="Komponente")
    menge = models.DecimalField("Menge", max_digits=12, decimal_places=3)
    bemerkung = models.CharField("Bemerkung", max_length=200, blank=True)

    class Meta:
        verbose_name = "Stücklistenposition"
        verbose_name_plural = "Stücklistenpositionen"
        ordering = ["position"]

    def __str__(self):
        return f"{self.position}: {self.menge} × {self.komponente}"

    def clean(self):
        if self.komponente_id and self.stueckliste_id and self.komponente_id == self.stueckliste.artikel_id:
            raise ValidationError("Ein Artikel kann nicht Bestandteil seiner eigenen Stückliste sein.")


class Arbeitsplan(models.Model):
    artikel = models.ForeignKey(Artikel, on_delete=models.PROTECT, related_name="arbeitsplaene",
                                verbose_name="Erzeugnis")
    version = models.PositiveIntegerField("Version", default=1)
    bezeichnung = models.CharField("Bezeichnung", max_length=200, blank=True)
    aktiv = models.BooleanField("Aktiv", default=True)

    class Meta:
        verbose_name = "Arbeitsplan"
        verbose_name_plural = "Arbeitspläne"
        ordering = ["artikel__artikelnummer", "-version"]
        unique_together = [("artikel", "version")]

    def __str__(self):
        return f"{self.artikel.artikelnummer} V{self.version} {self.bezeichnung}".strip()

    def vorgabezeit_min(self, menge=1):
        """Gesamte Vorgabezeit in Minuten (Rüstzeit + Stückzeit × Menge) – Basis für den Akkord."""
        return sum((ag.ruestzeit_min + ag.stueckzeit_min * Decimal(menge)) for ag in self.arbeitsgaenge.all())


class Lohnart(models.TextChoices):
    AKKORD = "AKKORD", "Akkord (Zeitakkord)"
    ZEITLOHN = "ZEITLOHN", "Zeitlohn"


class Arbeitsplatz(models.Model):
    """Arbeitsplatz / Kostenstelle (Maschine oder Handarbeitsplatz) mit Platzkostensatz je Stunde."""

    class Art(models.TextChoices):
        MASCHINE = "MASCHINE", "Maschine / Anlage"
        HAND = "HAND", "Handarbeitsplatz"

    code = models.CharField("Code", max_length=20, unique=True)
    bezeichnung = models.CharField("Bezeichnung", max_length=100)
    art = models.CharField("Art", max_length=10, choices=Art.choices, default=Art.MASCHINE)
    kostenstelle = models.CharField("Kostenstelle", max_length=20, blank=True)
    stundensatz = models.DecimalField("Platzkostensatz (€/h)", max_digits=10, decimal_places=2, default=0,
                                      help_text="Maschinen-/Platzkosten je Stunde ohne Lohn (für die Herstellkosten)")
    aktiv = models.BooleanField("Aktiv", default=True)

    class Meta:
        verbose_name = "Arbeitsplatz"
        verbose_name_plural = "Arbeitsplätze"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.bezeichnung}"


class Mitarbeiter(models.Model):
    """Personalstamm für BDE und Akkordlohn (keine Lohnabrechnung)."""

    personalnummer = models.CharField("Personalnummer", max_length=20, unique=True)
    ausweisnummer = models.CharField("Ausweis-/Stempelkarten-Nr.", max_length=30, blank=True)
    vorname = models.CharField("Vorname", max_length=60)
    nachname = models.CharField("Nachname", max_length=60)
    lohngruppe = models.CharField("Lohngruppe", max_length=20, blank=True)
    stundenlohn = models.DecimalField("Grund-/Stundenlohn (€/h)", max_digits=8, decimal_places=2, default=0,
                                      help_text="Zeitlohn und garantierter Mindestverdienst im Akkord")
    akkordrichtsatz = models.DecimalField("Akkordrichtsatz (€/h)", max_digits=8, decimal_places=2, default=0,
                                          help_text="Verdienst bei Normalleistung (Leistungsgrad 100 %)")
    benutzer = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="mitarbeiter", verbose_name="Benutzerkonto")
    aktiv = models.BooleanField("Aktiv", default=True)

    class Meta:
        verbose_name = "Mitarbeiter"
        verbose_name_plural = "Mitarbeiter"
        ordering = ["personalnummer"]
        constraints = [models.UniqueConstraint(fields=["ausweisnummer"], condition=~models.Q(ausweisnummer=""),
                                               name="stammdaten_mitarbeiter_ausweis_eindeutig")]

    def __str__(self):
        return f"{self.personalnummer} {self.vorname} {self.nachname}"

    @property
    def name(self):
        return f"{self.vorname} {self.nachname}"

    @property
    def minutenfaktor(self):
        """Akkordrichtsatz / 60 – Verdienst je Vorgabeminute."""
        return (self.akkordrichtsatz / 60).quantize(Decimal("0.0001"))


class Arbeitsgang(models.Model):
    arbeitsplan = models.ForeignKey(Arbeitsplan, on_delete=models.CASCADE, related_name="arbeitsgaenge")
    position = models.PositiveIntegerField("AG-Nr.", default=10)
    bezeichnung = models.CharField("Arbeitsgang", max_length=200)
    arbeitsplatz = models.ForeignKey(Arbeitsplatz, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="arbeitsgaenge", verbose_name="Arbeitsplatz")
    ruestzeit_min = models.DecimalField("Rüstzeit (min)", max_digits=8, decimal_places=2, default=0)
    stueckzeit_min = models.DecimalField("Vorgabezeit te je Stück (min)", max_digits=8, decimal_places=3, default=0)
    lohngruppe = models.CharField("Lohngruppe", max_length=20, blank=True)
    lohnart = models.CharField("Lohnart", max_length=10, choices=Lohnart.choices, default=Lohnart.AKKORD)

    class Meta:
        verbose_name = "Arbeitsgang"
        verbose_name_plural = "Arbeitsgänge"
        ordering = ["position"]

    def __str__(self):
        return f"{self.position} {self.bezeichnung}"

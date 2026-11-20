from django.conf import settings
from django.db import models

AKTIONEN = [
    ("lesen", "Lesen"),
    ("anlegen", "Anlegen"),
    ("aendern", "Ändern"),
    ("freigeben", "Freigeben"),
    ("loeschen", "Löschen"),
]
AKTION_CODES = [a for a, _ in AKTIONEN]


class Mandant(models.Model):
    """Firmenstammdaten (Phase 1: ein Mandant; Mehrmandantenfähigkeit siehe Roadmap)."""

    KONTENRAHMEN = [("SKR03", "SKR03 (Prozessgliederung)"), ("SKR04", "SKR04 (Abschlussgliederung)")]

    name = models.CharField("Firmenname", max_length=200)
    strasse = models.CharField("Straße", max_length=200, blank=True)
    plz = models.CharField("PLZ", max_length=10, blank=True)
    ort = models.CharField("Ort", max_length=100, blank=True)
    land = models.CharField("Land", max_length=2, default="DE")
    telefon = models.CharField("Telefon", max_length=50, blank=True)
    email = models.EmailField("E-Mail", blank=True)
    web = models.CharField("Website", max_length=200, blank=True)
    ust_id = models.CharField("USt-IdNr.", max_length=20, blank=True)
    steuernummer = models.CharField("Steuernummer", max_length=30, blank=True)
    handelsregister = models.CharField("Handelsregister", max_length=100, blank=True)
    geschaeftsfuehrer = models.CharField("Geschäftsführer", max_length=200, blank=True)
    bank = models.CharField("Bank", max_length=100, blank=True)
    iban = models.CharField("IBAN", max_length=34, blank=True)
    bic = models.CharField("BIC", max_length=11, blank=True)
    kontenrahmen = models.CharField("Kontenrahmen", max_length=5, choices=KONTENRAHMEN, default="SKR03")

    class Meta:
        verbose_name = "Mandant / Firma"
        verbose_name_plural = "Mandanten / Firmen"

    def __str__(self):
        return self.name

    @classmethod
    def aktuell(cls):
        return cls.objects.order_by("pk").first()


class Modul(models.Model):
    """Registriertes ERP-Modul. Wird beim Migrieren aus den App-Konfigurationen synchronisiert."""

    code = models.SlugField("Code", unique=True)
    name = models.CharField("Name", max_length=100)
    beschreibung = models.TextField("Beschreibung", blank=True)
    icon = models.CharField("Icon (Bootstrap Icons)", max_length=50, blank=True)
    reihenfolge = models.PositiveIntegerField("Reihenfolge", default=100)
    url_name = models.CharField("URL-Name", max_length=100, blank=True)
    aktiv = models.BooleanField("Aktiv", default=True)
    pflicht = models.BooleanField("Pflichtmodul (nicht abschaltbar)", default=False)
    geplant = models.JSONField("Geplante Funktionen", default=list, blank=True)

    class Meta:
        verbose_name = "Modul"
        verbose_name_plural = "Module"
        ordering = ["reihenfolge", "name"]

    def __str__(self):
        return self.name


class Rolle(models.Model):
    name = models.CharField("Name", max_length=100, unique=True)
    beschreibung = models.TextField("Beschreibung", blank=True)
    benutzer = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name="erp_rollen", blank=True, verbose_name="Benutzer"
    )

    class Meta:
        verbose_name = "Rolle"
        verbose_name_plural = "Rollen"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Recht(models.Model):
    """Ein Eintrag der Rechte-Matrix: Rolle × Modul mit fünf Aktionen."""

    rolle = models.ForeignKey(Rolle, on_delete=models.CASCADE, related_name="rechte", verbose_name="Rolle")
    modul = models.ForeignKey(Modul, on_delete=models.CASCADE, related_name="rechte", verbose_name="Modul")
    lesen = models.BooleanField("Lesen", default=False)
    anlegen = models.BooleanField("Anlegen", default=False)
    aendern = models.BooleanField("Ändern", default=False)
    freigeben = models.BooleanField("Freigeben", default=False)
    loeschen = models.BooleanField("Löschen", default=False)

    class Meta:
        verbose_name = "Recht"
        verbose_name_plural = "Rechte"
        unique_together = [("rolle", "modul")]

    def __str__(self):
        aktiv = [label for code, label in AKTIONEN if getattr(self, code)]
        return f"{self.rolle} / {self.modul}: {', '.join(aktiv) or '–'}"


class Nummernkreis(models.Model):
    """Lückenloser Nummernkreis. Vergabe ausschließlich über core.nummernkreise.naechste_nummer()."""

    code = models.SlugField("Code", max_length=50, unique=True)
    bezeichnung = models.CharField("Bezeichnung", max_length=100)
    praefix = models.CharField("Präfix", max_length=20, blank=True)
    jahresbezogen = models.BooleanField("Jahresbezogen (Neustart je Jahr)", default=True)
    stellen = models.PositiveSmallIntegerField("Stellen", default=5)
    naechste_nummer = models.PositiveIntegerField("Nächste Nummer", default=1)
    aktuelles_jahr = models.PositiveIntegerField("Aktuelles Jahr", null=True, blank=True)

    class Meta:
        verbose_name = "Nummernkreis"
        verbose_name_plural = "Nummernkreise"
        ordering = ["code"]

    def __str__(self):
        return f"{self.bezeichnung} ({self.code})"

    def formatieren(self, nummer, jahr):
        lfd = str(nummer).zfill(self.stellen)
        return f"{self.praefix}{jahr}-{lfd}" if self.jahresbezogen else f"{self.praefix}{lfd}"


class AuditLog(models.Model):
    AKTIONEN = [("anlegen", "Angelegt"), ("aendern", "Geändert"), ("loeschen", "Gelöscht"), ("zuordnung", "Zuordnung")]

    zeitpunkt = models.DateTimeField("Zeitpunkt", auto_now_add=True, db_index=True)
    benutzer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="Benutzer"
    )
    benutzername = models.CharField("Benutzername", max_length=150, blank=True)
    aktion = models.CharField("Aktion", max_length=10, choices=AKTIONEN)
    app_label = models.CharField("Modul", max_length=50)
    modell = models.CharField("Objekttyp", max_length=100)
    objekt_id = models.CharField("Objekt-ID", max_length=50)
    objekt_text = models.CharField("Objekt", max_length=255, blank=True)
    aenderungen = models.JSONField("Änderungen", default=dict, blank=True)

    class Meta:
        verbose_name = "Audit-Log-Eintrag"
        verbose_name_plural = "Audit-Log"
        ordering = ["-zeitpunkt", "-pk"]
        indexes = [models.Index(fields=["app_label", "modell", "objekt_id"])]

    def __str__(self):
        return f"{self.zeitpunkt:%d.%m.%Y %H:%M} {self.benutzername} {self.get_aktion_display()} {self.objekt_text}"

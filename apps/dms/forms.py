from decimal import Decimal

from django import forms

from apps.core.forms import ErpForm, ErpModelForm
from apps.stammdaten.models import Geschaeftspartner

from .models import Dokument, Dokumentart, Kontierungsregel, Tag


class MehrfachDateiInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MehrfachDateiFeld(forms.FileField):
    """Dateifeld für Mehrfachauswahl – liefert eine Liste hochgeladener Dateien."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MehrfachDateiInput(attrs={"multiple": True,
                                                              "accept": ".pdf,.png,.jpg,.jpeg,.tif,.tiff,.xml"}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        einzel = super().clean
        if isinstance(data, (list, tuple)):
            return [einzel(d, initial) for d in data]
        return [einzel(data, initial)] if data else []


class UploadForm(ErpForm):
    dateien = MehrfachDateiFeld(label="Dateien (PDF, PNG, JPEG, TIFF, XRechnung-XML)",
                                help_text="Mehrfachauswahl möglich – jede Datei wird ein eigenes Dokument; "
                                          "Sammel-PDFs werden an Trennblättern automatisch geteilt.")
    art = forms.ChoiceField(label="Dokumentart", choices=Dokumentart.choices, initial=Dokumentart.EINGANGSRECHNUNG)
    bezeichnung = forms.CharField(label="Bezeichnung des Stapels", max_length=200, required=False)
    partner = forms.ModelChoiceField(label="Geschäftspartner (optional)", required=False,
                                     queryset=Geschaeftspartner.objects.filter(aktiv=True))
    automatisch_trennen = forms.BooleanField(label="Sammel-PDFs an Trennblättern teilen", required=False,
                                             initial=True)
    sofort_verarbeiten = forms.BooleanField(label="Gleich verarbeiten (OCR und Erkennung)", required=False,
                                            initial=True)


class MetadatenForm(ErpModelForm):
    beleg_nummer = forms.CharField(label="Verknüpfter Beleg (Nummer)", max_length=40, required=False)
    buchung_nummer = forms.CharField(label="Verknüpfte Buchung (Nummer)", max_length=40, required=False)

    class Meta:
        model = Dokument
        fields = ["titel", "art", "partner", "belegdatum", "tags", "bemerkung"]
        widgets = {"bemerkung": forms.Textarea(attrs={"rows": 2}), "tags": forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tags"].widget.attrs["class"] = "list-unstyled d-flex flex-wrap gap-3"
        self.fields["partner"].queryset = Geschaeftspartner.objects.filter(aktiv=True)
        if self.instance.archiviert:
            self.fields["art"].disabled = True
            self.fields["art"].help_text = "Nach der Archivierung unveränderbar."
        if self.instance.beleg_id:
            self.fields["beleg_nummer"].initial = self.instance.beleg.nummer
        if self.instance.buchung_id:
            self.fields["buchung_nummer"].initial = self.instance.buchung.nummer

    def clean_beleg_nummer(self):
        from apps.belege.models import Beleg

        nr = self.cleaned_data["beleg_nummer"].strip()
        if not nr:
            return None
        b = Beleg.objects.filter(nummer=nr).first()
        if not b:
            raise forms.ValidationError(f"Beleg {nr} nicht gefunden.")
        return b

    def clean_buchung_nummer(self):
        from apps.fibu.models import Buchung

        nr = self.cleaned_data["buchung_nummer"].strip()
        if not nr:
            return None
        b = Buchung.objects.filter(nummer=nr).first()
        if not b:
            raise forms.ValidationError(f"Buchung {nr} nicht gefunden.")
        return b


class VersionForm(ErpForm):
    datei = forms.FileField(label="Neue Datei")
    kommentar = forms.CharField(label="Grund / Kommentar", max_length=255)


class TagForm(ErpModelForm):
    class Meta:
        model = Tag
        fields = ["name", "farbe"]


class RegelForm(ErpModelForm):
    class Meta:
        model = Kontierungsregel
        fields = ["partner", "stichwort", "konto", "steuerschluessel"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["partner"].queryset = Geschaeftspartner.objects.filter(aktiv=True, ist_lieferant=True)
        self.fields["konto"].queryset = _aufwandskonten()
        self.fields["steuerschluessel"] = forms.ChoiceField(label="Steuerschlüssel (optional)", required=False,
                                                            choices=_vorsteuer_schluessel())
        self.fields["steuerschluessel"].widget.attrs["class"] = "form-select form-select-sm"


# ------------------------------------------------------------------ Prüfmaske Eingangsrechnung

def _vorsteuer_schluessel():
    from apps.fibu.models import Steuerschluessel
    from apps.fibu.services import rahmen

    return [("", "– bitte wählen –")] + [
        (s.code, f"{s.code} – {s.bezeichnung}") for s in Steuerschluessel.objects.filter(
            kontenrahmen=rahmen(), aktiv=True, art__in=["VST", "IGE", "RC", "FREI"]).order_by("code")]


def _aufwandskonten():
    from apps.fibu.services import rahmen
    from apps.stammdaten.models import Konto

    return Konto.objects.filter(kontenrahmen=rahmen(), aktiv=True, kontoart__in=["aufwand", "aktiv"],
                                partner__isnull=True).order_by("nummer")


class PruefKopfForm(ErpForm):
    """Kopfdaten der erkannten Eingangsrechnung (korrigierbar)."""

    lieferant = forms.ModelChoiceField(label="Lieferant", required=False,
                                       queryset=Geschaeftspartner.objects.filter(aktiv=True, ist_lieferant=True))
    rechnungsnummer = forms.CharField(label="Rechnungsnummer", max_length=60, required=False)
    rechnungsdatum = forms.DateField(label="Rechnungsdatum", required=False,
                                     widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    faellig_am = forms.DateField(label="Fällig am", required=False,
                                 widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    zahlungsbedingung = forms.CharField(label="Zahlungsbedingung", max_length=200, required=False)
    iban = forms.CharField(label="IBAN", max_length=34, required=False)
    ust_id = forms.CharField(label="USt-IdNr. Lieferant", max_length=20, required=False)
    netto = forms.DecimalField(label="Netto", max_digits=14, decimal_places=2, required=False)
    steuer = forms.DecimalField(label="Steuer", max_digits=14, decimal_places=2, required=False)
    brutto = forms.DecimalField(label="Brutto", max_digits=14, decimal_places=2, required=False)
    bestellung = forms.CharField(label="Bestellbezug (Dreifach-Abgleich)", required=False, widget=forms.Select)

    def __init__(self, *args, bestellungen=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bestellung"].widget.choices = [("", "automatisch (Bestellnummer erkennen)"),
                                                    ("ohne", "ohne Bestellbezug")] + [
            (str(b.pk), f"{b.nummer} vom {b.datum:%d.%m.%Y} · {b.get_status_display()} · netto "
                        f"{b.summe_netto:.2f} €".replace(".", ",")) for b in bestellungen]
        self.fields["bestellung"].widget.attrs["class"] = "form-select form-select-sm"

    def clean_bestellung(self):
        from apps.einkauf.models import Bestellung

        wert = (self.cleaned_data.get("bestellung") or "").strip()
        if wert in ("", "ohne"):
            return wert
        if not wert.isdigit() or not Bestellung.objects.filter(pk=wert, typ=Bestellung.Typ.BESTELLUNG).exists():
            raise forms.ValidationError("Bestellung nicht gefunden.")
        return wert

    def clean_iban(self):
        from .erkennung import iban_gueltig

        iban = self.cleaned_data["iban"].replace(" ", "").upper()
        if iban and not iban_gueltig(iban):
            raise forms.ValidationError("IBAN ist ungültig (Prüfziffer).")
        return iban


class PruefPositionForm(ErpForm):
    beschreibung = forms.CharField(label="Beschreibung", max_length=200, required=False)
    menge = forms.DecimalField(label="Menge", max_digits=12, decimal_places=3, required=False)
    einzelpreis = forms.DecimalField(label="Einzelpreis", max_digits=12, decimal_places=4, required=False)
    betrag = forms.DecimalField(label="Betrag netto", max_digits=14, decimal_places=2, required=False)
    steuersatz = forms.DecimalField(label="USt %", max_digits=5, decimal_places=2, required=False)
    steuerschluessel = forms.ChoiceField(label="Steuerschlüssel", required=False)
    konto = forms.ModelChoiceField(label="Konto", required=False, queryset=None, to_field_name="nummer")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["steuerschluessel"].choices = _vorsteuer_schluessel()
        self.fields["konto"].queryset = _aufwandskonten()

    def leer(self):
        d = getattr(self, "cleaned_data", {}) or {}
        return not any(d.get(f) not in (None, "") for f in ("beschreibung", "menge", "einzelpreis", "betrag"))

    def clean(self):
        d = super().clean()
        if self.leer() or d.get("DELETE"):
            return d
        if d.get("menge") in (None, 0):
            d["menge"] = Decimal("1")
        if d.get("einzelpreis") is None and d.get("betrag") is not None:
            d["einzelpreis"] = (d["betrag"] / d["menge"]).quantize(Decimal("0.0001"))
        if d.get("einzelpreis") is None:
            self.add_error("einzelpreis", "Einzelpreis oder Betrag angeben.")
        elif d.get("betrag") is None:
            d["betrag"] = (d["menge"] * d["einzelpreis"]).quantize(Decimal("0.01"))
        return d


PruefPositionFormSet = forms.formset_factory(PruefPositionForm, extra=1, can_delete=True)

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

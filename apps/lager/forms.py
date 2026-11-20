from django import forms
from django.forms import modelformset_factory
from django.utils import timezone

from apps.core.forms import ErpForm, ErpModelForm
from apps.stammdaten.models import Artikel

from .models import Bestandsart, InventurPosition, Lagerort, Lagerplatz


def _artikel():
    return Artikel.objects.filter(aktiv=True).select_related("einheit")


def _platz_pruefen(form, ort_feld, platz_feld):
    ort, platz = form.cleaned_data.get(ort_feld), form.cleaned_data.get(platz_feld)
    if ort and platz and platz.lagerort_id != ort.pk:
        form.add_error(platz_feld, f"Der Platz gehört nicht zu Lagerort {ort.code}.")


class BuchungForm(ErpForm):
    """Manueller Zugang bzw. Abgang (z. B. Anfangsbestand, Materialentnahme, Verschrottung)."""

    artikel = forms.ModelChoiceField(label="Artikel", queryset=Artikel.objects.none())
    lagerort = forms.ModelChoiceField(label="Lagerort", queryset=Lagerort.objects.filter(aktiv=True))
    lagerplatz = forms.ModelChoiceField(label="Lagerplatz", required=False,
                                        queryset=Lagerplatz.objects.filter(aktiv=True).select_related("lagerort"))
    bestandsart = forms.ChoiceField(label="Bestandsart", choices=Bestandsart.choices, initial=Bestandsart.FREI)
    chargennummer = forms.CharField(label="Charge", required=False, max_length=50,
                                    help_text="Pflicht bei chargenpflichtigen Artikeln.")
    menge = forms.DecimalField(label="Menge", max_digits=14, decimal_places=3, min_value=0.001)
    preis = forms.DecimalField(label="Einstandspreis je Einheit", max_digits=14, decimal_places=4, required=False,
                               min_value=0, help_text="Leer = aktueller Durchschnitts- bzw. Einkaufspreis.")
    buchungsdatum = forms.DateField(label="Buchungsdatum", initial=timezone.localdate)
    grund = forms.CharField(label="Grund / Bemerkung", max_length=300)

    def __init__(self, *args, zugang=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["artikel"].queryset = _artikel()
        if not zugang:
            del self.fields["preis"]

    def clean(self):
        daten = super().clean()
        _platz_pruefen(self, "lagerort", "lagerplatz")
        artikel = daten.get("artikel")
        if artikel and artikel.chargenpflicht and not daten.get("chargennummer"):
            self.add_error("chargennummer", "Artikel ist chargenpflichtig.")
        return daten


class UmbuchungForm(ErpForm):
    artikel = forms.ModelChoiceField(label="Artikel", queryset=Artikel.objects.none())
    chargennummer = forms.CharField(label="Charge", required=False, max_length=50)
    bestandsart = forms.ChoiceField(label="Bestandsart", choices=Bestandsart.choices, initial=Bestandsart.FREI,
                                    help_text="Statuswechsel gesperrt → frei nur über die Qualitätsprüfung.")
    von_lagerort = forms.ModelChoiceField(label="Von Lagerort", queryset=Lagerort.objects.filter(aktiv=True))
    von_lagerplatz = forms.ModelChoiceField(label="Von Platz", required=False,
                                            queryset=Lagerplatz.objects.select_related("lagerort"))
    nach_lagerort = forms.ModelChoiceField(label="Nach Lagerort", queryset=Lagerort.objects.filter(aktiv=True))
    nach_lagerplatz = forms.ModelChoiceField(label="Nach Platz", required=False,
                                             queryset=Lagerplatz.objects.filter(aktiv=True).select_related("lagerort"))
    menge = forms.DecimalField(label="Menge", max_digits=14, decimal_places=3, min_value=0.001)
    grund = forms.CharField(label="Grund / Bemerkung", max_length=300, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["artikel"].queryset = _artikel()

    def clean(self):
        daten = super().clean()
        _platz_pruefen(self, "von_lagerort", "von_lagerplatz")
        _platz_pruefen(self, "nach_lagerort", "nach_lagerplatz")
        if (daten.get("von_lagerort"), daten.get("von_lagerplatz")) == (daten.get("nach_lagerort"),
                                                                          daten.get("nach_lagerplatz")):
            raise forms.ValidationError("Quelle und Ziel sind identisch.")
        return daten


class InventurNeuForm(ErpForm):
    lagerort = forms.ModelChoiceField(label="Lagerort", queryset=Lagerort.objects.filter(aktiv=True))
    stichtag = forms.DateField(label="Stichtag", initial=timezone.localdate)
    bezeichnung = forms.CharField(label="Bezeichnung", max_length=100, required=False)


class ZaehlForm(ErpModelForm):
    class Meta:
        model = InventurPosition
        fields = ["istmenge"]


ZaehlFormSet = modelformset_factory(InventurPosition, form=ZaehlForm, extra=0)

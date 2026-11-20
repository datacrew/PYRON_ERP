from django import forms
from django.forms import formset_factory, inlineformset_factory
from django.utils import timezone

from apps.core.forms import ErpForm, ErpModelForm
from apps.stammdaten.models import Geschaeftspartner, Konto

from .models import Eingangsrechnung, ERPosition, Steuerschluessel
from .services import rahmen


def _konten():
    return Konto.objects.filter(kontenrahmen=rahmen(), aktiv=True).order_by("nummer")


class KontoChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.nummer} {obj.bezeichnung}"


class BuchungKopfForm(ErpForm):
    belegdatum = forms.DateField(label="Belegdatum", initial=timezone.localdate)
    belegnr = forms.CharField(label="Belegnummer", max_length=40)
    text = forms.CharField(label="Buchungstext", max_length=200)
    brutto = forms.BooleanField(label="Beträge mit Steuerschlüssel sind brutto", required=False, initial=True)


class BuchungZeileForm(ErpForm):
    konto = KontoChoiceField(queryset=Konto.objects.none(), label="Konto", required=False)
    soll = forms.DecimalField(label="Soll", max_digits=14, decimal_places=2, required=False)
    haben = forms.DecimalField(label="Haben", max_digits=14, decimal_places=2, required=False)
    steuerschluessel = forms.ModelChoiceField(queryset=Steuerschluessel.objects.none(), label="Steuer",
                                              required=False)
    text = forms.CharField(label="Zeilentext", max_length=200, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["konto"].queryset = _konten()
        self.fields["steuerschluessel"].queryset = Steuerschluessel.objects.filter(kontenrahmen=rahmen(), aktiv=True)

    def clean(self):
        d = super().clean()
        if d.get("konto") and not (d.get("soll") or d.get("haben")):
            raise forms.ValidationError("Betrag im Soll oder Haben angeben.")
        if (d.get("soll") or d.get("haben")) and not d.get("konto"):
            raise forms.ValidationError("Konto fehlt.")
        if d.get("soll") and d.get("haben"):
            raise forms.ValidationError("Nur Soll oder Haben je Zeile.")
        return d


BuchungZeilenFormSet = formset_factory(BuchungZeileForm, extra=6, max_num=30)


class StornoForm(ErpForm):
    grund = forms.CharField(label="Stornogrund", max_length=150)
    datum = forms.DateField(label="Stornodatum (leer = Belegdatum der Buchung)", required=False)


class ZeitraumForm(ErpForm):
    von = forms.DateField(label="Von")
    bis = forms.DateField(label="Bis")


class AusgleichForm(ErpForm):
    betrag = forms.DecimalField(label="Zahlbetrag", max_digits=14, decimal_places=2, min_value=0.01)
    datum = forms.DateField(label="Zahlungsdatum", initial=timezone.localdate)
    gegenkonto = KontoChoiceField(queryset=Konto.objects.none(), label="Gegenkonto (Bank/Kasse)")
    skonto = forms.BooleanField(label="Skonto abziehen, falls in Frist", required=False, initial=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["gegenkonto"].queryset = _konten().filter(bilanz_position="B.IV")


class BankUploadForm(ErpForm):
    datei = forms.FileField(label="Kontoauszug (CAMT.053-XML oder MT940)")
    automatisch = forms.BooleanField(label="Eindeutige Treffer und Gebühren automatisch buchen", required=False,
                                     initial=True)


class SachkontoForm(ErpForm):
    konto = KontoChoiceField(queryset=Konto.objects.none(), label="Gegenkonto")
    steuerschluessel = forms.ModelChoiceField(queryset=Steuerschluessel.objects.none(), label="Steuerschlüssel",
                                              required=False)
    text = forms.CharField(label="Buchungstext", max_length=200, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["konto"].queryset = _konten().exclude(kontoart__in=["debitor", "kreditor"])
        self.fields["steuerschluessel"].queryset = Steuerschluessel.objects.filter(kontenrahmen=rahmen(), aktiv=True)


class MahnvorschlagForm(ErpForm):
    stichtag = forms.DateField(label="Stichtag", initial=timezone.localdate)


class ERKopfForm(ErpModelForm):
    wareneingang = forms.ModelChoiceField(queryset=None, label="Positionen aus Wareneingang übernehmen",
                                          required=False)

    class Meta:
        model = Eingangsrechnung
        fields = ["lieferant", "rechnungsnummer", "rechnungsdatum", "eingangsdatum", "faellig_am", "bestellung",
                  "zahlungsbedingung"]

    def __init__(self, *args, **kwargs):
        from apps.wareneingang.models import Wareneingang

        super().__init__(*args, **kwargs)
        self.fields["lieferant"].queryset = Geschaeftspartner.objects.filter(ist_lieferant=True, aktiv=True)
        self.fields["wareneingang"].queryset = Wareneingang.objects.select_related("lieferant").order_by("-datum")
        self.fields["wareneingang"].widget.attrs["class"] = "form-select form-select-sm"
        self.fields["faellig_am"].help_text = "Leer = aus Zahlungsbedingung"
        if self.instance.pk:
            del self.fields["wareneingang"]


class ERPositionForm(ErpModelForm):
    class Meta:
        model = ERPosition
        fields = ["position", "artikel", "bezeichnung", "menge", "einzelpreis", "steuerschluessel", "konto",
                  "bestellposition"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["steuerschluessel"].queryset = Steuerschluessel.objects.filter(
            kontenrahmen=rahmen(), aktiv=True, art__in=["VST", "IGE", "RC", "FREI"])
        self.fields["konto"].queryset = _konten().filter(kontoart__in=["aufwand", "aktiv"])
        self.fields["konto"].label_from_instance = lambda k: f"{k.nummer} {k.bezeichnung}"
        self.fields["bestellposition"].widget = forms.HiddenInput()
        self.fields["artikel"].required = False


ERPositionFormSet = inlineformset_factory(Eingangsrechnung, ERPosition, form=ERPositionForm, extra=2,
                                          can_delete=True)

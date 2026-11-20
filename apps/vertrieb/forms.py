from decimal import Decimal

from django import forms
from django.forms import formset_factory

from apps.core.forms import ErpForm, ErpModelForm
from apps.stammdaten.models import Artikel, Geschaeftspartner

from .models import Auftrag, Shop


class AuftragKopfForm(ErpModelForm):
    class Meta:
        model = Auftrag
        fields = ["partner", "bestelldatum", "liefertermin", "ihre_referenz", "zahlungsbedingung", "liefer_name",
                  "liefer_strasse", "liefer_plz", "liefer_ort", "liefer_land", "bemerkung"]
        widgets = {"bemerkung": forms.Textarea(attrs={"rows": 2})}


class PositionForm(ErpForm):
    artikel = forms.ModelChoiceField(Artikel.objects.filter(aktiv=True), label="Artikel", required=False)
    menge = forms.DecimalField(label="Menge", max_digits=12, decimal_places=3, required=False, min_value=Decimal("0.001"))
    einzelpreis = forms.DecimalField(label="Einzelpreis (leer = Preisfindung)", max_digits=12, decimal_places=4,
                                     required=False)
    liefertermin = forms.DateField(label="Liefertermin", required=False)

    def clean(self):
        d = super().clean()
        if d.get("artikel") and not d.get("menge"):
            self.add_error("menge", "Bitte Menge angeben.")
        return d


PositionFormSet = formset_factory(PositionForm, extra=5)


class AngebotKopfForm(ErpForm):
    partner = forms.ModelChoiceField(Geschaeftspartner.objects.filter(ist_kunde=True, aktiv=True), label="Kunde")
    betreff = forms.CharField(label="Betreff", max_length=200, required=False)
    freigeben = forms.BooleanField(label="Sofort freigeben (Nummer vergeben)", required=False, initial=True)


class StornoForm(ErpForm):
    grund = forms.CharField(label="Stornogrund", max_length=300)


class EdiUploadForm(ErpForm):
    datei = forms.FileField(label="ORDERS-Datei (.edi/.txt)")
    preise = forms.BooleanField(label="Preise aus der Bestellung übernehmen (sonst Preisfindung)", required=False)


class ShopForm(ErpModelForm):
    class Meta:
        model = Shop
        fields = ["name", "aktiv", "zahlungsbedingung", "lagerort", "auto_bestaetigen"]

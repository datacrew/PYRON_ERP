from django import forms
from django.forms import inlineformset_factory

from apps.core.forms import ErpForm, ErpModelForm
from apps.stammdaten.models import Artikel, Geschaeftspartner

from .models import Bestellanforderung, Bestellung, BestellPosition


class BestellungForm(ErpModelForm):
    class Meta:
        model = Bestellung
        fields = ["typ", "lieferant", "datum", "liefertermin", "lieferort", "zahlungsbedingung", "lieferbedingung",
                  "ihre_referenz", "kopftext", "fusstext"]
        widgets = {"kopftext": forms.Textarea(attrs={"rows": 2}), "fusstext": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lieferant"].queryset = Geschaeftspartner.objects.filter(ist_lieferant=True, aktiv=True)
        if self.instance.pk:
            self.fields["typ"].disabled = True

    def clean(self):
        daten = super().clean()
        lieferant = daten.get("lieferant")
        if lieferant and not daten.get("zahlungsbedingung"):
            daten["zahlungsbedingung"] = lieferant.zahlungsbedingung
        return daten


class PositionForm(ErpModelForm):
    class Meta:
        model = BestellPosition
        fields = ["artikel", "bezeichnung", "lieferanten_artikelnummer", "menge", "einheit", "einzelpreis",
                  "rabatt_prozent", "steuersatz", "liefertermin"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in ("einzelpreis", "steuersatz", "rabatt_prozent", "einheit", "menge"):
            self.fields[f].required = False
        self.fields["artikel"].queryset = Artikel.objects.filter(aktiv=True).select_related("einheit", "steuersatz")

    def has_changed(self):
        # Neue Zeile ohne Artikel und Menge gilt als leer (Vorgabewerte wie USt/Rabatt zählen nicht).
        if not self.instance.pk and not any(self.data.get(self.add_prefix(f)) for f in ("artikel", "menge")):
            return False
        return super().has_changed()

    def clean(self):
        daten = super().clean()
        if not self.has_changed():
            return daten
        artikel = daten.get("artikel")
        if not artikel:
            self.add_error("artikel", "Bitte einen Artikel wählen.")
            return daten
        if not daten.get("menge") or daten["menge"] <= 0:
            self.add_error("menge", "Menge größer 0 angeben.")
        daten["bezeichnung"] = daten.get("bezeichnung") or artikel.bezeichnung
        daten["einheit"] = daten.get("einheit") or artikel.einheit.code
        if daten.get("einzelpreis") is None:
            daten["einzelpreis"] = artikel.einkaufspreis
        if daten.get("steuersatz") is None:
            daten["steuersatz"] = artikel.steuersatz.satz
        if daten.get("rabatt_prozent") is None:
            daten["rabatt_prozent"] = 0
        for feld in ("bezeichnung", "einheit", "einzelpreis", "steuersatz", "rabatt_prozent"):
            setattr(self.instance, feld, daten[feld])
        return daten


PositionFormSet = inlineformset_factory(Bestellung, BestellPosition, form=PositionForm, extra=4, can_delete=True)


class StornoForm(forms.Form):
    grund = forms.CharField(label="Stornogrund", widget=forms.Textarea(attrs={"rows": 2, "class": "form-control"}))


class BanfForm(ErpModelForm):
    class Meta:
        model = Bestellanforderung
        fields = ["artikel", "menge", "bedarfstermin", "begruendung"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["artikel"].queryset = Artikel.objects.filter(aktiv=True)


class BanfUebernahmeForm(ErpForm):
    lieferant = forms.ModelChoiceField(label="Lieferant",
                                       queryset=Geschaeftspartner.objects.filter(ist_lieferant=True, aktiv=True))

from django import forms
from django.forms import inlineformset_factory

from apps.core.forms import ErpModelForm

from .models import Beleg, BelegPosition


class BelegForm(ErpModelForm):
    class Meta:
        model = Beleg
        fields = ["belegart", "partner", "belegdatum", "leistungsdatum", "zahlungsbedingung", "ihre_referenz",
                  "betreff", "kopftext", "fusstext"]
        widgets = {"kopftext": forms.Textarea(attrs={"rows": 2}), "fusstext": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["partner"].queryset = self.fields["partner"].queryset.filter(aktiv=True)

    def clean(self):
        daten = super().clean()
        art, partner = daten.get("belegart"), daten.get("partner")
        if art and partner:
            if art in Beleg.EINKAUFSARTEN and not partner.ist_lieferant:
                self.add_error("partner", "Für Einkaufsbelege muss der Partner Lieferant sein.")
            if art not in Beleg.EINKAUFSARTEN and not partner.ist_kunde:
                self.add_error("partner", "Für Verkaufsbelege muss der Partner Kunde sein.")
        if partner and not daten.get("zahlungsbedingung"):
            daten["zahlungsbedingung"] = partner.zahlungsbedingung
        return daten


class PositionForm(ErpModelForm):
    class Meta:
        model = BelegPosition
        fields = ["artikel", "bezeichnung", "menge", "einheit", "einzelpreis", "rabatt_prozent", "steuersatz"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in ("einzelpreis", "steuersatz", "rabatt_prozent", "einheit"):
            self.fields[f].required = False
        self.fields["artikel"].queryset = self.fields["artikel"].queryset.filter(aktiv=True)

    def clean(self):
        daten = super().clean()
        artikel = daten.get("artikel")
        if not artikel and not daten.get("bezeichnung") and not self.has_changed():
            return daten
        if not artikel and not daten.get("bezeichnung"):
            self.add_error("bezeichnung", "Artikel oder Bezeichnung angeben.")
        if artikel:
            daten["bezeichnung"] = daten.get("bezeichnung") or artikel.bezeichnung
            daten["einheit"] = daten.get("einheit") or artikel.einheit.code
            if daten.get("einzelpreis") is None:
                daten["einzelpreis"] = artikel.verkaufspreis
            if daten.get("steuersatz") is None:
                daten["steuersatz"] = artikel.steuersatz.satz
        for feld, standard in (("einzelpreis", 0), ("steuersatz", 19), ("rabatt_prozent", 0)):
            if daten.get(feld) is None:
                daten[feld] = standard
        for feld in ("einzelpreis", "steuersatz", "rabatt_prozent", "einheit", "bezeichnung"):
            setattr(self.instance, feld, daten.get(feld) or getattr(self.instance, feld))
        return daten


PositionFormSet = inlineformset_factory(Beleg, BelegPosition, form=PositionForm, extra=4, can_delete=True)


class StornoForm(forms.Form):
    grund = forms.CharField(label="Stornogrund", widget=forms.Textarea(attrs={"rows": 2, "class": "form-control"}))

from django import forms
from django.forms import formset_factory
from django.utils import timezone

from apps.core.forms import ErpForm
from apps.lager.models import Lagerort, Lagerplatz


class KopfForm(ErpForm):
    lieferschein_nr = forms.CharField(label="Lieferschein-Nr. des Lieferanten", max_length=50)
    datum = forms.DateField(label="Eingangsdatum", initial=timezone.localdate)
    lagerort = forms.ModelChoiceField(label="Lagerort", queryset=Lagerort.objects.filter(aktiv=True))
    bemerkung = forms.CharField(label="Bemerkung", required=False, widget=forms.Textarea(attrs={"rows": 2}))


class PositionForm(ErpForm):
    """Eine Zeile je offener Bestellposition."""

    bestellposition = forms.IntegerField(widget=forms.HiddenInput)
    menge = forms.DecimalField(label="Liefermenge", max_digits=14, decimal_places=3, min_value=0, required=False)
    chargennummer = forms.CharField(label="Charge", max_length=50, required=False)
    lieferantencharge = forms.CharField(label="Lieferantencharge", max_length=50, required=False)
    mhd = forms.DateField(label="MHD", required=False)
    herstelldatum = forms.DateField(label="Herstelldatum", required=False)
    lagerplatz = forms.ModelChoiceField(label="Platz", required=False,
                                        queryset=Lagerplatz.objects.filter(aktiv=True).select_related("lagerort"))
    lieferpreis = forms.DecimalField(label="Preis lt. Lieferschein", max_digits=14, decimal_places=4,
                                     min_value=0, required=False)
    qs_pruefen = forms.BooleanField(label="QS-Prüfung", required=False)
    restmenge_abschliessen = forms.BooleanField(label="Rest abschließen", required=False)


PositionFormSet = formset_factory(PositionForm, extra=0)


class QSForm(forms.Form):
    bemerkung = forms.CharField(label="Prüfbemerkung / Reklamationsgrund", required=False, max_length=300,
                                widget=forms.TextInput(attrs={"class": "form-control form-control-sm"}))
    ruecksenden = forms.BooleanField(label="Ware an Lieferanten zurücksenden (Abgang aus Sperrbestand)",
                                     required=False)

from django import forms
from django.utils import timezone

from apps.core.forms import ErpForm, ErpModelForm
from apps.fibu.models import Bankkonto
from apps.stammdaten.models import Geschaeftspartner

from .models import SepaMandat


class MandatForm(ErpModelForm):
    """Mandatsreferenz wird beim Anlegen automatisch aus dem Nummernkreis SEPA_MANDAT vergeben."""

    class Meta:
        model = SepaMandat
        fields = ["partner", "typ", "art", "unterschrieben_am", "kontoinhaber", "iban", "bic",
                  "letzte_lastschrift", "bemerkung"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["partner"].queryset = Geschaeftspartner.objects.filter(ist_kunde=True, aktiv=True)
        if self.instance.pk and self.instance.posten.exists():
            # Bereits genutzte Mandate: Kunde und Verfahren bleiben fest (Sequenzbezug FRST/RCUR)
            for name in ("partner", "typ", "art"):
                self.fields[name].disabled = True

    def clean_unterschrieben_am(self):
        datum = self.cleaned_data["unterschrieben_am"]
        if datum and datum > timezone.localdate():
            raise forms.ValidationError("Das Unterschriftsdatum darf nicht in der Zukunft liegen.")
        return datum


class VorschlagForm(ErpForm):
    bankkonto = forms.ModelChoiceField(queryset=Bankkonto.objects.none(), label="Eigenes Bankkonto")
    datum = forms.DateField(label="Ausführungsdatum", required=False,
                            help_text="Leer = nächster Bankarbeitstag; Wochenenden/Feiertage werden verschoben.")
    bis = forms.DateField(label="Fälligkeiten berücksichtigen bis", required=False,
                          help_text="Leer = Ausführungsdatum")
    skonto_nutzen = forms.BooleanField(label="Skonto ziehen", required=False, initial=True)

    def __init__(self, *args, lastschrift=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bankkonto"].queryset = Bankkonto.objects.filter(aktiv=True)
        if lastschrift:
            self.fields["datum"].label = "Einzugsdatum"
            self.fields["datum"].help_text = "Leer = frühestes zulässiges Datum (Vorlauf in Bankarbeitstagen)."
            del self.fields["skonto_nutzen"]


class StornoForm(ErpForm):
    grund = forms.CharField(label="Stornogrund", max_length=200)
    bestaetigt = forms.BooleanField(label="Ja, ich möchte den Lauf per Gegenbuchung stornieren", required=True)

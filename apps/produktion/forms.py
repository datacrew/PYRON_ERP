from django import forms
from django.utils import timezone

from apps.core.forms import ErpForm
from apps.lager.models import Lagerort, Lagerplatz
from apps.stammdaten.models import Artikel, Mitarbeiter

from .models import FAArbeitsgang, Fertigungsauftrag, Rueckmeldung, Stempelung
from .services import FERTIGBARE_ARTEN

DT = {"type": "datetime-local"}
DT_FORMAT = "%Y-%m-%dT%H:%M"


def _dt_feld(label, required=True):
    return forms.DateTimeField(label=label, required=required, input_formats=[DT_FORMAT, "%Y-%m-%d %H:%M"],
                               widget=forms.DateTimeInput(attrs=DT, format=DT_FORMAT))


class FAForm(ErpForm):
    artikel = forms.ModelChoiceField(label="Artikel (Fertigartikel/Halbzeug/Werkzeug)", queryset=Artikel.objects.none())
    menge = forms.DecimalField(label="Menge", max_digits=14, decimal_places=3, min_value=0.001)
    starttermin = forms.DateField(label="Starttermin", required=False)
    termin = forms.DateField(label="Endtermin", required=False)
    ziel_lagerort = forms.ModelChoiceField(label="Ziel-Lagerort", required=False,
                                           queryset=Lagerort.objects.filter(aktiv=True))
    bemerkung = forms.CharField(label="Bemerkung", required=False, max_length=300)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["artikel"].queryset = Artikel.objects.filter(aktiv=True, art__in=FERTIGBARE_ARTEN).order_by(
            "artikelnummer")


class EntnahmeForm(ErpForm):
    menge = forms.DecimalField(label="Menge", max_digits=14, decimal_places=3, min_value=0.001)


class FertigmeldungForm(ErpForm):
    menge = forms.DecimalField(label="Gutmenge zum Lager", max_digits=14, decimal_places=3, min_value=0.001)
    lagerort = forms.ModelChoiceField(label="Lagerort", queryset=Lagerort.objects.filter(aktiv=True))
    lagerplatz = forms.ModelChoiceField(label="Lagerplatz", required=False, queryset=Lagerplatz.objects.all())
    chargennummer = forms.CharField(label="Chargennummer", required=False, max_length=40,
                                    help_text="Leer = neue Fertigungscharge aus dem Nummernkreis.")
    abschliessen = forms.BooleanField(label="Auftrag danach abschließen", required=False)

    def clean(self):
        d = super().clean()
        if d.get("lagerplatz") and d.get("lagerort") and d["lagerplatz"].lagerort_id != d["lagerort"].pk:
            self.add_error("lagerplatz", "Der Lagerplatz gehört nicht zum gewählten Lagerort.")
        return d


class RueckmeldungForm(ErpForm):
    arbeitsgang = forms.ModelChoiceField(label="Arbeitsgang", queryset=FAArbeitsgang.objects.none())
    mitarbeiter = forms.ModelChoiceField(label="Mitarbeiter", required=False,
                                         queryset=Mitarbeiter.objects.filter(aktiv=True))
    gut = forms.DecimalField(label="Gutmenge", max_digits=14, decimal_places=3, min_value=0, initial=0)
    ausschuss = forms.DecimalField(label="Ausschuss", max_digits=14, decimal_places=3, min_value=0, initial=0)
    grund = forms.ChoiceField(label="Ausschussgrund", required=False,
                              choices=[("", "–")] + list(Rueckmeldung.Grund.choices))
    nacharbeit = forms.DecimalField(label="Nacharbeit", max_digits=14, decimal_places=3, min_value=0, initial=0)
    bemerkung = forms.CharField(label="Bemerkung", required=False, max_length=300)

    def __init__(self, *args, auftrag=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = FAArbeitsgang.objects.select_related("auftrag").filter(
            auftrag__status__in=Fertigungsauftrag.OFFENE_STATI)
        if auftrag is not None:
            qs = qs.filter(auftrag=auftrag)
        self.fields["arbeitsgang"].queryset = qs.order_by("auftrag__nummer", "position")
        self.fields["arbeitsgang"].label_from_instance = lambda ag: f"{ag.code} {ag.bezeichnung}"


class KorrekturForm(ErpForm):
    beginn = _dt_feld("Beginn")
    ende = _dt_feld("Ende", required=False)
    taetigkeit = forms.ChoiceField(label="Tätigkeit", choices=Stempelung.Taetigkeit.choices)
    grund = forms.CharField(label="Korrekturgrund", max_length=200)


class NachtragForm(ErpForm):
    mitarbeiter = forms.ModelChoiceField(label="Mitarbeiter", queryset=Mitarbeiter.objects.filter(aktiv=True))
    code = forms.CharField(label="Arbeitsgang-Code", max_length=40, help_text="z. B. FA-2026-00001-10")
    taetigkeit = forms.ChoiceField(label="Tätigkeit", choices=Stempelung.Taetigkeit.choices,
                                   initial=Stempelung.Taetigkeit.FERTIGEN)
    beginn = _dt_feld("Beginn")
    ende = _dt_feld("Ende")
    grund = forms.CharField(label="Grund des Nachtrags", max_length=200)


class AkkordFilterForm(ErpForm):
    von = forms.DateField(label="Von")
    bis = forms.DateField(label="Bis")
    mitarbeiter = forms.ModelChoiceField(label="Mitarbeiter", required=False, queryset=Mitarbeiter.objects.all())
    auftrag = forms.ModelChoiceField(label="Fertigungsauftrag", required=False,
                                     queryset=Fertigungsauftrag.objects.order_by("-nummer"))

    @staticmethod
    def standard():
        heute = timezone.localdate()
        return {"von": heute.replace(day=1), "bis": heute}

    def clean(self):
        d = super().clean()
        if d.get("von") and d.get("bis") and d["von"] > d["bis"]:
            raise forms.ValidationError("„Von“ liegt nach „Bis“.")
        return d


class StornoForm(ErpForm):
    grund = forms.CharField(label="Stornogrund", max_length=300)

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

from .models import Rolle


class BootstrapMixin:
    """Setzt Bootstrap-Klassen, deutsches Zahlenformat und HTML5-Datumsfelder."""

    def _bootstrap(self):
        for feld in self.fields.values():
            w = feld.widget
            if isinstance(feld, (forms.DecimalField, forms.FloatField)):
                feld.localize = True
                w.is_localized = True
                if isinstance(w, forms.NumberInput):
                    feld.widget = w = forms.TextInput(attrs=w.attrs)
                    w.is_localized = True
                w.attrs.setdefault("inputmode", "decimal")
            if isinstance(feld, forms.DateField) and not isinstance(feld, forms.DateTimeField):
                feld.widget = w = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
            if isinstance(w, (forms.CheckboxInput,)):
                w.attrs["class"] = "form-check-input"
            elif isinstance(w, (forms.Select, forms.SelectMultiple)):
                w.attrs["class"] = "form-select form-select-sm"
            else:
                w.attrs["class"] = "form-control form-control-sm"


class ErpModelForm(BootstrapMixin, forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bootstrap()


class ErpForm(BootstrapMixin, forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bootstrap()


User = get_user_model()


class BenutzerForm(ErpModelForm):
    passwort = forms.CharField(
        label="Neues Passwort", required=False, widget=forms.PasswordInput(render_value=False),
        help_text="Leer lassen, um das Passwort nicht zu ändern.",
    )
    rollen = forms.ModelMultipleChoiceField(
        label="Rollen", queryset=Rolle.objects.all(), required=False, widget=forms.CheckboxSelectMultiple
    )

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_active", "is_superuser"]
        labels = {"is_superuser": "Systemadministrator (alle Rechte)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["rollen"].widget.attrs["class"] = "list-unstyled"
        if self.instance.pk:
            self.fields["rollen"].initial = self.instance.erp_rollen.all()
        else:
            self.fields["passwort"].required = True

    def clean_passwort(self):
        pw = self.cleaned_data.get("passwort")
        if pw:
            validate_password(pw, self.instance)
        return pw

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data.get("passwort"):
            user.set_password(self.cleaned_data["passwort"])
        user.save()
        user.erp_rollen.set(self.cleaned_data["rollen"])
        return user


class RolleForm(ErpModelForm):
    class Meta:
        model = Rolle
        fields = ["name", "beschreibung"]
        widgets = {"beschreibung": forms.Textarea(attrs={"rows": 2})}

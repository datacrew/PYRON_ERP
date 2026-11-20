"""Generische, rechtegeprüfte CRUD-Views inkl. Inline-Positionen.

`crud_urls()` erzeugt für ein Modell Liste/Anlegen/Bearbeiten/Löschen mit einheitlichen Vorlagen –
so bleiben neue Stammdatenmasken in den Folgephasen mit wenigen Zeilen umsetzbar.
"""
from django.contrib import messages
from django.db import transaction
from django.db.models import ProtectedError, Q
from django.forms import inlineformset_factory, modelform_factory
from django.shortcuts import redirect
from django.urls import path, reverse
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from .forms import ErpModelForm
from .permissions import hat_recht, ModulRechtMixin


class ErpBasis(ModulRechtMixin):
    praefix = ""
    titel = ""
    titel_plural = ""
    nur_lesen = False

    def url(self, art):
        ns = self.request.resolver_match.namespace
        return f"{ns}:{self.praefix}_{art}" if ns else f"{self.praefix}_{art}"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        ctx.update(
            titel=self.titel, titel_plural=self.titel_plural,
            liste_url=self.url("liste"), neu_url=self.url("neu"),
            bearbeiten_url=self.url("bearbeiten"), loeschen_url=self.url("loeschen"),
            kann_anlegen=not self.nur_lesen and hat_recht(user, self.modul_code, "anlegen"),
            kann_aendern=not self.nur_lesen and hat_recht(user, self.modul_code, "aendern"),
            kann_loeschen=not self.nur_lesen and hat_recht(user, self.modul_code, "loeschen"),
        )
        return ctx


class ErpListView(ErpBasis, ListView):
    template_name = "generic/liste.html"
    spalten = []  # [(Überschrift, Attributpfad)]
    suchfelder = []
    paginate_by = 25

    def get_queryset(self):
        qs = super().get_queryset()
        q = self.request.GET.get("q", "").strip()
        if q and self.suchfelder:
            bedingung = Q()
            for feld in self.suchfelder:
                bedingung |= Q(**{f"{feld}__icontains": q})
            qs = qs.filter(bedingung)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(spalten=self.spalten, q=self.request.GET.get("q", ""), suche=bool(self.suchfelder))
        return ctx


class InlineMixin:
    inlines = []  # [(Titel, FormSet-Klasse)]
    zusatz_template = None

    def get_formsets(self, instanz):
        daten = self.request.POST if self.request.method == "POST" else None
        return [
            (titel, fs(daten, instance=instanz, prefix=f"fs{i}"))
            for i, (titel, fs) in enumerate(self.inlines)
        ]

    def get_context_data(self, **kwargs):
        if "formsets" not in kwargs:
            kwargs["formsets"] = self.get_formsets(self.object or self.model())
        kwargs["zusatz_template"] = self.zusatz_template
        return super().get_context_data(**kwargs)

    def nach_speichern(self):
        """Hook für abgeleitete Views (z. B. Summen neu berechnen)."""

    def form_valid(self, form):
        formsets = self.get_formsets(form.instance)
        if not all(fs.is_valid() for _, fs in formsets):
            return self.render_to_response(self.get_context_data(form=form, formsets=formsets))
        with transaction.atomic():
            self.object = form.save()
            for _, fs in formsets:
                fs.instance = self.object
                fs.save()
            self.nach_speichern()
        messages.success(self.request, f"{self.titel} „{self.object}“ wurde gespeichert.")
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse(self.url("bearbeiten"), args=[self.object.pk])


class ErpCreateView(ErpBasis, InlineMixin, CreateView):
    template_name = "generic/formular.html"
    recht_aktion = "anlegen"


class ErpUpdateView(ErpBasis, InlineMixin, UpdateView):
    template_name = "generic/formular.html"

    def get_recht_aktion(self):
        # Ansehen erfordert „lesen“, Speichern „ändern“
        return "aendern" if self.request.method == "POST" else "lesen"


class ErpDeleteView(ErpBasis, DeleteView):
    template_name = "generic/loeschen.html"
    recht_aktion = "loeschen"

    def form_valid(self, form):
        obj = self.get_object()
        try:
            obj.delete()
        except ProtectedError:
            messages.error(self.request, f"„{obj}“ wird noch verwendet und kann nicht gelöscht werden.")
            return redirect(self.url("bearbeiten"), obj.pk)
        messages.success(self.request, f"„{obj}“ wurde gelöscht.")
        return redirect(self.url("liste"))


def crud_urls(praefix, model, modul, felder, spalten, suchfelder=(), inlines=(), titel=None,
              titel_plural=None, form_class=None, widgets=None, nur_lesen=False, queryset=None,
              zusatz_template=None):
    titel = titel or model._meta.verbose_name
    titel_plural = titel_plural or model._meta.verbose_name_plural
    form = form_class or modelform_factory(model, form=ErpModelForm, fields=felder, widgets=widgets)
    gemeinsam = dict(model=model, modul_code=modul, praefix=praefix, titel=titel,
                     titel_plural=titel_plural, nur_lesen=nur_lesen)
    if queryset is not None:
        gemeinsam["queryset"] = queryset
    inline_klassen = []
    for eintrag in inlines:
        it_titel, it_model, it_felder = eintrag[:3]
        it_widgets = eintrag[3] if len(eintrag) > 3 else None
        inline_klassen.append((it_titel, inlineformset_factory(
            model, it_model, form=ErpModelForm, fields=it_felder, extra=3, can_delete=True, widgets=it_widgets
        )))
    name = model.__name__
    liste = type(f"{name}Liste", (ErpListView,), {**gemeinsam, "spalten": spalten, "suchfelder": list(suchfelder)})
    muster = [path(f"{praefix}/", liste.as_view(), name=f"{praefix}_liste")]
    detail = {**gemeinsam, "form_class": form, "inlines": inline_klassen, "zusatz_template": zusatz_template}
    muster.append(path(f"{praefix}/<int:pk>/", type(f"{name}Bearbeiten", (ErpUpdateView,), detail).as_view(),
                       name=f"{praefix}_bearbeiten"))
    if not nur_lesen:
        muster += [
            path(f"{praefix}/neu/", type(f"{name}Neu", (ErpCreateView,), detail).as_view(), name=f"{praefix}_neu"),
            path(f"{praefix}/<int:pk>/loeschen/", type(f"{name}Loeschen", (ErpDeleteView,), gemeinsam).as_view(),
                 name=f"{praefix}_loeschen"),
        ]
    return muster

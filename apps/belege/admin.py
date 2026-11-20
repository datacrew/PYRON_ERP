from django.contrib import admin, messages

from .models import Beleg, BelegGesperrt, BelegPosition, BelegVorlage


@admin.register(BelegVorlage)
class BelegVorlageAdmin(admin.ModelAdmin):
    list_display = ["name", "belegart", "aktiv", "geaendert_am"]
    list_filter = ["belegart", "aktiv"]


class PositionInline(admin.TabularInline):
    model = BelegPosition
    extra = 0
    readonly_fields = ["betrag_netto"]

    def has_change_permission(self, request, obj=None):
        return obj is None or obj.ist_bearbeitbar

    has_add_permission = has_delete_permission = lambda self, request, obj=None: obj is None or obj.ist_bearbeitbar


@admin.register(Beleg)
class BelegAdmin(admin.ModelAdmin):
    list_display = ["nummer", "belegart", "status", "partner", "belegdatum", "summe_brutto"]
    list_filter = ["belegart", "status"]
    search_fields = ["nummer", "partner__name"]
    inlines = [PositionInline]
    readonly_fields = ["nummer", "status", "summe_netto", "summe_steuer", "summe_brutto", "partner_daten",
                       "freigegeben_von", "freigegeben_am", "festgeschrieben_von", "festgeschrieben_am",
                       "storniert_von", "storniert_am", "storno_grund", "ketten_index", "pruefsumme",
                       "vorgaenger_pruefsumme", "pdf", "pdf_pruefsumme", "erstellt_von"]

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and (obj is None or obj.ist_bearbeitbar)

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and (obj is None or obj.ist_bearbeitbar)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        try:
            form.instance.berechne_summen()
            form.instance.save()
        except BelegGesperrt as exc:
            messages.error(request, str(exc))

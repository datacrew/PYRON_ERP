from django.contrib import admin

from .models import Bestellanforderung, Bestellung, BestellPosition


class PositionInline(admin.TabularInline):
    model = BestellPosition
    extra = 0
    readonly_fields = ["geliefert_menge", "erledigt"]


@admin.register(Bestellung)
class BestellungAdmin(admin.ModelAdmin):
    list_display = ["nummer", "typ", "status", "lieferant", "datum", "liefertermin", "summe_netto"]
    list_filter = ["typ", "status"]
    search_fields = ["nummer", "lieferant__name"]
    readonly_fields = ["nummer", "status", "partner_daten", "summe_netto", "summe_steuer", "summe_brutto",
                       "freigegeben_von", "freigegeben_am", "storniert_am", "storno_grund"]
    inlines = [PositionInline]


@admin.register(Bestellanforderung)
class BestellanforderungAdmin(admin.ModelAdmin):
    list_display = ["nummer", "artikel", "menge", "bedarfstermin", "status"]
    list_filter = ["status"]

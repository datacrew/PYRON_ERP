from django.contrib import admin

from .models import (Aktivitaet, Auftrag, AuftragPosition, EdiArtikelZuordnung, EdiNachricht, EdiPartner, Kundenpreis,
                     Lead, Lieferung, Shop, ShopBestellung, Verkaufschance)


class AuftragPositionInline(admin.TabularInline):
    model = AuftragPosition
    extra = 0


@admin.register(Auftrag)
class AuftragAdmin(admin.ModelAdmin):
    list_display = ["nummer", "partner", "status", "quelle", "bestelldatum"]
    list_filter = ["status", "quelle"]
    search_fields = ["nummer", "partner__name", "ihre_referenz"]
    inlines = [AuftragPositionInline]


@admin.register(EdiNachricht)
class EdiNachrichtAdmin(admin.ModelAdmin):
    list_display = ["erstellt_am", "richtung", "typ", "status", "referenz", "kontrollnummer"]
    list_filter = ["richtung", "typ", "status"]


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = ["name", "aktiv", "token_anfang"]
    readonly_fields = ["token_anfang"]


for m in (Lead, Aktivitaet, Verkaufschance, Kundenpreis, Lieferung, ShopBestellung, EdiPartner, EdiArtikelZuordnung):
    admin.site.register(m)

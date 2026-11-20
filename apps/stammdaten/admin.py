from django.contrib import admin

from .models import (
    Adresse, Ansprechpartner, Arbeitsgang, Arbeitsplan, Artikel, Einheit, Geschaeftspartner, Konto,
    Steuersatz, Stueckliste, StuecklistenPosition, Zahlungsbedingung,
)


@admin.register(Artikel)
class ArtikelAdmin(admin.ModelAdmin):
    list_display = ["artikelnummer", "bezeichnung", "art", "einheit", "verkaufspreis", "aktiv"]
    list_filter = ["art", "aktiv", "chargenpflicht"]
    search_fields = ["artikelnummer", "bezeichnung"]


class AdresseInline(admin.TabularInline):
    model = Adresse
    extra = 0


class AnsprechpartnerInline(admin.TabularInline):
    model = Ansprechpartner
    extra = 0


@admin.register(Geschaeftspartner)
class GeschaeftspartnerAdmin(admin.ModelAdmin):
    list_display = ["nummer", "name", "ist_kunde", "ist_lieferant", "ust_id", "aktiv"]
    list_filter = ["ist_kunde", "ist_lieferant", "aktiv"]
    search_fields = ["nummer", "name", "ust_id"]
    inlines = [AdresseInline, AnsprechpartnerInline]


class StuecklistenPositionInline(admin.TabularInline):
    model = StuecklistenPosition
    extra = 0
    autocomplete_fields = ["komponente"]


@admin.register(Stueckliste)
class StuecklisteAdmin(admin.ModelAdmin):
    list_display = ["artikel", "version", "bezeichnung", "aktiv"]
    inlines = [StuecklistenPositionInline]


class ArbeitsgangInline(admin.TabularInline):
    model = Arbeitsgang
    extra = 0


@admin.register(Arbeitsplan)
class ArbeitsplanAdmin(admin.ModelAdmin):
    list_display = ["artikel", "version", "bezeichnung", "aktiv"]
    inlines = [ArbeitsgangInline]


@admin.register(Konto)
class KontoAdmin(admin.ModelAdmin):
    list_display = ["kontenrahmen", "nummer", "bezeichnung", "kontoart", "aktiv"]
    list_filter = ["kontenrahmen", "kontoart"]
    search_fields = ["nummer", "bezeichnung"]


admin.site.register([Einheit, Steuersatz, Zahlungsbedingung])

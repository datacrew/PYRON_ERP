from django.contrib import admin

from .models import ArtikelLager, Bestand, Charge, Inventur, InventurPosition, Lagerbewegung, Lagerort, Lagerplatz


class LagerplatzInline(admin.TabularInline):
    model = Lagerplatz
    extra = 0


@admin.register(Lagerort)
class LagerortAdmin(admin.ModelAdmin):
    list_display = ["code", "bezeichnung", "ist_wareneingang", "aktiv"]
    inlines = [LagerplatzInline]


@admin.register(Charge)
class ChargeAdmin(admin.ModelAdmin):
    list_display = ["chargennummer", "artikel", "lieferant", "lieferantencharge", "mhd"]
    search_fields = ["chargennummer", "lieferantencharge", "artikel__artikelnummer"]


@admin.register(ArtikelLager)
class ArtikelLagerAdmin(admin.ModelAdmin):
    list_display = ["artikel", "meldebestand", "mindestbestand", "bestand_gesamt", "durchschnittspreis", "qs_pflicht"]
    readonly_fields = ["bestand_gesamt", "durchschnittspreis"]


class NurLesenAdmin(admin.ModelAdmin):
    """Bestände und Journal werden nur über die Buchungslogik verändert."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Bestand)
class BestandAdmin(NurLesenAdmin):
    list_display = ["artikel", "lagerort", "lagerplatz", "charge", "bestandsart", "menge"]
    list_filter = ["lagerort", "bestandsart"]


@admin.register(Lagerbewegung)
class LagerbewegungAdmin(NurLesenAdmin):
    list_display = ["pk", "buchungsdatum", "art", "artikel", "charge", "menge", "wert", "beleg_nummer", "benutzer"]
    list_filter = ["art"]
    search_fields = ["artikel__artikelnummer", "beleg_nummer", "charge__chargennummer"]


class InventurPositionInline(admin.TabularInline):
    model = InventurPosition
    extra = 0


@admin.register(Inventur)
class InventurAdmin(admin.ModelAdmin):
    list_display = ["nummer", "lagerort", "stichtag", "status"]
    inlines = [InventurPositionInline]

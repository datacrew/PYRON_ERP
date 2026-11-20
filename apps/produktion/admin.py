from django.contrib import admin

from .models import (
    Anwesenheit, FAArbeitsgang, FAMaterial, Fertigmeldung, Fertigungsauftrag, Materialentnahme, Rueckmeldung,
    Stempelung,
)


class MaterialInline(admin.TabularInline):
    model = FAMaterial
    fk_name = "auftrag"
    extra = 0


class ArbeitsgangInline(admin.TabularInline):
    model = FAArbeitsgang
    extra = 0


@admin.register(Fertigungsauftrag)
class FertigungsauftragAdmin(admin.ModelAdmin):
    list_display = ("nummer", "artikel", "menge", "gut_menge", "status", "termin")
    list_filter = ("status",)
    search_fields = ("nummer", "artikel__artikelnummer")
    inlines = [MaterialInline, ArbeitsgangInline]


@admin.register(Stempelung)
class StempelungAdmin(admin.ModelAdmin):
    list_display = ("mitarbeiter", "arbeitsgang", "taetigkeit", "beginn", "ende", "ende_art", "quelle", "korrigiert")
    list_filter = ("taetigkeit", "quelle", "korrigiert")


@admin.register(Anwesenheit)
class AnwesenheitAdmin(admin.ModelAdmin):
    list_display = ("mitarbeiter", "kommen", "gehen")


@admin.register(Rueckmeldung)
class RueckmeldungAdmin(admin.ModelAdmin):
    list_display = ("arbeitsgang", "mitarbeiter", "zeitpunkt", "gut_menge", "ausschuss_menge", "ausschuss_grund")


# Lagerrelevante Belege nur lesend (Korrekturen ausschließlich über Gegenbuchungen im Lager)
@admin.register(Materialentnahme, Fertigmeldung)
class NurLesenAdmin(admin.ModelAdmin):
    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False

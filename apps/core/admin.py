from django.contrib import admin

from .models import AuditLog, Mandant, Modul, Nummernkreis, Recht, Rolle


class RechtInline(admin.TabularInline):
    model = Recht
    extra = 0


@admin.register(Rolle)
class RolleAdmin(admin.ModelAdmin):
    list_display = ["name", "beschreibung"]
    filter_horizontal = ["benutzer"]
    inlines = [RechtInline]


@admin.register(Modul)
class ModulAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "aktiv", "pflicht", "reihenfolge"]
    list_editable = ["aktiv"]
    readonly_fields = ["code", "pflicht", "geplant"]


@admin.register(Nummernkreis)
class NummernkreisAdmin(admin.ModelAdmin):
    list_display = ["code", "bezeichnung", "praefix", "jahresbezogen", "naechste_nummer", "aktuelles_jahr"]
    readonly_fields = ["naechste_nummer", "aktuelles_jahr"]


admin.site.register(Mandant)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ["zeitpunkt", "benutzername", "aktion", "app_label", "modell", "objekt_text"]
    list_filter = ["aktion", "app_label"]
    search_fields = ["objekt_text", "benutzername"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

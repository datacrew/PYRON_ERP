from django.contrib import admin

from .models import Wareneingang, WEPosition


class WEPositionInline(admin.TabularInline):
    model = WEPosition
    extra = 0
    can_delete = False

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Wareneingang)
class WareneingangAdmin(admin.ModelAdmin):
    list_display = ["nummer", "datum", "lieferant", "bestellung", "lieferschein_nr", "lagerort"]
    search_fields = ["nummer", "lieferschein_nr", "lieferant__name"]
    inlines = [WEPositionInline]

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False

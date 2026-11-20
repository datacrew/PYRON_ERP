from django.contrib import admin

from .models import Fahrzeug, LvsProtokoll, PlzKoordinate, Region, Tour, TourStopp


class TourStoppInline(admin.TabularInline):
    model = TourStopp
    extra = 0


@admin.register(Tour)
class TourAdmin(admin.ModelAdmin):
    list_display = ["nummer", "datum", "region", "fahrzeug", "status"]
    list_filter = ["status", "region"]
    inlines = [TourStoppInline]


for m in (Region, Fahrzeug, PlzKoordinate, LvsProtokoll):
    admin.site.register(m)

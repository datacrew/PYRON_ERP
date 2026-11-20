from django.contrib import admin

from .models import Berichtsvariante


@admin.register(Berichtsvariante)
class BerichtsvarianteAdmin(admin.ModelAdmin):
    list_display = ("name", "bericht", "benutzer", "favorit", "erstellt_am")
    list_filter = ("favorit", "bericht")

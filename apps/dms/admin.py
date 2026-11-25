from django.contrib import admin

from .models import Kontierungsregel, MailEingang, Tag

# Dokumente, Versionen und Protokoll bewusst nicht im Admin (GoBD: Änderungen nur über die DMS-Oberfläche).
admin.site.register(Tag)
admin.site.register(Kontierungsregel)


@admin.register(MailEingang)
class MailEingangAdmin(admin.ModelAdmin):
    list_display = ["erstellt_am", "absender", "betreff", "status", "anhaenge", "versuche", "stapel"]
    list_filter = ["status"]
    search_fields = ["absender", "betreff", "message_id"]
    readonly_fields = [f.name for f in MailEingang._meta.fields]

    def has_add_permission(self, request):
        return False

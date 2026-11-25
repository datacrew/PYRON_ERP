from django.contrib import admin

from .models import Kontierungsregel, Tag

# Dokumente, Versionen und Protokoll bewusst nicht im Admin (GoBD: Änderungen nur über die DMS-Oberfläche).
admin.site.register(Tag)
admin.site.register(Kontierungsregel)

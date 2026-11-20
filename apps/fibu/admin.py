from django.contrib import admin

from .models import (Bankkonto, Basiszinssatz, Geschaeftsjahr, Kontierungsregel, Mahnstufe, Periode,
                     Steuerschluessel)

# Buchungen, OPs und Bankumsätze bewusst nicht im Admin (GoBD: Änderungen nur über die Fibu-Oberfläche/Storno).
for model in (Bankkonto, Basiszinssatz, Geschaeftsjahr, Kontierungsregel, Mahnstufe, Periode, Steuerschluessel):
    admin.site.register(model)

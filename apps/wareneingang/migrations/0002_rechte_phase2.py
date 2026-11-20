"""Einmalige Rechte-Ergänzung für bereits bestehende Rollen (Phase 2).

Neue Module (z. B. Lager) ergänzt `erp_grunddaten` automatisch. Hier wird nur ein bestehendes Recht erweitert:
die Rolle „Einkauf“ darf Wareneingänge jetzt auch buchen (anlegen). Rechte werden nur hinzugefügt, nie entzogen.
"""
from django.db import migrations


def ergaenzen(apps, schema_editor):
    Recht = apps.get_model("core", "Recht")
    Recht.objects.filter(rolle__name="Einkauf", modul__code="wareneingang").update(lesen=True, anlegen=True)


class Migration(migrations.Migration):
    dependencies = [("wareneingang", "0001_initial"), ("core", "0001_initial")]
    operations = [migrations.RunPython(ergaenzen, migrations.RunPython.noop)]

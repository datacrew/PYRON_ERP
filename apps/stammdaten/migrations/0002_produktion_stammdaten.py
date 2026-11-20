# Phase 3: Arbeitsplätze, Mitarbeiter, Arbeitsgang mit Arbeitsplatz-Verweis und Lohnart.
# Bisherige Freitext-Arbeitsplätze werden in Arbeitsplatz-Datensätze überführt, „akkordfähig“ in die Lohnart.
import re

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def uebernehmen(apps, schema_editor):
    Arbeitsgang = apps.get_model("stammdaten", "Arbeitsgang")
    Arbeitsplatz = apps.get_model("stammdaten", "Arbeitsplatz")
    for ag in Arbeitsgang.objects.all():
        ag.lohnart = "AKKORD" if ag.akkordfaehig else "ZEITLOHN"
        text = (ag.arbeitsplatz or "").strip()
        if text:
            treffer = re.search(r"\b([A-Z]{2,4}-\d+)\b", text)
            code = (treffer.group(1) if treffer else text)[:20]
            ag.platz_neu, _ = Arbeitsplatz.objects.get_or_create(code=code, defaults={"bezeichnung": text[:100]})
        ag.save(update_fields=["lohnart", "platz_neu"])


class Migration(migrations.Migration):

    dependencies = [
        ('stammdaten', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Arbeitsplatz',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=20, unique=True, verbose_name='Code')),
                ('bezeichnung', models.CharField(max_length=100, verbose_name='Bezeichnung')),
                ('art', models.CharField(choices=[('MASCHINE', 'Maschine / Anlage'), ('HAND', 'Handarbeitsplatz')], default='MASCHINE', max_length=10, verbose_name='Art')),
                ('kostenstelle', models.CharField(blank=True, max_length=20, verbose_name='Kostenstelle')),
                ('stundensatz', models.DecimalField(decimal_places=2, default=0, help_text='Maschinen-/Platzkosten je Stunde ohne Lohn (für die Herstellkosten)', max_digits=10, verbose_name='Platzkostensatz (€/h)')),
                ('aktiv', models.BooleanField(default=True, verbose_name='Aktiv')),
            ],
            options={
                'verbose_name': 'Arbeitsplatz',
                'verbose_name_plural': 'Arbeitsplätze',
                'ordering': ['code'],
            },
        ),
        migrations.AddField(
            model_name='arbeitsgang',
            name='lohnart',
            field=models.CharField(choices=[('AKKORD', 'Akkord (Zeitakkord)'), ('ZEITLOHN', 'Zeitlohn')], default='AKKORD', max_length=10, verbose_name='Lohnart'),
        ),
        migrations.AddField(
            model_name='arbeitsgang',
            name='platz_neu',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='stammdaten.arbeitsplatz'),
        ),
        migrations.RunPython(uebernehmen, migrations.RunPython.noop),
        migrations.RemoveField(model_name='arbeitsgang', name='akkordfaehig'),
        migrations.RemoveField(model_name='arbeitsgang', name='arbeitsplatz'),
        migrations.RenameField(model_name='arbeitsgang', old_name='platz_neu', new_name='arbeitsplatz'),
        migrations.AlterField(
            model_name='arbeitsgang',
            name='stueckzeit_min',
            field=models.DecimalField(decimal_places=3, default=0, max_digits=8, verbose_name='Vorgabezeit te je Stück (min)'),
        ),
        migrations.AlterField(
            model_name='arbeitsgang',
            name='arbeitsplatz',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='arbeitsgaenge', to='stammdaten.arbeitsplatz', verbose_name='Arbeitsplatz'),
        ),
        migrations.CreateModel(
            name='Mitarbeiter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('personalnummer', models.CharField(max_length=20, unique=True, verbose_name='Personalnummer')),
                ('ausweisnummer', models.CharField(blank=True, max_length=30, verbose_name='Ausweis-/Stempelkarten-Nr.')),
                ('vorname', models.CharField(max_length=60, verbose_name='Vorname')),
                ('nachname', models.CharField(max_length=60, verbose_name='Nachname')),
                ('lohngruppe', models.CharField(blank=True, max_length=20, verbose_name='Lohngruppe')),
                ('stundenlohn', models.DecimalField(decimal_places=2, default=0, help_text='Zeitlohn und garantierter Mindestverdienst im Akkord', max_digits=8, verbose_name='Grund-/Stundenlohn (€/h)')),
                ('akkordrichtsatz', models.DecimalField(decimal_places=2, default=0, help_text='Verdienst bei Normalleistung (Leistungsgrad 100 %)', max_digits=8, verbose_name='Akkordrichtsatz (€/h)')),
                ('aktiv', models.BooleanField(default=True, verbose_name='Aktiv')),
                ('benutzer', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='mitarbeiter', to=settings.AUTH_USER_MODEL, verbose_name='Benutzerkonto')),
            ],
            options={
                'verbose_name': 'Mitarbeiter',
                'verbose_name_plural': 'Mitarbeiter',
                'ordering': ['personalnummer'],
                'constraints': [models.UniqueConstraint(condition=models.Q(('ausweisnummer', ''), _negated=True), fields=('ausweisnummer',), name='stammdaten_mitarbeiter_ausweis_eindeutig')],
            },
        ),
    ]

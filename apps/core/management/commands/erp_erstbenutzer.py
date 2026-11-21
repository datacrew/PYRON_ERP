"""Legt den ersten Administrator eines Produktivsystems an (ohne Demodaten).

    python manage.py erp_erstbenutzer --benutzername mmueller --email m.mueller@firma.de
    ERP_ADMIN_PASSWORT=... python manage.py erp_erstbenutzer --benutzername admin --ohne-abfrage

Das Passwort wird interaktiv abgefragt (oder aus ERP_ADMIN_PASSWORT gelesen) und gegen die
Passwortrichtlinien geprüft. Der Benutzer wird Systemadministrator und erhält die Rolle „Administrator“.
"""
import getpass
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import Rolle


class Command(BaseCommand):
    help = "Legt einen Administrator-Benutzer an (erste Anmeldung im Produktivsystem)."

    def add_arguments(self, parser):
        parser.add_argument("--benutzername", required=True)
        parser.add_argument("--email", default="")
        parser.add_argument("--vorname", default="")
        parser.add_argument("--nachname", default="")
        parser.add_argument("--ohne-abfrage", action="store_true",
                            help="Passwort ausschließlich aus der Umgebungsvariable ERP_ADMIN_PASSWORT lesen")
        parser.add_argument("--passwort-setzen", action="store_true",
                            help="Benutzer existiert bereits: Passwort neu setzen und Adminrechte vergeben")

    def handle(self, *args, **opt):
        User = get_user_model()
        name = opt["benutzername"].strip()
        if not name:
            raise CommandError("Benutzername darf nicht leer sein.")
        vorhanden = User.objects.filter(username=name).first()
        if vorhanden and not opt["passwort_setzen"]:
            raise CommandError(f"Benutzer „{name}“ existiert bereits (zum Zurücksetzen --passwort-setzen angeben).")

        passwort = os.environ.get("ERP_ADMIN_PASSWORT", "")
        if not passwort:
            if opt["ohne_abfrage"]:
                raise CommandError("ERP_ADMIN_PASSWORT ist nicht gesetzt.")
            passwort = getpass.getpass("Passwort: ")
            if passwort != getpass.getpass("Passwort (Wiederholung): "):
                raise CommandError("Die Passwörter stimmen nicht überein.")
        kandidat = vorhanden or User(username=name, email=opt["email"],
                                     first_name=opt["vorname"], last_name=opt["nachname"])
        try:
            validate_password(passwort, kandidat)
        except ValidationError as e:
            raise CommandError("Passwort abgelehnt: " + " ".join(e.messages))

        with transaction.atomic():
            user = kandidat
            if opt["email"]:
                user.email = opt["email"]
            user.is_superuser = user.is_staff = user.is_active = True
            user.set_password(passwort)
            user.save()
            rolle = Rolle.objects.filter(name="Administrator").first()
            if rolle:
                rolle.benutzer.add(user)
        aktion = "aktualisiert" if vorhanden else "angelegt"
        self.stdout.write(self.style.SUCCESS(f"Administrator „{name}“ {aktion}."))

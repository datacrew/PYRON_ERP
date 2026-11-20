from django import forms
from django.urls import path

from apps.core.generic import crud_urls

from . import views
from .models import (
    Adresse, Ansprechpartner, Arbeitsgang, Arbeitsplan, Artikel, Einheit, Geschaeftspartner, Konto,
    Steuersatz, Stueckliste, StuecklistenPosition, Zahlungsbedingung,
)

app_name = "stammdaten"
M = "stammdaten"
TEXT2 = {"beschreibung": forms.Textarea(attrs={"rows": 3})}

urlpatterns = [
    path("", views.index, name="index"),
    path("stueckliste/<int:pk>/aufloesung/", views.stueckliste_aufloesung, name="stueckliste_aufloesung"),
]

urlpatterns += crud_urls(
    "artikel", Artikel, M,
    felder=["artikelnummer", "bezeichnung", "art", "einheit", "steuersatz", "einkaufspreis", "verkaufspreis",
            "chargenpflicht", "gewicht_kg", "ean", "zolltarifnummer", "aktiv", "beschreibung"],
    spalten=[("Artikelnr.", "artikelnummer"), ("Bezeichnung", "bezeichnung"), ("Art", "art"),
             ("Einheit", "einheit"), ("VK netto", "verkaufspreis"), ("Charge", "chargenpflicht"), ("Aktiv", "aktiv")],
    suchfelder=["artikelnummer", "bezeichnung", "ean"], widgets=TEXT2,
    queryset=Artikel.objects.select_related("einheit"),
)
urlpatterns += crud_urls(
    "partner", Geschaeftspartner, M,
    felder=["nummer", "name", "name2", "ist_kunde", "ist_lieferant", "ust_id", "steuernummer", "leitweg_id",
            "email", "email_rechnung", "telefon", "zahlungsbedingung", "debitorenkonto", "kreditorenkonto",
            "gln", "aktiv"],
    spalten=[("Nr.", "nummer"), ("Name", "name"), ("Rolle", "rollen_text"), ("USt-IdNr.", "ust_id"),
             ("Ort", "rechnungsadresse.ort"), ("Aktiv", "aktiv")],
    suchfelder=["nummer", "name", "name2", "ust_id"],
    inlines=[("Adressen", Adresse, ["typ", "zusatz", "strasse", "plz", "ort", "land"]),
             ("Ansprechpartner", Ansprechpartner, ["anrede", "vorname", "nachname", "funktion", "email", "telefon"])],
    queryset=Geschaeftspartner.objects.prefetch_related("adressen"),
)
urlpatterns += crud_urls(
    "stueckliste", Stueckliste, M,
    felder=["artikel", "version", "bezeichnung", "basismenge", "aktiv"],
    spalten=[("Erzeugnis", "artikel"), ("Version", "version"), ("Bezeichnung", "bezeichnung"), ("Aktiv", "aktiv")],
    suchfelder=["artikel__artikelnummer", "artikel__bezeichnung", "bezeichnung"],
    inlines=[("Komponenten", StuecklistenPosition, ["position", "komponente", "menge", "bemerkung"])],
    queryset=Stueckliste.objects.select_related("artikel"),
    zusatz_template="stammdaten/_stueckliste_link.html",
)
urlpatterns += crud_urls(
    "arbeitsplan", Arbeitsplan, M,
    felder=["artikel", "version", "bezeichnung", "aktiv"],
    spalten=[("Erzeugnis", "artikel"), ("Version", "version"), ("Bezeichnung", "bezeichnung"), ("Aktiv", "aktiv")],
    suchfelder=["artikel__artikelnummer", "bezeichnung"],
    inlines=[("Arbeitsgänge", Arbeitsgang,
              ["position", "bezeichnung", "arbeitsplatz", "ruestzeit_min", "stueckzeit_min", "lohngruppe",
               "akkordfaehig"])],
    queryset=Arbeitsplan.objects.select_related("artikel"),
)
urlpatterns += crud_urls(
    "konto", Konto, M, felder=["kontenrahmen", "nummer", "bezeichnung", "kontoart", "aktiv"],
    spalten=[("Rahmen", "kontenrahmen"), ("Konto", "nummer"), ("Bezeichnung", "bezeichnung"),
             ("Kontoart", "kontoart"), ("Aktiv", "aktiv")],
    suchfelder=["nummer", "bezeichnung", "kontenrahmen"],
)
urlpatterns += crud_urls(
    "steuersatz", Steuersatz, M, felder=["code", "bezeichnung", "satz", "aktiv"],
    spalten=[("Code", "code"), ("Bezeichnung", "bezeichnung"), ("Satz %", "satz"), ("Aktiv", "aktiv")],
)
urlpatterns += crud_urls(
    "einheit", Einheit, M, felder=["code", "bezeichnung"],
    spalten=[("Kürzel", "code"), ("Bezeichnung", "bezeichnung")],
)
urlpatterns += crud_urls(
    "zahlungsbedingung", Zahlungsbedingung, M,
    felder=["code", "bezeichnung", "tage_netto", "skonto_tage", "skonto_prozent"],
    spalten=[("Code", "code"), ("Text", "bezeichnung"), ("Netto Tage", "tage_netto"),
             ("Skonto Tage", "skonto_tage"), ("Skonto %", "skonto_prozent")],
)

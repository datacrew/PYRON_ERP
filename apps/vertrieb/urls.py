from django import forms
from django.urls import path

from apps.core.generic import crud_urls

from . import views
from .models import Aktivitaet, EdiArtikelZuordnung, EdiPartner, Kundenpreis, Lead, Shop, Verkaufschance

app_name = "vertrieb"

urlpatterns = [
    path("", views.index, name="index"),
    path("pipeline/", views.pipeline, name="pipeline"),
    path("leads/<int:pk>/umwandeln/", views.lead_umwandeln, name="lead_umwandeln"),
    path("aktivitaeten/<int:pk>/erledigt/", views.wiedervorlage_erledigt, name="wiedervorlage_erledigt"),
    path("angebote/", views.angebote, name="angebote"),
    path("angebote/neu/", views.angebot_neu, name="angebot_neu"),
    path("angebote/<int:pk>/uebernehmen/", views.angebot_uebernehmen, name="angebot_uebernehmen"),
    path("auftraege/", views.auftraege, name="auftraege"),
    path("auftraege/neu/", views.auftrag_neu, name="auftrag_neu"),
    path("auftraege/<int:pk>/", views.auftrag_detail, name="auftrag"),
    path("auftraege/<int:pk>/<slug:aktion>/", views.auftrag_aktion, name="auftrag_aktion"),
    path("lieferscheine/", views.lieferscheine, name="lieferscheine"),
    path("lieferscheine/sammelrechnung/", views.sammelrechnung, name="sammelrechnung"),
    path("lieferscheine/<int:pk>/", views.lieferschein_detail, name="lieferschein"),
    path("lieferscheine/<int:pk>/<slug:aktion>/", views.lieferschein_aktion, name="lieferschein_aktion"),
    path("rechnungen/", views.rechnungen, name="rechnungen"),
    path("rechnungen/<int:pk>/", views.rechnung_detail, name="rechnung"),
    path("rechnungen/<int:pk>/datei/<slug:art>/", views.rechnung_datei, name="rechnung_datei"),
    path("rechnungen/<int:pk>/<slug:aktion>/", views.rechnung_aktion, name="rechnung_aktion"),
    path("edi/", views.edi, name="edi"),
    path("edi/<int:pk>/", views.edi_nachricht, name="edi_nachricht"),
    path("edi/<int:pk>/uebertragen/", views.edi_uebertragen, name="edi_uebertragen"),
    path("shops/", views.shops, name="shops"),
]

TEXT = {"bemerkung": forms.Textarea(attrs={"rows": 3}), "notiz": forms.Textarea(attrs={"rows": 3})}
urlpatterns += crud_urls(
    "leads", Lead, "vertrieb",
    ["firma", "ansprechpartner", "email", "telefon", "strasse", "plz", "ort", "land", "quelle", "status",
     "zustaendig", "bemerkung"],
    [("Firma", "firma"), ("Ort", "ort"), ("Status", "get_status_display"), ("Zuständig", "zustaendig"),
     ("Kunde", "partner")],
    suchfelder=["firma", "ort", "ansprechpartner"], widgets=TEXT,
    inlines=[("Aktivitäten", Aktivitaet, ["art", "betreff", "zeitpunkt", "wiedervorlage", "erledigt", "zustaendig"])],
    zusatz_template="vertrieb/_lead_zusatz.html")
urlpatterns += crud_urls(
    "aktivitaeten", Aktivitaet, "vertrieb",
    ["art", "lead", "partner", "betreff", "notiz", "zeitpunkt", "wiedervorlage", "erledigt", "zustaendig"],
    [("Zeitpunkt", "zeitpunkt"), ("Art", "get_art_display"), ("Betreff", "betreff"), ("Lead/Kunde", "bezug"),
     ("Wiedervorlage", "wiedervorlage"), ("Erledigt", "erledigt")],
    suchfelder=["betreff", "lead__firma", "partner__name"], widgets=TEXT)
urlpatterns += crud_urls(
    "chancen", Verkaufschance, "vertrieb",
    ["titel", "lead", "partner", "phase", "wert", "wahrscheinlichkeit", "abschluss_erwartet", "zustaendig", "angebot"],
    [("Titel", "titel"), ("Kunde", "partner"), ("Phase", "get_phase_display"), ("Wert", "wert"),
     ("%", "wahrscheinlichkeit"), ("Abschluss", "abschluss_erwartet")],
    suchfelder=["titel", "partner__name", "lead__firma"])
urlpatterns += crud_urls(
    "kundenpreise", Kundenpreis, "vertrieb",
    ["partner", "artikel", "ab_menge", "preis", "rabatt_prozent", "gueltig_ab", "gueltig_bis"],
    [("Artikel", "artikel"), ("Kunde", "partner"), ("Ab Menge", "ab_menge"), ("Preis", "preis"),
     ("Rabatt %", "rabatt_prozent"), ("Gültig bis", "gueltig_bis")],
    suchfelder=["artikel__artikelnummer", "partner__name"])
urlpatterns += crud_urls(
    "edipartner", EdiPartner, "edi",
    ["partner", "gln", "rechnungsempfaenger_gln", "ordrsp", "desadv", "invoic", "testbetrieb", "aktiv"],
    [("Partner", "partner"), ("GLN", "gln"), ("ORDRSP", "ordrsp"), ("DESADV", "desadv"), ("INVOIC", "invoic"),
     ("Test", "testbetrieb")],
    suchfelder=["partner__name", "gln"],
    inlines=[("Kundenartikelnummern", EdiArtikelZuordnung, ["kundenartikelnummer", "artikel"])])
urlpatterns += crud_urls(
    "shop", Shop, "shopapi", ["name", "aktiv", "zahlungsbedingung", "lagerort", "auto_bestaetigen"],
    [("Name", "name"), ("Aktiv", "aktiv"), ("Token", "token_anfang"), ("Auto-Bestätigung", "auto_bestaetigen")],
    suchfelder=["name"])

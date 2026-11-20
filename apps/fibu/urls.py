from django.urls import path

from apps.core.generic import crud_urls

from . import views
from .models import Bankkonto, Basiszinssatz, Kontierungsregel, Mahnstufe, Steuerschluessel

app_name = "fibu"

urlpatterns = [
    path("", views.index, name="index"),
    path("nachbuchen/", views.nachbuchen_view, name="nachbuchen"),
    path("konten/", views.konten, name="konten"),
    path("konten/<int:pk>/", views.kontoblatt, name="kontoblatt"),
    path("buchungen/", views.buchungen, name="buchungen"),
    path("buchungen/neu/", views.buchung_neu, name="buchung_neu"),
    path("buchungen/festschreiben/", views.buchungen_festschreiben, name="buchungen_festschreiben"),
    path("buchungen/kette/", views.kette, name="kette"),
    path("buchungen/<int:pk>/", views.buchung_detail, name="buchung"),
    path("buchungen/<int:pk>/festschreiben/", views.buchung_festschreiben, name="buchung_festschreiben"),
    path("buchungen/<int:pk>/storno/", views.buchung_storno, name="buchung_storno"),
    path("op/", views.op_liste, name="op_liste"),
    path("op/<int:pk>/", views.op_detail, name="op"),
    path("op/<int:pk>/mahnsperre/", views.op_mahnsperre, name="op_mahnsperre"),
    path("eingangsrechnungen/", views.er_liste, name="er_liste"),
    path("eingangsrechnungen/neu/", views.er_neu, name="er_neu"),
    path("eingangsrechnungen/<int:pk>/", views.er_detail, name="er"),
    path("bank/", views.bank, name="bank"),
    path("bank/import/", views.bank_import, name="bank_import"),
    path("bank/automatisch/", views.bank_auto, name="bank_auto"),
    path("bank/umsatz/<int:pk>/", views.bank_umsatz, name="bank_umsatz"),
    path("mahnwesen/", views.mahnwesen, name="mahnwesen"),
    path("mahnwesen/lauf/<int:pk>/", views.mahnlauf, name="mahnlauf"),
    path("mahnwesen/mahnung/<int:pk>/pdf/", views.mahnung_pdf, name="mahnung_pdf"),
    path("perioden/", views.perioden, name="perioden"),
    path("auswertungen/", views.auswertungen_index, name="auswertungen"),
    path("auswertungen/datev/", views.datev, name="datev"),
    path("auswertungen/<slug:art>/", views.auswertung, name="auswertung"),
]

# Einstellungen (Recht fibu/aendern)
urlpatterns += crud_urls(
    "bankkonten", Bankkonto, "fibu", felder=["bezeichnung", "iban", "bic", "konto", "aktiv"],
    spalten=[("Bezeichnung", "bezeichnung"), ("IBAN", "iban"), ("BIC", "bic"), ("Sachkonto", "konto"),
             ("Aktiv", "aktiv")],
    suchfelder=["bezeichnung", "iban"], titel="Bankkonto", titel_plural="Bankkonten")
urlpatterns += crud_urls(
    "steuerschluessel", Steuerschluessel, "fibu",
    felder=["kontenrahmen", "code", "bezeichnung", "art", "satz", "steuerkonto", "gegenkonto", "kz_bemessung",
            "kz_steuer", "kz_vorsteuer", "datev_bu", "aktiv"],
    spalten=[("Rahmen", "kontenrahmen"), ("Schlüssel", "code"), ("Bezeichnung", "bezeichnung"), ("Art", "art"),
             ("Satz %", "satz"), ("Steuerkonto", "steuerkonto"), ("Kz", "kz_bemessung"), ("BU", "datev_bu")],
    suchfelder=["code", "bezeichnung"], titel="Steuerschlüssel", titel_plural="Steuerschlüssel")
urlpatterns += crud_urls(
    "kontierungsregeln", Kontierungsregel, "fibu",
    felder=["kontenrahmen", "vorgang", "artikelart", "steuerschluessel", "konto"],
    spalten=[("Rahmen", "kontenrahmen"), ("Vorgang", "vorgang"), ("Artikelart", "artikelart"),
             ("Steuerschlüssel", "steuerschluessel"), ("Konto", "konto")],
    suchfelder=["vorgang", "konto__nummer"], titel="Kontierungsregel", titel_plural="Kontierungsregeln")
urlpatterns += crud_urls(
    "mahnstufen", Mahnstufe, "fibu",
    felder=["stufe", "bezeichnung", "tage", "frist_tage", "gebuehr", "zinsen", "text"],
    spalten=[("Stufe", "stufe"), ("Bezeichnung", "bezeichnung"), ("Tage", "tage"), ("Frist", "frist_tage"),
             ("Gebühr €", "gebuehr"), ("Zinsen", "zinsen")],
    titel="Mahnstufe", titel_plural="Mahnstufen")
urlpatterns += crud_urls(
    "basiszinssaetze", Basiszinssatz, "fibu", felder=["gueltig_ab", "satz"],
    spalten=[("Gültig ab", "gueltig_ab"), ("Basiszinssatz %", "satz")],
    titel="Basiszinssatz", titel_plural="Basiszinssätze")

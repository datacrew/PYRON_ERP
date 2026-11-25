"""Fachlogik DMS: Eingang, Versionen, Protokoll, Archivierung, Volltextsuche.

Nummern ausschließlich über apps.core.nummernkreise (DMS_DOKUMENT „DOK-“, DMS_STAPEL „ST-“).
"""
import hashlib
import mimetypes
from pathlib import Path

from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.core.nummernkreise import naechste_nummer

from .models import (Dokument, DokumentProtokoll, DokumentVersion, DmsGesperrt, Dokumentart, Stapel,
                     aufbewahren_bis)

ERLAUBTE_ENDUNGEN = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".xml"}
MAX_GROESSE = 50 * 1024 * 1024  # 50 MB je Datei


class DmsFehler(Exception):
    """Fachlicher Fehler im DMS (wird dem Benutzer angezeigt)."""


def _user(benutzer):
    return benutzer if getattr(benutzer, "pk", None) else None


def sha256(daten):
    return hashlib.sha256(daten).hexdigest()


def protokollieren(dokument, aktion, text="", benutzer=None):
    u = _user(benutzer)
    return DokumentProtokoll.objects.create(dokument=dokument, aktion=aktion, text=text[:5000], benutzer=u,
                                            benutzername=u.get_username() if u else "System")


def datei_pruefen(dateiname, daten):
    endung = Path(dateiname).suffix.lower()
    if endung not in ERLAUBTE_ENDUNGEN:
        raise DmsFehler(f"„{dateiname}“: Dateityp {endung or '(ohne Endung)'} wird nicht unterstützt "
                        f"(erlaubt: PDF, PNG, JPEG, TIFF, XML).")
    if not daten:
        raise DmsFehler(f"„{dateiname}“ ist leer.")
    if len(daten) > MAX_GROESSE:
        raise DmsFehler(f"„{dateiname}“ ist größer als {MAX_GROESSE // (1024 * 1024)} MB.")
    if endung == ".pdf" and not daten.lstrip()[:5].startswith(b"%PDF"):
        raise DmsFehler(f"„{dateiname}“ ist keine gültige PDF-Datei.")


def stapel_anlegen(quelle=Stapel.Quelle.UPLOAD, bezeichnung="", art=Dokumentart.EINGANGSRECHNUNG,
                   automatisch_trennen=True, benutzer=None):
    return Stapel.objects.create(nummer=naechste_nummer("DMS_STAPEL"), quelle=quelle, bezeichnung=bezeichnung[:200],
                                 art=art, automatisch_trennen=automatisch_trennen, erstellt_von=_user(benutzer))


def version_speichern(dokument, daten, dateiname, art=DokumentVersion.Art.ORIGINAL, quelle=None, kommentar="",
                      benutzer=None, version=None):
    """Legt eine neue, mit SHA-256 versiegelte Version an (Datei wird nie überschrieben)."""
    if version is None:
        version = quelle.version if quelle else (
            (dokument.versionen.filter(art=DokumentVersion.Art.ORIGINAL).order_by("-version")
             .values_list("version", flat=True).first() or 0) + 1)
    v = DokumentVersion(dokument=dokument, version=version, art=art, quelle=quelle, dateiname=dateiname[-255:],
                        mime=mimetypes.guess_type(dateiname)[0] or "application/octet-stream",
                        groesse=len(daten), sha256=sha256(daten), kommentar=kommentar[:255],
                        erstellt_von=_user(benutzer))
    v.datei.save(Path(dateiname).name, ContentFile(daten), save=False)
    v.save()
    return v


def dublette_datei(daten):
    """Bereits abgelegtes Dokument mit identischer Originaldatei (gleicher SHA-256) oder None."""
    v = (DokumentVersion.objects.filter(sha256=sha256(daten), art=DokumentVersion.Art.ORIGINAL)
         .select_related("dokument").order_by("pk").first())
    return v.dokument if v else None


def dokument_anlegen(daten, dateiname, art=Dokumentart.SONSTIGES, titel="", stapel=None, partner=None,
                     benutzer=None, eltern=None, seiten_von=None, seiten_bis=None, quelle_text="Upload"):
    """Neuer Dokumenteingang: Prüfung, Nummer, Original-Version mit SHA-256, Protokoll. Status NEU (Warteschlange)."""
    datei_pruefen(dateiname, daten)
    with transaction.atomic():
        d = Dokument.objects.create(
            nummer=naechste_nummer("DMS_DOKUMENT"), titel=(titel or Path(dateiname).stem)[:200], art=art,
            stapel=stapel, partner=partner, eltern=eltern, seiten_von=seiten_von, seiten_bis=seiten_bis,
            erstellt_von=_user(benutzer))
        v = version_speichern(d, daten, dateiname, benutzer=benutzer)
        protokollieren(d, "Eingang", f"{quelle_text}: {dateiname} ({len(daten)} Bytes), SHA-256 {v.sha256}",
                       benutzer)
        suchvektor_aktualisieren(d)
    return d


def neue_version(dokument, daten, dateiname, kommentar="", benutzer=None):
    """Korrigierte/ergänzte Datei als neue Version – nur vor der Archivierung; danach Verarbeitung erneut."""
    if dokument.archiviert:
        raise DmsGesperrt("Archivierte Dokumente erhalten keine neuen Versionen.")
    datei_pruefen(dateiname, daten)
    with transaction.atomic():
        v = version_speichern(dokument, daten, dateiname, kommentar=kommentar, benutzer=benutzer)
        Dokument.objects.filter(pk=dokument.pk).update(status=Dokument.Status.NEU, fehlermeldung="")
        protokollieren(dokument, "Neue Version", f"V{v.version}: {dateiname}, SHA-256 {v.sha256}. {kommentar}",
                       benutzer)
    dokument.refresh_from_db()
    return v


def suchvektor_aktualisieren(dokument):
    """Volltextindex (PostgreSQL, Konfiguration german): Titel/Nummer stark, OCR-Text normal gewichtet."""
    Dokument.objects.filter(pk=dokument.pk).update(
        suchvektor=SearchVector("nummer", "titel", weight="A", config="german")
        + SearchVector("bemerkung", weight="B", config="german")
        + SearchVector("ocr_text", weight="C", config="german"))


def suchen(qs, begriff):
    """Volltextsuche (websearch-Syntax: „Wörter“, -ausschließen, oder) plus Nummern-/Titelsuche."""
    begriff = (begriff or "").strip()
    if not begriff:
        return qs
    anfrage = SearchQuery(begriff, config="german", search_type="websearch")
    return (qs.filter(Q(suchvektor=anfrage) | Q(nummer__icontains=begriff) | Q(titel__icontains=begriff))
            .annotate(rang=SearchRank(F("suchvektor"), anfrage)).order_by("-rang", "-erstellt_am"))


def archivieren(dokument, benutzer=None):
    """Revisionssichere Archivierung: unumkehrbar, Aufbewahrungsfrist ab Jahresende des Dokumentdatums."""
    if dokument.archiviert:
        return dokument
    if not dokument.versionen.exists():
        raise DmsFehler("Dokument ohne Datei kann nicht archiviert werden.")
    stichtag = dokument.belegdatum or timezone.localdate(dokument.erstellt_am)
    with transaction.atomic():
        dokument.archiviert, dokument.archiviert_am = True, timezone.now()
        dokument.archiviert_von = _user(benutzer)
        dokument.aufbewahren_bis = max(filter(None, [dokument.aufbewahren_bis,
                                                     aufbewahren_bis(dokument.art, stichtag)]))
        dokument._archivierung = True
        dokument.save()
        dokument._archivierung = False
        protokollieren(dokument, "Archiviert", f"Revisionssicher archiviert, aufzubewahren bis "
                                               f"{dokument.aufbewahren_bis:%d.%m.%Y}.", benutzer)
    return dokument


def loeschen(dokument, benutzer=None):
    """Löschen nur vor der Archivierung (z. B. Fehlscan); Dateien werden mit entfernt."""
    if dokument.archiviert:
        raise DmsGesperrt(f"Dokument {dokument.nummer} ist archiviert und darf nicht gelöscht werden.")
    if dokument.teile.exists():
        raise DmsFehler("Das Sammel-PDF wurde bereits aufgeteilt – bitte zuerst die Teildokumente löschen.")
    if dokument.eingangsrechnung_id or dokument.buchung_id:
        raise DmsFehler("Das Dokument ist mit einer Buchung/Eingangsrechnung verknüpft.")
    dateien = [v.datei.name for v in dokument.versionen.all()]
    speicher = DokumentVersion._meta.get_field("datei").storage
    with transaction.atomic():
        # Ableitungen zuerst (PROTECT auf quelle)
        dokument.versionen.exclude(quelle=None).delete()
        dokument.delete()
    for name in dateien:
        try:
            speicher.delete(name)
        except OSError:
            pass


def integritaet_pruefen():
    """Prüft alle Dateiversionen gegen ihren SHA-256 → (anzahl, fehlerliste)."""
    fehler, anzahl = [], 0
    for v in DokumentVersion.objects.select_related("dokument").order_by("pk"):
        anzahl += 1
        try:
            if not v.hash_pruefen():
                fehler.append(f"{v}: SHA-256 abweichend – Datei wurde verändert.")
        except (OSError, ValueError):
            fehler.append(f"{v}: Datei fehlt.")
    return anzahl, fehler

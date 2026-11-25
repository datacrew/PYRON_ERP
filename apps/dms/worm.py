"""WORM-Speicher (Write Once Read Many) für archivierte DMS-Dokumente.

Beim Archivieren wird jede Dateiversion zusätzlich inhaltsadressiert abgelegt (`worm/<sha[:2]>/<sha256>`) und in
einem Append-only-Manifest (`WormEintrag`, PostgreSQL-Trigger: nur INSERT) mit Hash-Kette eingetragen. Der
`WormSpeicher` verweigert Überschreiben und Löschen bereits abgelegter Dateien. `dms_worm_pruefen` prüft
Datei-Hashes und Hash-Kette.

Modi (`ERP_DMS_WORM_MODUS`):
* `aus` – keine WORM-Ablage (nur GoBD-Schutz in der Datenbank).
* `logisch` (Standard) – Content-Adressierung, Manifest, Schutz auf Applikationsebene.
* `os` – zusätzlich Betriebssystem-Schutz per `chattr +i` (nur Produktion, benötigt CAP_LINUX_IMMUTABLE; schlägt
  das fehl, bleibt der logische Schutz bestehen und es wird protokolliert). In Sandbox/Tests nie verwenden.

Optional S3 Object Lock (`ERP_DMS_WORM_S3_BUCKET`, boto3 nur bei Bedarf): Objekte werden im COMPLIANCE-Modus mit
„Retain until“ = Aufbewahrungsfrist des Dokuments hochgeladen.
"""
import hashlib
import logging
import os
import subprocess
from datetime import datetime, time, timezone as dt_tz
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.db import transaction

log = logging.getLogger(__name__)
NULL_HASH = "0" * 64


class WormVerletzung(Exception):
    """Versuch, eine WORM-Datei zu überschreiben oder zu löschen."""


def modus():
    m = (getattr(settings, "ERP_DMS_WORM_MODUS", "logisch") or "logisch").lower()
    return m if m in ("aus", "logisch", "os") else "logisch"


def wurzel():
    return Path(getattr(settings, "ERP_DMS_WORM_PFAD", "") or Path(settings.MEDIA_ROOT) / "worm")


class WormSpeicher(FileSystemStorage):
    """Dateisystem-Speicher ohne Überschreiben und Löschen (Schutz auf Applikationsebene)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("location", str(wurzel()))
        super().__init__(**kwargs)

    def get_available_name(self, name, max_length=None):
        if self.exists(name):
            raise WormVerletzung(f"WORM: {name} existiert bereits und darf nicht überschrieben werden.")
        return name

    def delete(self, name):
        raise WormVerletzung(f"WORM: {name} darf nicht gelöscht werden.")


def pfad_fuer(sha):
    return f"{sha[:2]}/{sha}"


def _sha(daten):
    return hashlib.sha256(daten).hexdigest()


def kettenhash(vorgaenger, sha, pfad, aufbewahren_bis, version_id):
    roh = f"{vorgaenger}|{sha}|{pfad}|{aufbewahren_bis or ''}|{version_id}"
    return hashlib.sha256(roh.encode()).hexdigest()


def _os_schutz(pfad):
    """chattr +i – nur im Modus `os` (Produktion). Fehler werden protokolliert, nie erzwungen."""
    try:
        subprocess.run(["chattr", "+i", str(pfad)], check=True, capture_output=True, timeout=10)
        return True
    except Exception as exc:  # noqa: BLE001 – OS-Schutz ist optional
        log.warning("WORM: chattr +i für %s nicht möglich (%s) – logischer Schutz bleibt aktiv.", pfad, exc)
        return False


def s3_client():
    import boto3  # optional, nur bei konfiguriertem S3-Bucket

    return boto3.client("s3", endpoint_url=getattr(settings, "ERP_DMS_WORM_S3_ENDPOINT", "") or None,
                        region_name=getattr(settings, "ERP_DMS_WORM_S3_REGION", "") or None)


def _s3_ablegen(sha, daten, aufbewahren_bis, client=None):
    bucket = getattr(settings, "ERP_DMS_WORM_S3_BUCKET", "")
    if not bucket:
        return ""
    client = client or s3_client()
    schluessel = f"{getattr(settings, 'ERP_DMS_WORM_S3_PRAEFIX', 'worm/')}{pfad_fuer(sha)}"
    args = {"Bucket": bucket, "Key": schluessel, "Body": daten, "ChecksumAlgorithm": "SHA256"}
    if aufbewahren_bis:
        args.update(ObjectLockMode="COMPLIANCE",
                    ObjectLockRetainUntilDate=datetime.combine(aufbewahren_bis, time.max, tzinfo=dt_tz.utc))
    client.put_object(**args)
    return f"s3://{bucket}/{schluessel}"


def ablegen(version, aufbewahren_bis=None, s3=None):
    """Version inhaltsadressiert ablegen und im Manifest verketten. Idempotent je Version."""
    from .models import WormEintrag

    if modus() == "aus":
        return None
    vorhanden = WormEintrag.objects.filter(version=version).first()
    if vorhanden:
        return vorhanden
    with version.datei.open("rb") as f:
        daten = f.read()
    sha = _sha(daten)
    if sha != version.sha256:
        raise WormVerletzung(f"WORM: Hash der Version {version.pk} weicht vom gespeicherten SHA-256 ab.")
    speicher, pfad = WormSpeicher(), pfad_fuer(sha)
    if speicher.exists(pfad):  # gleicher Inhalt bereits abgelegt (Content-Adressierung)
        with speicher.open(pfad, "rb") as f:
            if _sha(f.read()) != sha:
                raise WormVerletzung(f"WORM: {pfad} ist beschädigt.")
    else:
        speicher.save(pfad, ContentFile(daten))
        if modus() == "os":
            _os_schutz(speicher.path(pfad))
    s3_ziel = _s3_ablegen(sha, daten, aufbewahren_bis, s3)
    with transaction.atomic():
        letzter = WormEintrag.objects.select_for_update().order_by("-pk").first()
        vorgaenger = letzter.kettenhash if letzter else NULL_HASH
        return WormEintrag.objects.create(
            version=version, sha256=sha, pfad=pfad, groesse=len(daten), aufbewahren_bis=aufbewahren_bis,
            s3_ziel=s3_ziel, vorgaenger_hash=vorgaenger,
            kettenhash=kettenhash(vorgaenger, sha, pfad, aufbewahren_bis, version.pk))


def pruefen():
    """Alle Manifest-Einträge prüfen → Liste von Abweichungen (leer = in Ordnung)."""
    from .models import WormEintrag

    fehler, vorgaenger, speicher = [], NULL_HASH, WormSpeicher()
    for e in WormEintrag.objects.order_by("pk").iterator():
        if e.vorgaenger_hash != vorgaenger:
            fehler.append(f"Eintrag {e.pk}: Hash-Kette unterbrochen (Vorgänger passt nicht).")
        if e.kettenhash != kettenhash(e.vorgaenger_hash, e.sha256, e.pfad, e.aufbewahren_bis, e.version_id):
            fehler.append(f"Eintrag {e.pk}: Kettenhash stimmt nicht (Manifest verändert).")
        vorgaenger = e.kettenhash
        if not speicher.exists(e.pfad):
            fehler.append(f"Eintrag {e.pk}: WORM-Datei {e.pfad} fehlt.")
            continue
        with speicher.open(e.pfad, "rb") as f:
            if _sha(f.read()) != e.sha256:
                fehler.append(f"Eintrag {e.pk}: Inhalt von {e.pfad} weicht vom SHA-256 ab.")
    return fehler


def os_modus_moeglich():
    return os.name == "posix" and modus() == "os"

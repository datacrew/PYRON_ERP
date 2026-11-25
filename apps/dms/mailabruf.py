"""Rechnungseingang per E-Mail: IMAP-Postfach (SSL/TLS) abrufen und Anhänge als DMS-Stapel anlegen.

Ablauf je ungelesener Mail im Ordner ERP_DMS_IMAP_ORDNER:
1. Message-ID ermitteln (fehlt sie, dient ein SHA-256 der Rohmail als Ersatz) – bereits erfolgreich verarbeitete
   Message-IDs werden nicht erneut angelegt (Dublettenschutz, auch wenn die Mail wieder als ungelesen markiert wird).
2. Anhänge PDF, PNG/JPEG, TIFF und XML (XRechnung) übernehmen – ein Stapel je Mail (Quelle „E-Mail-Eingang“),
   Absender, Betreff und Message-ID stehen im Dokumentprotokoll und in den Metadaten. Kleine eingebettete Bilder
   (Signatur-Logos) werden ignoriert, Dateien mit gleichem SHA-256 wie ein vorhandenes Dokument ebenfalls.
3. Mail im Postfach kennzeichnen: in ERP_DMS_IMAP_ZIELORDNER verschieben (MOVE bzw. COPY + \\Deleted) oder – ohne
   Zielordner – als gelesen markieren. Mails ohne verwertbaren Anhang gehen in den Fehlerordner (falls gesetzt).
4. Technische Fehler je Mail werden protokolliert; die Mail bleibt ungelesen und wird beim nächsten Lauf erneut
   versucht, nach MAX_VERSUCHE Versuchen aufgegeben (Fehlerordner bzw. gelesen).
"""
import email
import hashlib
import imaplib
import logging
import re
import ssl
from dataclasses import dataclass, field
from email import policy
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from . import services as S
from .models import Dokument, MailEingang, Stapel

log = logging.getLogger(__name__)

MAX_VERSUCHE = 3
MIN_BILDGROESSE = 15 * 1024  # kleinere Inline-Bilder sind meist Logos/Signaturen
ENDUNG_FUER_TYP = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg", "image/tiff": ".tif",
                   "application/xml": ".xml", "text/xml": ".xml"}
SPERR_SCHLUESSEL = 0x444D534D41494C  # pg_advisory_lock: verhindert parallele Abrufläufe


class MailabrufFehler(Exception):
    """Konfigurations- oder Verbindungsfehler (Lauf wird abgebrochen)."""


@dataclass
class Ergebnis:
    mails: int = 0
    uebernommen: int = 0
    dokumente: int = 0
    doppelt: int = 0
    ohne_anhang: int = 0
    ignoriert: int = 0
    fehler: int = 0
    stapel: list = field(default_factory=list)
    meldungen: list = field(default_factory=list)


def konfiguration(**abw):
    """Einstellungen aus settings (Umgebung) – einzelne Werte für Tests/Kommandozeile übersteuerbar."""
    k = {"host": settings.ERP_DMS_IMAP_HOST, "port": settings.ERP_DMS_IMAP_PORT, "user": settings.ERP_DMS_IMAP_USER,
         "passwort": settings.ERP_DMS_IMAP_PASSWORD, "ordner": settings.ERP_DMS_IMAP_ORDNER,
         "zielordner": settings.ERP_DMS_IMAP_ZIELORDNER, "fehlerordner": settings.ERP_DMS_IMAP_FEHLERORDNER,
         "absender": settings.ERP_DMS_IMAP_ABSENDER, "art": settings.ERP_DMS_IMAP_ART,
         "max_mails": settings.ERP_DMS_IMAP_MAX_MAILS, "timeout": settings.ERP_DMS_IMAP_TIMEOUT}
    k.update({schl: wert for schl, wert in abw.items() if wert is not None})
    fehlend = [n for n, schl in (("ERP_DMS_IMAP_HOST", "host"), ("ERP_DMS_IMAP_USER", "user"),
                                 ("ERP_DMS_IMAP_PASSWORD", "passwort")) if not k[schl]]
    if fehlend:
        raise MailabrufFehler("E-Mail-Abruf nicht konfiguriert – fehlt: " + ", ".join(fehlend))
    return k


def _quote(ordner):
    return '"' + ordner.replace("\\", "\\\\").replace('"', '\\"') + '"'


def verbinden(k):
    try:
        conn = imaplib.IMAP4_SSL(k["host"], int(k["port"]), ssl_context=ssl.create_default_context(),
                                 timeout=k["timeout"])
    except (OSError, imaplib.IMAP4.error) as exc:
        raise MailabrufFehler(f"Verbindung zu {k['host']}:{k['port']} fehlgeschlagen: {exc}") from exc
    try:
        conn.login(k["user"], k["passwort"])
    except imaplib.IMAP4.error as exc:
        _abmelden(conn)
        raise MailabrufFehler(f"Anmeldung am Postfach {k['user']} fehlgeschlagen: {exc}") from exc
    typ, daten = conn.select(_quote(k["ordner"]))
    if typ != "OK":
        _abmelden(conn)
        raise MailabrufFehler(f"Ordner „{k['ordner']}“ nicht vorhanden: {daten}")
    return conn


def _abmelden(conn):
    try:
        conn.logout()
    except Exception:  # noqa: BLE001 – Abmelden ist nur Aufräumen
        pass


def _text(wert, laenge):
    return re.sub(r"\s+", " ", str(wert or "")).strip()[:laenge]


def message_id(msg, roh):
    mid = _text(msg.get("Message-ID"), 500)
    return mid or f"<sha256-{hashlib.sha256(roh).hexdigest()}@pyron-erp.local>"


def absender(msg):
    adressen = getaddresses([str(msg.get("From", ""))])
    return (adressen[0][1] if adressen else "").lower()


def absender_zulaessig(adresse, erlaubt):
    if not erlaubt:
        return True
    return any(adresse == e or (e.startswith("@") and adresse.endswith(e)) for e in erlaubt)


def anhaenge(msg):
    """→ (liste[(dateiname, bytes)], hinweise) – verwertbare Anhänge und Gründe für abgelehnte Teile."""
    gefunden, hinweise = [], []
    for teil in msg.walk():
        if teil.is_multipart() or teil.get_content_maintype() == "message":
            continue
        typ = teil.get_content_type()
        name = teil.get_filename()
        dispo = teil.get_content_disposition()
        if not name and dispo != "attachment" and typ not in ("application/pdf", "application/xml"):
            continue  # Mailtext (text/plain, text/html)
        try:
            daten = teil.get_payload(decode=True) or b""
        except Exception:  # noqa: BLE001 – kaputte Kodierung nur melden
            hinweise.append(f"{name or typ}: Anhang nicht lesbar")
            continue
        name = Path(_text(name, 200).replace("/", "_").replace("\\", "_")).name if name else ""
        endung = Path(name).suffix.lower()
        if not endung and typ in ENDUNG_FUER_TYP:
            endung = ENDUNG_FUER_TYP[typ]
            name = (name or "anhang") + endung
        if endung not in S.ERLAUBTE_ENDUNGEN:
            hinweise.append(f"{name or typ}: Dateityp nicht unterstützt")
            continue
        if typ.startswith("image/") and dispo != "attachment" and len(daten) < MIN_BILDGROESSE:
            continue  # eingebettetes Logo
        if typ == "application/octet-stream" and endung == ".pdf" and not daten.lstrip()[:5].startswith(b"%PDF"):
            hinweise.append(f"{name}: keine gültige PDF-Datei")
            continue
        gefunden.append((name, daten))
    return gefunden, hinweise


def _partner(adresse):
    """Lieferant über (Rechnungs-)E-Mail-Adresse – nur bei eindeutigem Treffer."""
    from django.db.models import Q

    from apps.stammdaten.models import Geschaeftspartner

    if not adresse:
        return None
    treffer = list(Geschaeftspartner.objects.filter(Q(email__iexact=adresse) | Q(email_rechnung__iexact=adresse),
                                                    ist_lieferant=True)[:2])
    return treffer[0] if len(treffer) == 1 else None


def mail_uebernehmen(roh, k, postfach=""):
    """Eine Rohmail verarbeiten → (MailEingang, neu_angelegt: bool). Wirft nur bei technischen Fehlern."""
    msg = email.message_from_bytes(roh, policy=policy.default)
    mid = message_id(msg, roh)
    vorhanden = MailEingang.objects.filter(message_id=mid).first()
    if vorhanden and vorhanden.status != MailEingang.Status.FEHLER:
        return vorhanden, False
    von, betreff = absender(msg), _text(msg.get("Subject"), 500)
    try:
        gesendet = parsedate_to_datetime(str(msg.get("Date"))) if msg.get("Date") else None
    except (TypeError, ValueError):
        gesendet = None
    eintrag = vorhanden or MailEingang(message_id=mid, versuche=0)
    eintrag.absender, eintrag.betreff, eintrag.gesendet_am, eintrag.postfach = von[:320], betreff, gesendet, postfach
    eintrag.versuche += 1
    eintrag.fehler = ""
    if not absender_zulaessig(von, k["absender"]):
        eintrag.status = MailEingang.Status.IGNORIERT
        eintrag.save()
        return eintrag, True
    dateien, hinweise = anhaenge(msg)
    neu, doppelt = [], []
    for name, daten in dateien:
        alt = S.dublette_datei(daten)
        (doppelt if alt else neu).append((name, daten, alt))
    hinweise += [f"{n}: identisch mit {alt.nummer} – nicht erneut angelegt" for n, _, alt in doppelt]
    with transaction.atomic():
        if neu:
            stapel = S.stapel_anlegen(quelle=Stapel.Quelle.EMAIL, art=k["art"],
                                      bezeichnung=f"E-Mail {von}: {betreff}"[:200])
            partner = _partner(von)
            quelle = f"E-Mail von {von or 'unbekannt'}, Betreff „{betreff}“, Message-ID {mid}"
            for name, daten, _ in neu:
                try:
                    d = S.dokument_anlegen(daten, name, art=k["art"], stapel=stapel, partner=partner,
                                           quelle_text=quelle)
                except S.DmsFehler as exc:
                    hinweise.append(str(exc))
                    continue
                Dokument.objects.filter(pk=d.pk).update(metadaten={"email": {
                    "absender": von, "betreff": betreff, "message_id": mid,
                    "gesendet_am": gesendet.isoformat() if gesendet else None}})
                eintrag.anhaenge += 1
            if eintrag.anhaenge:
                eintrag.stapel = stapel
            else:
                stapel.delete()
        eintrag.hinweise = "\n".join(hinweise)[:5000]
        eintrag.status = MailEingang.Status.UEBERNOMMEN if eintrag.anhaenge or doppelt \
            else MailEingang.Status.OHNE_ANHANG
        eintrag.save()
    return eintrag, True


def _kennzeichnen(conn, uid, ziel):
    """Verarbeitete Mail verschieben (MOVE, sonst COPY + \\Deleted) oder als gelesen markieren."""
    if ziel:
        if b"MOVE" in {c if isinstance(c, bytes) else c.encode() for c in getattr(conn, "capabilities", ())}:
            typ, _ = conn.uid("MOVE", uid, _quote(ziel))
            if typ == "OK":
                return "verschoben"
        typ, _ = conn.uid("COPY", uid, _quote(ziel))
        if typ == "OK":
            conn.uid("STORE", uid, "+FLAGS", r"(\Seen \Deleted)")
            return "verschoben"
        log.warning("Zielordner %s nicht erreichbar – Mail nur als gelesen markiert.", ziel)
    conn.uid("STORE", uid, "+FLAGS", r"(\Seen)")
    return "gelesen"


def _sperren():
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", [SPERR_SCHLUESSEL])
        return cur.fetchone()[0]


def _entsperren():
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", [SPERR_SCHLUESSEL])


def abrufen(k, trocken=False, alle=False, verarbeiten=True, ausgabe=None):
    """Postfach abrufen → Ergebnis. trocken=True listet nur auf (keine Anlage, keine Kennzeichnung)."""
    schreiben = ausgabe or (lambda text: None)
    erg = Ergebnis()
    if not trocken and not _sperren():
        erg.meldungen.append("Ein anderer Mailabruf läuft bereits – übersprungen.")
        return erg
    conn = verbinden(k)
    try:
        typ, daten = conn.uid("SEARCH", None, "ALL" if alle else "UNSEEN")
        if typ != "OK":
            raise MailabrufFehler(f"Suche im Postfach fehlgeschlagen: {daten}")
        uids = (daten[0] or b"").split()[: k["max_mails"]]
        geloescht = False
        for uid in uids:
            erg.mails += 1
            typ, teile = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            roh = next((t[1] for t in teile or [] if isinstance(t, tuple) and len(t) > 1), None)
            if typ != "OK" or not roh:
                erg.fehler += 1
                erg.meldungen.append(f"UID {uid.decode()}: Mail konnte nicht geladen werden.")
                continue
            if trocken:
                msg = email.message_from_bytes(roh, policy=policy.default)
                dateien, _ = anhaenge(msg)
                schreiben(f"  {absender(msg)} | {_text(msg.get('Subject'), 80)} | "
                          f"{len(dateien)} Anhang/Anhänge | {message_id(msg, roh)}")
                continue
            try:
                eintrag, neu = mail_uebernehmen(roh, k, k["ordner"])
            except Exception as exc:  # noqa: BLE001 – jede Mail einzeln, der Lauf geht weiter
                log.exception("Mailabruf: Fehler bei UID %s", uid)
                eintrag = _fehler_merken(roh, k, exc)
                erg.fehler += 1
                if eintrag.status == MailEingang.Status.FEHLER:
                    erg.meldungen.append(f"{eintrag.betreff or uid.decode()}: {exc} – wird erneut versucht.")
                    continue  # ungelesen lassen
                erg.meldungen.append(f"{eintrag.betreff or uid.decode()}: {exc} – aufgegeben.")
                ziel = k["fehlerordner"]
            else:
                if not neu:
                    erg.doppelt += 1
                    ziel = k["zielordner"]
                elif eintrag.status == MailEingang.Status.UEBERNOMMEN:
                    erg.uebernommen += 1
                    erg.dokumente += eintrag.anhaenge
                    if eintrag.stapel_id:
                        erg.stapel.append(eintrag.stapel)
                    ziel = k["zielordner"]
                elif eintrag.status == MailEingang.Status.IGNORIERT:
                    erg.ignoriert += 1
                    ziel = ""  # fremde Mails nur als gelesen markieren, nicht verschieben
                else:
                    erg.ohne_anhang += 1
                    ziel = k["fehlerordner"]
                schreiben(f"  {eintrag.get_status_display()}: {eintrag.absender} | {eintrag.betreff[:80]}"
                          f"{f' → {eintrag.stapel.nummer} ({eintrag.anhaenge} Dok.)' if eintrag.stapel_id else ''}")
            try:
                if _kennzeichnen(conn, uid, ziel) == "verschoben":
                    geloescht = True
            except (imaplib.IMAP4.error, OSError) as exc:
                erg.meldungen.append(f"UID {uid.decode()}: Kennzeichnen fehlgeschlagen ({exc}) – "
                                     "Dublettenschutz per Message-ID greift beim nächsten Lauf.")
        if geloescht:
            conn.expunge()
    finally:
        _abmelden(conn)
        if not trocken:
            _entsperren()
    if verarbeiten and not trocken:
        from . import verarbeitung as V

        for stapel in erg.stapel:
            V.warteschlange_abarbeiten(stapel=stapel)
    return erg


def _fehler_merken(roh, k, exc):
    try:
        msg = email.message_from_bytes(roh, policy=policy.default)
        mid, von, betreff = message_id(msg, roh), absender(msg), _text(msg.get("Subject"), 500)
    except Exception:  # noqa: BLE001
        mid, von, betreff = f"<sha256-{hashlib.sha256(roh).hexdigest()}@pyron-erp.local>", "", ""
    eintrag, _ = MailEingang.objects.get_or_create(
        message_id=mid, defaults={"absender": von[:320], "betreff": betreff, "postfach": k["ordner"],
                                  "status": MailEingang.Status.FEHLER, "versuche": 0})
    eintrag.versuche += 1
    eintrag.fehler = f"{timezone.localtime():%d.%m.%Y %H:%M}: {exc}"[:5000]
    eintrag.status = MailEingang.Status.ABGEBROCHEN if eintrag.versuche >= MAX_VERSUCHE \
        else MailEingang.Status.FEHLER
    eintrag.save()
    return eintrag

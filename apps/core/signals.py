"""Audit-Log: protokolliert Anlage, Änderung (mit Vorher/Nachher) und Löschung aller ERP-Objekte."""
from datetime import date, datetime
from decimal import Decimal

from django.conf import settings
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save
from django.dispatch import receiver

from .middleware import aktueller_benutzer

ERP_LABELS = {app.split(".")[-1] for app in settings.ERP_APPS}
ZUSAETZLICH = {"auth.user"}
AUSGESCHLOSSEN = {"core.auditlog"}
IGNORIERTE_FELDER = {"last_login"}
MASKIERTE_FELDER = {"password"}


def _verfolgt(sender):
    label = sender._meta.label_lower
    if label in AUSGESCHLOSSEN:
        return False
    return sender._meta.app_label in ERP_LABELS or label in ZUSAETZLICH


def _json(wert):
    if wert is None or isinstance(wert, (bool, int, float, str, list, dict)):
        return wert
    if isinstance(wert, Decimal):
        return str(wert)
    if isinstance(wert, (date, datetime)):
        return wert.isoformat()
    return str(wert)


def _werte(instanz):
    daten = {}
    for feld in instanz._meta.concrete_fields:
        if feld.name in IGNORIERTE_FELDER:
            continue
        wert = feld.value_from_object(instanz)
        daten[feld.name] = "***" if feld.name in MASKIERTE_FELDER else _json(wert)
    return daten


def _schreiben(instanz, aktion, aenderungen):
    from .models import AuditLog

    user = aktueller_benutzer()
    angemeldet = user is not None and getattr(user, "is_authenticated", False)
    AuditLog.objects.create(
        benutzer=user if angemeldet else None,
        benutzername=user.get_username() if angemeldet else "System",
        aktion=aktion,
        app_label=instanz._meta.app_label,
        modell=instanz._meta.verbose_name,
        objekt_id=str(instanz.pk),
        objekt_text=str(instanz)[:255],
        aenderungen=aenderungen,
    )


@receiver(pre_save, dispatch_uid="audit_pre_save")
def _vorher(sender, instance, raw=False, **kwargs):
    if raw or not _verfolgt(sender):
        return
    instance._audit_alt = None
    if instance.pk:
        alt = sender._base_manager.filter(pk=instance.pk).first()
        if alt is not None:
            instance._audit_alt = _werte(alt)


@receiver(post_save, dispatch_uid="audit_post_save")
def _nachher(sender, instance, created, raw=False, **kwargs):
    if raw or not _verfolgt(sender):
        return
    neu = _werte(instance)
    alt = getattr(instance, "_audit_alt", None)
    if created or alt is None:
        _schreiben(instance, "anlegen", {k: [None, v] for k, v in neu.items() if v not in (None, "")})
    else:
        diff = {k: [alt.get(k), v] for k, v in neu.items() if alt.get(k) != v}
        if diff:
            _schreiben(instance, "aendern", diff)


@receiver(post_delete, dispatch_uid="audit_post_delete")
def _geloescht(sender, instance, **kwargs):
    if _verfolgt(sender):
        _schreiben(instance, "loeschen", {k: [v, None] for k, v in _werte(instance).items()})


@receiver(m2m_changed, dispatch_uid="audit_m2m")
def _zuordnung(sender, instance, action, pk_set, **kwargs):
    if action not in ("post_add", "post_remove", "post_clear") or not _verfolgt(type(instance)):
        return
    _schreiben(instance, "zuordnung", {"aktion": action.replace("post_", ""), "ids": sorted(pk_set or [])})

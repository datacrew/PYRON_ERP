"""Merkt sich den angemeldeten Benutzer des laufenden Requests (für das Audit-Log)."""
from contextvars import ContextVar

_aktueller_benutzer = ContextVar("erp_aktueller_benutzer", default=None)


def aktueller_benutzer():
    return _aktueller_benutzer.get()


def benutzer_setzen(user):
    return _aktueller_benutzer.set(user)


class AktuellerBenutzerMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _aktueller_benutzer.set(getattr(request, "user", None))
        try:
            return self.get_response(request)
        finally:
            _aktueller_benutzer.reset(token)

"""Zentrale Einstellungen des ERP-Systems.

Alle umgebungsabhängigen Werte kommen aus Umgebungsvariablen (siehe .env.example).
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env_liste(name, standard=""):
    return [x.strip() for x in os.environ.get(name, standard).split(",") if x.strip()]


def _env_bool(name, standard=False):
    wert = os.environ.get(name)
    if wert is None or wert.strip() == "":
        return standard
    return wert.strip().lower() in ("1", "true", "ja", "yes", "on")


# DEBUG ist nur für die Entwicklung voreingestellt; im Produktivbetrieb DJANGO_DEBUG=0 setzen.
DEBUG = _env_bool("DJANGO_DEBUG", True)
_ENTWICKLUNGS_SCHLUESSEL = "nur-fuer-entwicklung-bitte-aendern"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "" if not DEBUG else _ENTWICKLUNGS_SCHLUESSEL)
if not DEBUG and (not SECRET_KEY or SECRET_KEY == _ENTWICKLUNGS_SCHLUESSEL or len(SECRET_KEY) < 40):
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "Produktivbetrieb (DJANGO_DEBUG=0): DJANGO_SECRET_KEY muss gesetzt sein (zufällig, mindestens 40 Zeichen). "
        "Erzeugen z. B. mit: python3 -c \"import secrets; print(secrets.token_urlsafe(50))\""
    )

ALLOWED_HOSTS = _env_liste("DJANGO_ALLOWED_HOSTS", "*" if DEBUG else "")
if not DEBUG:
    if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured("Produktivbetrieb: DJANGO_ALLOWED_HOSTS muss die Domain(s) enthalten (kein „*“).")
    # interne Namen für Healthchecks (Container/Systemd) – ändern nichts an der öffentlichen Erreichbarkeit
    ALLOWED_HOSTS += [h for h in _env_liste("ERP_INTERNE_HOSTS", "localhost,127.0.0.1,app") if h not in ALLOWED_HOSTS]
CSRF_TRUSTED_ORIGINS = _env_liste(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    "http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000,http://127.0.0.1:3000,"
    "https://*.abacusai.app,https://*.preview.abacusai.app" if DEBUG else "",
)

# ---------------------------------------------------------------- Sicherheit (Produktivbetrieb)
# ERP_HTTPS=1: Anwendung läuft hinter einem Reverse Proxy mit HTTPS (Caddy). Standard: an, wenn DEBUG aus.
ERP_HTTPS = _env_bool("ERP_HTTPS", not DEBUG)
if ERP_HTTPS:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = _env_bool("DJANGO_SSL_REDIRECT", True)
    # Healthcheck wird intern per HTTP aufgerufen
    SECURE_REDIRECT_EXEMPT = [r"^gesundheit/$"]
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SEKUNDEN") or 0)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool("DJANGO_HSTS_SUBDOMAINS", False)
    SECURE_HSTS_PRELOAD = _env_bool("DJANGO_HSTS_PRELOAD", False)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = int(os.environ.get("ERP_SITZUNG_SEKUNDEN") or 10 * 3600)  # ein Arbeitstag
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# ERP-Fachmodule (Reihenfolge = Ladereihenfolge)
ERP_APPS = [
    "apps.core",
    "apps.stammdaten",
    "apps.belege",
    "apps.lager",
    "apps.einkauf",
    "apps.wareneingang",
    "apps.produktion",
    "apps.bde",
    "apps.akkord",
    "apps.vertrieb",
    "apps.versand",
    "apps.edi",
    "apps.shopapi",
    "apps.fibu",
    "apps.fibu_bank",
    "apps.fibu_mahnwesen",
    "apps.fibu_abschluss",
    "apps.reporting",
    "apps.dms",
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django.contrib.postgres",
    "rest_framework",
    "rest_framework.authtoken",
    "drf_spectacular",
    *ERP_APPS,
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.AktuellerBenutzerMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.erp_kontext",
            ],
            # ERP-Filter (euro, zahl, attr ...) in allen Vorlagen inkl. DB-Belegvorlagen verfügbar
            "builtins": ["apps.core.templatetags.erp"],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "erp"),
        "USER": os.environ.get("POSTGRES_USER", "erp"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "erp"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": int(os.environ.get("POSTGRES_CONN_MAX_AGE") or 60),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

# Lokalisierung Deutschland
# Gesetzlicher Mindestlohn (€/h) als Untergrenze der Akkord-/Zeitlohngarantie.
# Stand 01.01.2026: 13,90 € (ab 01.01.2027 voraussichtlich 14,60 €) – vor Produktivbetrieb prüfen.
from decimal import Decimal as _Decimal  # noqa: E402

ERP_MINDESTLOHN = _Decimal(os.environ.get("ERP_MINDESTLOHN", "13.90"))
# Lohnsatz für die Soll-Kalkulation, falls zur Lohngruppe kein Mitarbeiter mit Akkordrichtsatz existiert
ERP_STANDARD_LOHNSATZ = _Decimal(os.environ.get("ERP_STANDARD_LOHNSATZ", "18.00"))

LANGUAGE_CODE = "de"
LANGUAGES = [("de", "Deutsch")]
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True
DECIMAL_SEPARATOR = ","
THOUSAND_SEPARATOR = "."

STATIC_URL = "static/"
STATIC_ROOT = Path(os.environ.get("ERP_STATIC_ROOT", BASE_DIR / "staticfiles"))
# Produktiv: komprimierte, versionierte Dateien (WhiteNoise); Caddy liefert /static/ zusätzlich direkt aus.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage" if DEBUG
                    else "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
STATICFILES_DIRS = [BASE_DIR / "static"]
# Belegarchiv – wird NICHT öffentlich ausgeliefert, sondern nur über rechtegeprüfte Views
MEDIA_ROOT = Path(os.environ.get("ERP_MEDIA_ROOT", BASE_DIR / "media"))
MEDIA_URL = "/media/"

# Dokumentenmanagement (DMS): OCR-Sprache(n) für Tesseract und Zeitlimit je Seite (Sekunden)
ERP_DMS_OCR_SPRACHE = os.environ.get("ERP_DMS_OCR_SPRACHE", "deu")
ERP_DMS_OCR_TIMEOUT = int(os.environ.get("ERP_DMS_OCR_TIMEOUT", "180"))
# Eingangsordner (Hotfolder) für Scanner/E-Mail-Ablage – eingelesen von „manage.py dms_eingang“
ERP_DMS_EINGANG = Path(os.environ.get("ERP_DMS_EINGANG", BASE_DIR / "eingang"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "login"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "COERCE_DECIMAL_TO_STRING": True,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "ERP Mittelstand – REST-API",
    "DESCRIPTION": "Interne API (Token/Session, Rechte je Modul) und Onlineshop-API (/api/v1/shop/, "
                   "Header „Authorization: Shop <token>“).",
    "VERSION": "1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# EDI/EDIFACT: eigene GLN/ILN (Absender in UNB/NAD SU)
ERP_EIGENE_GLN = os.environ.get("ERP_EIGENE_GLN", "4012345000016")

_LOG_LEVEL = os.environ.get("ERP_LOG_LEVEL", "INFO")
_LOG_DATEI = os.environ.get("ERP_LOG_DATEI", "")  # z. B. /var/log/erp/erp.log (leer = nur stdout)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"standard": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "standard"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "apps": {"handlers": ["console"], "level": _LOG_LEVEL, "propagate": False},
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
if _LOG_DATEI:
    LOGGING["handlers"]["datei"] = {
        "class": "logging.handlers.RotatingFileHandler", "filename": _LOG_DATEI,
        "maxBytes": 10 * 1024 * 1024, "backupCount": 10, "encoding": "utf-8", "formatter": "standard",
    }
    for _logger in [LOGGING["root"], *LOGGING["loggers"].values()]:
        _logger["handlers"].append("datei")


# ---------------------------------------------------------------- Phase 4: Finanzbuchhaltung
# Verzugszinsen § 288 BGB: Basiszinssatz wird in der Fibu gepflegt (Tabelle Basiszinssätze);
# Aufschlag in Prozentpunkten: Unternehmer 9 (§ 288 Abs. 2), Verbraucher 5 (§ 288 Abs. 1).
ERP_VERZUGSZINS_AUFSCHLAG_B2B = 9
ERP_VERZUGSZINS_AUFSCHLAG_B2C = 5
# Rechnungsprüfung: zulässige Preisabweichung gegenüber Bestellung/Wareneingang in Prozent
ERP_RECHNUNGSPRUEFUNG_TOLERANZ_PROZENT = 1
# DATEV-Export (EXTF-Kopf): Berater- und Mandantennummer laut Steuerberater
ERP_DATEV_BERATERNUMMER = os.environ.get("ERP_DATEV_BERATERNUMMER", "1001")
ERP_DATEV_MANDANTENNUMMER = os.environ.get("ERP_DATEV_MANDANTENNUMMER", "1")

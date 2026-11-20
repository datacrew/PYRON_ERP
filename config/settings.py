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


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "nur-fuer-entwicklung-bitte-aendern")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = _env_liste("DJANGO_ALLOWED_HOSTS", "*")
CSRF_TRUSTED_ORIGINS = _env_liste(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    "http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000,http://127.0.0.1:3000,"
    "https://*.abacusai.app,https://*.preview.abacusai.app",
)

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
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
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
        "CONN_MAX_AGE": int(os.environ.get("POSTGRES_CONN_MAX_AGE", "60")),
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
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
# Belegarchiv – wird NICHT öffentlich ausgeliefert, sondern nur über rechtegeprüfte Views
MEDIA_ROOT = Path(os.environ.get("ERP_MEDIA_ROOT", BASE_DIR / "media"))
MEDIA_URL = "/media/"

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

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"apps": {"handlers": ["console"], "level": os.environ.get("ERP_LOG_LEVEL", "INFO")}},
}


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

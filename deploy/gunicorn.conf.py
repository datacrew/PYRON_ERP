"""Gunicorn-Konfiguration (Docker und nativer Betrieb). Werte per Umgebungsvariablen überschreibbar."""
import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")


def _zahl(name, standard):
    """Ganzzahl aus der Umgebung; leere Werte (z. B. „GUNICORN_WORKERS=“ in der .env) = Standard."""
    wert = os.environ.get(name, "").strip()
    return int(wert) if wert else standard


workers = _zahl("GUNICORN_WORKERS", min(2 * multiprocessing.cpu_count() + 1, 9))
threads = _zahl("GUNICORN_THREADS", 2)
# PDF-Erzeugung (WeasyPrint) und Berichte können einige Sekunden dauern
timeout = _zahl("GUNICORN_TIMEOUT", 120)
graceful_timeout = 30
keepalive = 5
# Speicherlecks begrenzen: Worker nach n Anfragen neu starten
max_requests = _zahl("GUNICORN_MAX_REQUESTS", 1000)
max_requests_jitter = 100
accesslog = os.environ.get("GUNICORN_ACCESSLOG", "-")
errorlog = os.environ.get("GUNICORN_ERRORLOG", "-")
loglevel = os.environ.get("GUNICORN_LOGLEVEL", "info")
# Caddy setzt X-Forwarded-*; nur lokalen/Container-Proxys vertrauen
forwarded_allow_ips = os.environ.get("GUNICORN_FORWARDED_ALLOW_IPS", "127.0.0.1,::1,172.16.0.0/12,10.0.0.0/8")
proc_name = "erp"

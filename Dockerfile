# ERP Mittelstand – Anwendungs-Image (Gunicorn). Produktivbetrieb: deploy/docker-compose.prod.yml
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    ERP_STATIC_ROOT=/app/staticfiles ERP_MEDIA_ROOT=/app/media
# Systembibliotheken für WeasyPrint (Pango/HarfBuzz) und Schriften für die PDF-Belege
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 \
        fonts-dejavu fonts-liberation tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
# Ohne Root-Rechte betreiben; Media (Belegarchiv) und Static als Volumes
RUN useradd --system --uid 1000 --home /app erp \
    && mkdir -p /app/media /app/staticfiles \
    && chmod +x docker/entrypoint.sh \
    && chown -R erp:erp /app/media /app/staticfiles
USER erp

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/gesundheit/', timeout=4).status == 200 else 1)"
ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "-c", "deploy/gunicorn.conf.py"]

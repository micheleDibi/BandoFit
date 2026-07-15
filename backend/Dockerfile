FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Librerie di sistema per WeasyPrint (HTML→PDF): Pango per il layout del testo,
# gdk-pixbuf per le immagini, cairo, shared-mime-info e i font DejaVu (senza
# font il PDF esce senza glifi). Prima di USER appuser (serve root per apt).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libcairo2 \
        libffi-dev \
        shared-mime-info \
        fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

# Prima i metadati del pacchetto (layer cache-friendly), poi il codice.
COPY pyproject.toml ./
COPY app ./app
RUN pip install .

# Utente non privilegiato
RUN useradd --create-home appuser
USER appuser

EXPOSE 8000

# Healthcheck senza curl (immagine slim)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

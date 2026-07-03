FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

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

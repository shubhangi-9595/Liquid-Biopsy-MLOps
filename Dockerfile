# Serving image: the API, the drift job, the trained model and inference dependencies.
# The model is trained in CI *before* `docker build`, then baked into the image,
# so every image tag maps to exactly one model version.
#
#   API (default):  uvicorn app.main:app
#   Drift job:      python -m app.drift
#   Migrations:     python -m app.db.migrate   (the API also runs them on startup)

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MODEL_DIR=/app/models \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ ./app/
COPY models/model.joblib models/metadata.json models/reference_stats.json ./models/

# Run as a non-root user. The numeric UID lets Kubernetes verify runAsNonRoot.
RUN useradd --create-home --uid 10001 appuser && chown -R 10001 /app
USER 10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]

# Container for the FastAPI upload service, deployed to Cloud Run.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create the non-root user first, then copy the code so it is
# readable by that user regardless of local file permissions.
RUN useradd --create-home appuser
COPY --chown=appuser:appuser healthy_hints/ healthy_hints/
COPY --chown=appuser:appuser api/ api/
RUN chmod -R a+rX /app

USER appuser

# Cloud Run sets $PORT; default to 8080 for local runs.
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT}"]
FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app
COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock \
    && python -m playwright install --with-deps chromium \
    && useradd --uid 1000 --create-home appuser \
    && mkdir /data && chown appuser:appuser /data \
    && chmod -R a+rX /ms-playwright
COPY fleet ./fleet
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
USER appuser
CMD ["uvicorn", "fleet.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]

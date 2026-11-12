# Production image: gunicorn + WhiteNoise, SQLite with the price list loaded at start-up.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_DEBUG=0 \
    DJANGO_CACHE_DIR=/app/.cache

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Static files for the admin / browsable API (a dummy key is enough to build them).
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput

RUN useradd --create-home appuser && mkdir -p /app/.cache && chown -R appuser /app
USER appuser

EXPOSE 8000
# migrate also loads the fuel price list on first start.
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --threads 4 --timeout 60 --access-logfile -"]

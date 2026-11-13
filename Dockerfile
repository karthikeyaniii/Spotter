# Production image: gunicorn (gthread) + WhiteNoise.
FROM python:3.12-slim

# One math-library thread per process: NumPy and SciPy each bundle OpenBLAS, which by
# default starts a thread per CPU in every gunicorn worker (measured: 23 threads per
# worker, CPU saturated at ~50 req/s). Parallelism comes from worker processes instead.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_DEBUG=0 \
    DJANGO_CACHE_DIR=/app/.cache \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Static files for the admin / browsable API (a throwaway key is enough to build them).
RUN DJANGO_SECRET_KEY=build-only-throwaway-key-not-used-at-runtime-0123456789 \
    python manage.py collectstatic --noinput

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/.cache && chown -R appuser /app \
    && sed -i 's/\r$//' /app/deploy/entrypoint.sh && chmod +x /app/deploy/entrypoint.sh
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health/', timeout=4).status == 200 else 1)"

ENTRYPOINT ["/app/deploy/entrypoint.sh"]
CMD ["gunicorn", "-c", "gunicorn.conf.py", "config.wsgi:application"]

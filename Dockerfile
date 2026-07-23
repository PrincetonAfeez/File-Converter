FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libreoffice file libmagic1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.lock requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.lock
COPY . /app

# Build static assets into the manifest so CompressedManifestStaticFilesStorage can
# resolve {% static %} references at runtime (DEBUG=False). Uses throwaway build-time
# settings so the production SECRET_KEY guard doesn't trip during the build.
RUN DJANGO_DEBUG=True DJANGO_SECRET_KEY=build-only-secret \
    python manage.py collectstatic --noinput

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/media /app/staticfiles /app/work \
    && chmod +x /app/entrypoint.sh \
    && chown -R appuser:appuser /app
USER appuser

ENTRYPOINT ["/app/entrypoint.sh"]

EXPOSE 8000
CMD ["gunicorn", "fileconverter.wsgi:application", "--bind", "0.0.0.0:8000"]

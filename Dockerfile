# Olimlar.uz — production image for a Google Cloud VPS (free tier) via Docker.
FROM python:3.11-slim

# Faster, cleaner Python in containers.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Tashkent

WORKDIR /app

# Build deps for psycopg2/pandas wheels, then drop the apt cache.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first so this layer is cached when only code changes.
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Application code.
COPY . .

# On-disk cache directory (FileSystemCache) — keeps RAM free.
RUN mkdir -p /app/flask_cache

EXPOSE 8000

# Bind to $PORT when the platform provides one (Cloud Run / App Engine set it,
# typically 8080); fall back to 8000 for the nginx-fronted VPS setup. Shell form
# so ${PORT} is expanded at runtime.
CMD gunicorn app:app \
    --bind 0.0.0.0:${PORT:-8000} \
    --workers 2 \
    --threads 2 \
    --timeout 120

# LIVE Genesis: local development image. Not hardened for public hosting.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    LIVE_DB=/data/live.db

WORKDIR /app
COPY requirements.txt requirements-anthropic.txt ./
RUN pip install -r requirements.txt -r requirements-anthropic.txt

COPY . .
RUN useradd --create-home --uid 1000 genesis \
    && mkdir -p /data /app/secrets \
    && chown -R genesis:genesis /data /app
USER genesis

# Default: run the offline test suite. Other commands: see compose.yaml.
CMD ["python", "-m", "pytest", "-q"]

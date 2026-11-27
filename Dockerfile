# Web app (API + page) in one container.
#   docker build -t tour-insertion .
#   docker run -p 8000:8000 -v tour-data:/data tour-insertion
# Optional: -e TOOL_PASSWORD=... (browser asks for it)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TOOL_ROOT=/app \
    TOOL_GEOCODE_CACHE=/data/geocode_cache.json \
    TOOL_CONSENT_LOG=/data/terms_accepted.jsonl \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[server]"

COPY web ./web
COPY examples ./examples
COPY profiles.json ./

RUN useradd --create-home app && mkdir /data && chown app /data
USER app
VOLUME /data
EXPOSE 8000
CMD ["tour-insert-server"]

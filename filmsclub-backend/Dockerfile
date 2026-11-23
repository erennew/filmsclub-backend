FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/app

WORKDIR /app

# Runtime tools: ffmpeg for remux/streaming, mediainfo for probing.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        mediainfo \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN groupadd --gid 10001 app && \
    useradd --uid 10001 --gid app --create-home --home-dir /app app

COPY --chown=app:app . .
RUN chmod +x start.sh && \
    mkdir -p /app/.cache/remux /app/data && \
    chown -R app:app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/health', timeout=4).read()"

CMD ["bash", "start.sh"]

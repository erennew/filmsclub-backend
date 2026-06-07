FROM python:3.10-slim

WORKDIR /app

# System packages
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        wget \
        xz-utils \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Install FFmpeg from Debian repos (reliable for Heroku)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Verify FFmpeg version
RUN ffmpeg -version && ffprobe -version

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir "redis>=5.0.0"

COPY . .

RUN chmod +x start.sh

CMD ["bash", "start.sh"]
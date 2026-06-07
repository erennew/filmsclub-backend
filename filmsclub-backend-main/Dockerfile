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

# Install FFmpeg 7.1 static build
RUN mkdir -p /opt/ffmpeg && \
    wget -O /tmp/ffmpeg.tar.xz \
    https://johnvansickle.com/ffmpeg/releases/old-releases/ffmpeg-7.1-amd64-static.tar.xz && \
    tar -xf /tmp/ffmpeg.tar.xz -C /opt/ffmpeg --strip-components=1 && \
    ln -s /opt/ffmpeg/ffmpeg /usr/local/bin/ffmpeg && \
    ln -s /opt/ffmpeg/ffprobe /usr/local/bin/ffprobe && \
    rm -f /tmp/ffmpeg.tar.xz

# Verify FFmpeg version
RUN ffmpeg -version && ffprobe -version

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir "redis>=5.0.0"

COPY . .

RUN chmod +x start.sh

CMD ["bash", "start.sh"]
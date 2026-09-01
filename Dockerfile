FROM python:3.12-slim

# libgl1/libglib2.0-0 are needed by OpenCV for some codecs; ffmpeg gives
# OpenCV its RTSP(S)/TLS backend support.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

WORKDIR /app

COPY . .
RUN uv sync --frozen --no-dev

# config.json is expected to be mounted as a volume so edits from the web UI
# persist across container restarts -- see docker-compose.yml.
ENV BOLLARD_CONFIG=/app/config.json
ENV PORT=8080

EXPOSE 8080

CMD ["uv", "run", "bollard-detect"]

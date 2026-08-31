FROM python:3.11-slim

# libgl1/libglib2.0-0 are needed by opencv-python-headless for some codecs;
# ffmpeg gives OpenCV's FFmpeg backend full RTSP(S)/TLS support.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY detector.py app.py bollard_state.py ./
COPY templates ./templates
COPY static ./static
COPY config.json ./config.default.json

# config.json is expected to be mounted as a volume so edits from the web UI
# persist across container restarts -- see docker-compose.yml.
ENV BOLLARD_CONFIG=/app/data/config.json
ENV PORT=8080

EXPOSE 8080

CMD ["python3", "app.py"]

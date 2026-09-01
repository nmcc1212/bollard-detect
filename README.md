# Bollard Detect

Bollard Detect is a small Python app that watches an RTSP camera stream, detects the LED state for driveway bollards, and exposes the result through a simple web UI and optional MQTT/Home Assistant discovery.

It is designed for situations where each bollard has a visible LED or light source that moves between a raised and lowered state, and the software needs to classify those states continuously from the live video.

## Features

- Reads a live RTSP stream with OpenCV
- Detects LED centroid location inside configured ROIs
- Classifies each bollard as `RAISED`, `LOWERED`, `TRANSITIONING`, or `UNKNOWN`
- Smooths noisy readings over a configurable time window
- Exposes a web dashboard for live preview and calibration
- Saves user configuration to `config.json`
- Publishes state updates via MQTT
- Sends Home Assistant MQTT discovery payloads for each bollard sensor

## Project structure

- `src/bollard_detect/app.py` — Flask web app and background detection loop
- `src/bollard_detect/detector.py` — camera access, detection, calibration, and smoothing logic
- `config.default.json` — bundled default configuration template
- `config.json` — runtime configuration generated on first run
- `Dockerfile` — container image for running the service

## Requirements

- Python 3.12+
- `uv` package manager
- RTSP-capable camera or video stream
- Optional: MQTT broker for Home Assistant integration
- Optional: ffmpeg for RTSP and video decode support

## Quick start

1. Install dependencies:

   ```bash
   uv sync
   ```

2. Start the app:

   ```bash
   uv run bollard-detect
   ```

3. Open the UI in a browser:

   ```text
   http://localhost:8080
   ```

On first launch, the app checks for `config.json`. If it does not exist, it seeds it from `config.default.json`.

## Configuration

The runtime config lives in `config.json` and is created automatically on first run. A typical file looks like this:

```json
{
  "rtsp_url": "rtsp://user:pass@camera.local/stream",
  "snapshot_interval_seconds": 2,
  "smoothing_window": 5,
  "smoothing_required_agreement": 4,
  "mqtt": {
    "enabled": true,
    "host": "homeassistant.local",
    "port": 1883,
    "username": "",
    "password": "",
    "base_topic": "bollards"
  },
  "bollards": [
    {
      "name": "bollard_left",
      "display_name": "Bollard Left",
      "roi": [900, 260, 85, 130],
      "led_raised_y_range": [15, 40],
      "led_lowered_y_range": [90, 120],
      "led_hue_range": [0, 15],
      "led_min_saturation": 120,
      "led_min_value_day": 180,
      "led_min_value_night": 220
    }
  ]
}
```

### Important fields

- `rtsp_url`: URL of the camera stream to monitor
- `bollards`: list of bollard definitions, each with its own ROI and LED thresholds
- `roi`: bounding box in the video frame as `[x, y, width, height]`
- `led_raised_y_range` / `led_lowered_y_range`: vertical ranges inside the ROI that correspond to the LED being raised or lowered
- `mqtt`: MQTT settings for publishing to Home Assistant or another broker

## Web UI

The app serves a local dashboard at `http://localhost:8080` with:

- camera preview / snapshot view
- current bollard state output
- calibration controls for each bollard
- configuration editing through the API-backed UI

The dashboard supports calibrating the LED positions for each bollard by selecting the target state and updating the `led_raised_y_range` or `led_lowered_y_range` values automatically.

## MQTT and Home Assistant

If `mqtt.enabled` is `true`, the app will:

- connect to the configured MQTT broker
- publish a state topic for each bollard under the configured base topic, such as:

  ```text
  bollards/bollard_left/state
  ```

- publish Home Assistant MQTT discovery payloads so each bollard appears as a sensor entity automatically

This makes the detector simple to combine with automations and dashboards in Home Assistant.

## Docker

A container image is included via the `Dockerfile`.

Run it with:

```bash
docker run --rm -p 8080:8080 \
  -v "$PWD/config.json:/app/config.json" \
  -e PORT=8080 \
  ghcr.io/nmcc1212/bollard-detect:latest
```

You can also mount a custom config file and point `BOLLARD_CONFIG` at it if needed.

## Troubleshooting

- If the camera does not open, verify the RTSP URL and that ffmpeg can decode it.
- If detection is unreliable, adjust the ROI and LED threshold ranges in `config.json`.
- If the app reports a stream error, check camera connectivity, credentials, and network access.
- If LED detection fails at night, inspect `led_min_value_night` and ensure the camera exposure is stable.

## Notes

This project is a practical detector for custom driveway bollard hardware and may require on-site calibration for your camera angle, LED intensity, and lighting conditions.

A valid configuration is essential: the ROI must line up with the LED in the frame, and the raised/lowered vertical ranges should match the actual LED motion.

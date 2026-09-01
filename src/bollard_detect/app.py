#!/usr/bin/env python3
"""
app.py -- web UI for calibration + always-on detection loop that publishes
states to MQTT (with Home Assistant discovery).

Run directly:      python3 app.py
Run in Docker:      see Dockerfile / docker-compose.yml

Web UI: http://<host>:8080/
"""

import json
import logging
import os
import threading
import time

import cv2
from flask import Flask, Response, jsonify, render_template, request

from . import detector

CONFIG_PATH = os.environ.get("BOLLARD_CONFIG", "config.json")


def seed_config_if_missing():
    """On first run against a fresh volume, copy the bundled default config."""
    if os.path.exists(CONFIG_PATH):
        return
    default_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "config.default.json"
    )
    if not os.path.exists(default_path):
        return
    os.makedirs(os.path.dirname(CONFIG_PATH) or ".", exist_ok=True)
    with open(default_path, "r") as src, open(CONFIG_PATH, "w") as dst:
        dst.write(src.read())
    print(f"Seeded {CONFIG_PATH} from bundled default config.")


app = Flask(__name__)
logger = logging.getLogger(__name__)

# Shared state between the background loop and the web routes.
STATE_LOCK = threading.Lock()
LATEST_STATES = {}  # smoothed states, e.g. {"bollard_left": "RAISED"}
LATEST_RAW = {}  # raw per-frame results (debug)
LATEST_FRAME = None  # last successfully captured frame (numpy array)
STREAM_OK = False
STREAM_ERROR = None

_mqtt_client = None
_mqtt_discovery_sent = set()


# --------------------------------------------------------------------------
# MQTT / Home Assistant
# --------------------------------------------------------------------------


def get_mqtt_client(mqtt_cfg: dict):
    global _mqtt_client
    if not mqtt_cfg.get("enabled"):
        return None
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("paho-mqtt not installed; MQTT disabled.")
        return None

    if _mqtt_client is not None:
        return _mqtt_client

    client = mqtt.Client(client_id="bollard-detector")
    if mqtt_cfg.get("username"):
        client.username_pw_set(mqtt_cfg["username"], mqtt_cfg.get("password", ""))

    base_topic = mqtt_cfg.get("base_topic", "bollards")
    availability_topic = f"{base_topic}/status"
    client.will_set(availability_topic, "offline", retain=True)

    try:
        client.connect(mqtt_cfg["host"], mqtt_cfg.get("port", 1883), keepalive=30)
        client.loop_start()
        client.publish(availability_topic, "online", retain=True)
        _mqtt_client = client
    except (ConnectionError, OSError, TimeoutError, ValueError) as exc:
        logger.warning("MQTT connection failed: %s", exc)
        return None

    return _mqtt_client


def publish_ha_discovery(client, mqtt_cfg: dict, bollards):
    """Publish Home Assistant MQTT discovery config for each bollard, once."""
    base_topic = mqtt_cfg.get("base_topic", "bollards")
    availability_topic = f"{base_topic}/status"

    device = {
        "identifiers": ["driveway_bollard_detector"],
        "name": "Driveway Bollards",
        "manufacturer": "Custom",
        "model": "Camera LED Detector",
    }

    for cfg in bollards:
        if cfg.name in _mqtt_discovery_sent:
            continue
        state_topic = f"{base_topic}/{cfg.name}/state"
        discovery_topic = f"homeassistant/sensor/bollard_{cfg.name}/config"
        payload = {
            "name": cfg.display_name,
            "unique_id": f"bollard_{cfg.name}_state",
            "state_topic": state_topic,
            "availability_topic": availability_topic,
            "device": device,
            "icon": "mdi:traffic-cone",
        }
        client.publish(discovery_topic, json.dumps(payload), retain=True)
        _mqtt_discovery_sent.add(cfg.name)


def publish_states(client, mqtt_cfg: dict, stable_states: dict):
    if client is None:
        return
    base_topic = mqtt_cfg.get("base_topic", "bollards")
    for name, state in stable_states.items():
        client.publish(f"{base_topic}/{name}/state", state, retain=True)


# --------------------------------------------------------------------------
# Background detection loop
# --------------------------------------------------------------------------


def background_loop():
    global LATEST_STATES, LATEST_RAW, LATEST_FRAME, STREAM_OK, STREAM_ERROR

    cap = None
    smoother = None

    while True:
        try:
            raw_cfg, bollards = detector.load_config(CONFIG_PATH)
        except (OSError, TypeError, ValueError) as exc:
            with STATE_LOCK:
                STREAM_ERROR = f"Config error: {exc}"
                STREAM_OK = False
            time.sleep(3)
            continue

        interval = raw_cfg.get("snapshot_interval_seconds", 2)
        window = raw_cfg.get("smoothing_window", 5)
        agreement = raw_cfg.get("smoothing_required_agreement", 4)

        if smoother is None:
            smoother = detector.make_smoother(window, agreement)

        if cap is None:
            try:
                cap = detector.open_stream(raw_cfg["rtsp_url"])
                with STATE_LOCK:
                    STREAM_OK = True
                    STREAM_ERROR = None
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                with STATE_LOCK:
                    STREAM_OK = False
                    STREAM_ERROR = str(exc)
                time.sleep(3)
                continue

        try:
            frame = detector.grab_frame(cap)
            raw_states = detector.evaluate_frame(frame, bollards)
            stable = smoother(raw_states)

            with STATE_LOCK:
                LATEST_STATES = stable
                LATEST_RAW = raw_states
                LATEST_FRAME = frame
                STREAM_OK = True
                STREAM_ERROR = None

            mqtt_cfg = raw_cfg.get("mqtt", {})
            if mqtt_cfg.get("enabled"):
                client = get_mqtt_client(mqtt_cfg)
                if client is not None:
                    publish_ha_discovery(client, mqtt_cfg, bollards)
                    publish_states(client, mqtt_cfg, stable)

        except RuntimeError as exc:
            with STATE_LOCK:
                STREAM_OK = False
                STREAM_ERROR = str(exc)
            try:
                cap.release()
            except (AttributeError, OSError, RuntimeError):
                logger.exception("Error releasing video capture after stream failure")
            cap = None
            time.sleep(2)
            continue

        time.sleep(interval)


# --------------------------------------------------------------------------
# Web routes
# --------------------------------------------------------------------------


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config", methods=["GET"])
def api_get_config():
    raw_cfg, _ = detector.load_config(CONFIG_PATH)
    return jsonify(raw_cfg)


@app.route("/api/config", methods=["POST"])
def api_save_config():
    new_cfg = request.get_json(force=True)
    # Basic sanity checks before writing to disk.
    if "rtsp_url" not in new_cfg or "bollards" not in new_cfg:
        return jsonify({"error": "config must include rtsp_url and bollards"}), 400
    try:
        detector.save_config(CONFIG_PATH, new_cfg)
    except (OSError, TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@app.route("/api/state")
def api_state():
    with STATE_LOCK:
        return jsonify(
            {
                "states": LATEST_STATES,
                "raw": LATEST_RAW,
                "stream_ok": STREAM_OK,
                "stream_error": STREAM_ERROR,
            }
        )


@app.route("/api/snapshot.jpg")
def api_snapshot():
    """Serve the most recent captured frame, with bollard ROIs overlaid."""
    with STATE_LOCK:
        frame = None if LATEST_FRAME is None else LATEST_FRAME.copy()
        raw = dict(LATEST_RAW)

    if frame is None:
        return Response(status=503)

    try:
        _, bollards = detector.load_config(CONFIG_PATH)
        for cfg in bollards:
            x, y, w, h = cfg.roi
            state = raw.get(cfg.name, {}).get("state", "UNKNOWN")
            color = {
                "RAISED": (0, 200, 0),
                "LOWERED": (0, 140, 255),
                "TRANSITIONING": (0, 255, 255),
            }.get(state, (0, 0, 255))
            cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
            cv2.putText(
                frame,
                f"{cfg.name}: {state}",
                (x, max(y - 8, 15)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                1,
                cv2.LINE_AA,
            )
    except (TypeError, ValueError, cv2.error):
        logger.debug("Could not draw snapshot overlay", exc_info=True)

    ok, buf = cv2.imencode(".jpg", frame)
    if not ok:
        return Response(status=500)
    return Response(buf.tobytes(), mimetype="image/jpeg")


@app.route("/api/calibrate", methods=["POST"])
def api_calibrate():
    """
    Given a bollard name and target state ('raised' or 'lowered'), read the
    LED's current y-position in the live frame and write a calibrated range
    (with padding) into config.json for that bollard.
    """
    body = request.get_json(force=True)
    name = body.get("name")
    which = body.get("which")  # "raised" or "lowered"
    padding = int(body.get("padding", 15))

    if which not in ("raised", "lowered"):
        return jsonify({"error": "which must be 'raised' or 'lowered'"}), 400

    with STATE_LOCK:
        frame = None if LATEST_FRAME is None else LATEST_FRAME.copy()

    if frame is None:
        return jsonify({"error": "no frame available yet"}), 503

    raw_cfg, bollards = detector.load_config(CONFIG_PATH)
    cfg = next((b for b in bollards if b.name == name), None)
    if cfg is None:
        return jsonify({"error": f"unknown bollard '{name}'"}), 404

    night = detector.is_night_frame(frame)
    x, y, w, h = cfg.roi
    roi = frame[y : y + h, x : x + w]
    led_y = detector.find_led_centroid_y(roi, cfg, night)
    if led_y is None:
        return jsonify(
            {"error": "could not detect LED in current frame -- check ROI/thresholds"}
        ), 422

    new_range = [max(0, int(led_y - padding)), int(led_y + padding)]

    for b in raw_cfg["bollards"]:
        if b["name"] == name:
            if which == "raised":
                b["led_raised_y_range"] = new_range
            else:
                b["led_lowered_y_range"] = new_range

    detector.save_config(CONFIG_PATH, raw_cfg)
    return jsonify(
        {"ok": True, "led_y": led_y, "new_range": new_range, "night_mode": night}
    )


def main():
    seed_config_if_missing()
    thread = threading.Thread(target=background_loop, daemon=True)
    thread.start()
    port_value = os.environ.get("PORT")
    app.run(host="0.0.0.0", port=int(port_value) if port_value else 8080)


if __name__ == "__main__":
    main()

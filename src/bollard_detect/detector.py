"""
detector.py

Core bollard-state detection logic, shared by bollard_state.py (CLI),
app.py (web UI + MQTT loop). Keeping this separate means the web UI's
"live preview" always matches exactly what the background loop will do.
"""

import collections
import json
import threading
from dataclasses import dataclass

import cv2
import numpy as np

CONFIG_LOCK = threading.Lock()


@dataclass
class BollardConfig:
    name: str
    display_name: str
    roi: tuple  # (x, y, w, h) in full-frame coordinates
    led_raised_y_range: tuple  # (y0, y1) within the ROI
    led_lowered_y_range: tuple  # (y0, y1) within the ROI
    led_hue_range: tuple  # (h0, h1) OpenCV hue units 0-179, daytime only
    led_min_saturation: int
    led_min_value_day: int
    led_min_value_night: int


def default_bollard(name: str) -> dict:
    return {
        "name": name,
        "display_name": name.replace("_", " ").title(),
        "roi": [100, 100, 60, 200],
        "led_raised_y_range": [0, 40],
        "led_lowered_y_range": [160, 200],
        "led_hue_range": [0, 15],
        "led_min_saturation": 120,
        "led_min_value_day": 180,
        "led_min_value_night": 220,
    }


def load_config(path: str):
    with CONFIG_LOCK, open(path, "r") as f:
        raw = json.load(f)

    raw.setdefault(
        "mqtt",
        {
            "enabled": False,
            "host": "homeassistant.local",
            "port": 1883,
            "username": "",
            "password": "",
            "base_topic": "bollards",
        },
    )
    raw.setdefault("snapshot_interval_seconds", 2)
    raw.setdefault("smoothing_window", 5)
    raw.setdefault("smoothing_required_agreement", 4)

    bollards = []
    for b in raw["bollards"]:
        bollards.append(
            BollardConfig(
                name=b["name"],
                display_name=b.get("display_name", b["name"]),
                roi=tuple(b["roi"]),
                led_raised_y_range=tuple(b["led_raised_y_range"]),
                led_lowered_y_range=tuple(b["led_lowered_y_range"]),
                led_hue_range=tuple(b["led_hue_range"]),
                led_min_saturation=b["led_min_saturation"],
                led_min_value_day=b["led_min_value_day"],
                led_min_value_night=b["led_min_value_night"],
            )
        )

    return raw, bollards


def save_config(path: str, raw: dict):
    with CONFIG_LOCK, open(path, "w") as f:
        json.dump(raw, f, indent=2)


def open_stream(rtsp_url: str) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        raise RuntimeError(
            f'Could not open stream. Test with `ffprobe "{rtsp_url}"` '
            "first to confirm the URL/credentials are correct and that "
            "ffmpeg can decode it before troubleshooting further."
        )
    return cap


def grab_frame(cap: cv2.VideoCapture) -> np.ndarray:
    for _ in range(2):
        cap.grab()
    ok, frame = cap.read()
    if not ok or frame is None:
        raise RuntimeError("Failed to read a frame from the stream.")
    return frame


def is_night_frame(frame_bgr: np.ndarray) -> bool:
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mean_saturation = float(np.mean(hsv[:, :, 1]))
    return mean_saturation < 25.0


def find_led_centroid_y(
    roi_bgr: np.ndarray, cfg: BollardConfig, night: bool
) -> float | None:
    if roi_bgr.size == 0:
        return None
    hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)

    if night:
        mask = cv2.inRange(v, cfg.led_min_value_night, 255)
    else:
        hue_mask = cv2.inRange(h, cfg.led_hue_range[0], cfg.led_hue_range[1])
        sat_mask = cv2.inRange(s, cfg.led_min_saturation, 255)
        val_mask = cv2.inRange(v, cfg.led_min_value_day, 255)
        mask = cv2.bitwise_and(hue_mask, cv2.bitwise_and(sat_mask, val_mask))

    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < 4:
        return None

    M = cv2.moments(largest)
    if M["m00"] == 0:
        return None
    return M["m01"] / M["m00"]


def classify_state(led_y: float | None, cfg: BollardConfig) -> str:
    if led_y is None:
        return "UNKNOWN"
    r0, r1 = cfg.led_raised_y_range
    l0, l1 = cfg.led_lowered_y_range
    if r0 <= led_y <= r1:
        return "RAISED"
    if l0 <= led_y <= l1:
        return "LOWERED"
    return "TRANSITIONING"


def evaluate_frame(frame_bgr: np.ndarray, bollards: list) -> dict:
    night = is_night_frame(frame_bgr)
    results = {}
    for cfg in bollards:
        x, y, w, h = cfg.roi
        roi = frame_bgr[y : y + h, x : x + w]
        led_y = find_led_centroid_y(roi, cfg, night)
        state = classify_state(led_y, cfg)
        results[cfg.name] = {
            "state": state,
            "led_y_in_roi": led_y,
            "night_mode": night,
        }
    return results


def make_smoother(window: int, required_agreement: int):
    """Returns a stateful function that smooths states across calls."""
    trackers = {}

    def smooth(states: dict) -> dict:
        stable = {}
        for name, result in states.items():
            tracker = trackers.setdefault(name, collections.deque(maxlen=window))
            tracker.append(result["state"])
            counts = collections.Counter(tracker)
            top_state, top_count = counts.most_common(1)[0]
            stable[name] = top_state if top_count >= required_agreement else "UNCERTAIN"
        return stable

    return smooth

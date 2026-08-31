#!/usr/bin/env python3
"""
bollard_state.py

Detects whether motorised driveway bollards are RAISED or LOWERED using a
static RTSP(S) security camera feed, by tracking the y-position of each
bollard's LED indicator light. Works in daylight (color threshold) and at
night under IR illumination (brightness threshold), since it doesn't rely on
color information staying consistent between the two.

--------------------------------------------------------------------------
HOW TO CALIBRATE
--------------------------------------------------------------------------
1. Run:  python3 bollard_state.py --snapshot snapshot.jpg
   This grabs a single frame from the camera and saves it to disk.
2. Open snapshot.jpg in any image viewer that shows pixel coordinates
   (e.g. GIMP, Preview with a ruler, or even just cropping in an editor).
3. For each bollard, note a rectangle [x, y, width, height] in config.json
   that tightly covers the bollard's *entire travel range* -- from where
   the LED sits when fully lowered to where it sits when fully raised.
4. Raise one bollard fully, take another snapshot, and note the LED's
   y-offset *within that ROI* -> that's your led_raised_y_range.
   Lower it fully, take another snapshot, note the y-offset -> that's
   your led_lowered_y_range. Leave a little padding on each range.
5. Repeat for each bollard. Do this once in daylight and once at night;
   if the LED position differs noticeably you may want separate ranges,
   but usually the geometry is identical -- only the detection method
   (color vs brightness) changes.

--------------------------------------------------------------------------
RUNNING
--------------------------------------------------------------------------
python3 bollard_state.py --config config.json --loop
python3 bollard_state.py --config config.json --once
python3 bollard_state.py --config config.json --snapshot out.jpg

--------------------------------------------------------------------------
NOTES ON RTSPS INGESTION
--------------------------------------------------------------------------
OpenCV's FFmpeg backend can usually handle rtsps:// directly if the
underlying ffmpeg build has TLS support (most do). If cv2.VideoCapture
fails to open the stream:
  1. Test the URL directly first:  ffprobe "rtsps://user:pass@ip:port/path"
  2. If ffprobe works but OpenCV doesn't, try setting the backend
     explicitly: cv2.VideoCapture(url, cv2.CAP_FFMPEG)
  3. If it's a UniFi Protect camera, consider running go2rtc locally to
     relay the RTSPS stream as a plain RTSP or HTTP-MJPEG source, then
     point this script at the relay instead -- much more forgiving.
"""

import argparse
import collections
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np


@dataclass
class BollardConfig:
    name: str
    roi: tuple            # (x, y, w, h) in full-frame coordinates
    led_raised_y_range: tuple    # (y0, y1) within the ROI
    led_lowered_y_range: tuple   # (y0, y1) within the ROI
    led_hue_range: tuple         # (h0, h1) OpenCV hue units 0-179, daytime only
    led_min_saturation: int
    led_min_value_day: int
    led_min_value_night: int


@dataclass
class BollardState:
    config: BollardConfig
    history: collections.deque = field(default_factory=lambda: collections.deque(maxlen=10))
    stable_state: str = "UNKNOWN"


def load_config(path: str):
    with open(path, "r") as f:
        raw = json.load(f)

    bollards = []
    for b in raw["bollards"]:
        bollards.append(BollardConfig(
            name=b["name"],
            roi=tuple(b["roi"]),
            led_raised_y_range=tuple(b["led_raised_y_range"]),
            led_lowered_y_range=tuple(b["led_lowered_y_range"]),
            led_hue_range=tuple(b["led_hue_range"]),
            led_min_saturation=b["led_min_saturation"],
            led_min_value_day=b["led_min_value_day"],
            led_min_value_night=b["led_min_value_night"],
        ))

    return raw, bollards


def open_stream(rtsp_url: str) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        raise RuntimeError(
            f"Could not open stream. Test with `ffprobe \"{rtsp_url}\"` "
            "first to confirm the URL/credentials are correct and that "
            "ffmpeg can decode it before troubleshooting further."
        )
    return cap


def grab_frame(cap: cv2.VideoCapture) -> np.ndarray:
    # Flush a couple of stale buffered frames so we get something current.
    for _ in range(2):
        cap.grab()
    ok, frame = cap.read()
    if not ok or frame is None:
        raise RuntimeError("Failed to read a frame from the stream.")
    return frame


def is_night_frame(frame_bgr: np.ndarray) -> bool:
    """IR/night frames are effectively monochrome -- very low saturation."""
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mean_saturation = float(np.mean(hsv[:, :, 1]))
    return mean_saturation < 25.0  # tune if your camera's IR cut differs


def find_led_centroid_y(roi_bgr: np.ndarray, cfg: BollardConfig, night: bool) -> Optional[float]:
    """Return the y-coordinate (within the ROI) of the LED blob, or None."""
    hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)

    if night:
        # No reliable color at night -- just take the brightest blob.
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

    # Pick the largest bright blob (avoids stray reflections/noise pixels).
    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < 4:
        return None

    M = cv2.moments(largest)
    if M["m00"] == 0:
        return None
    return M["m01"] / M["m00"]


def classify_state(led_y: Optional[float], cfg: BollardConfig) -> str:
    if led_y is None:
        return "UNKNOWN"
    r0, r1 = cfg.led_raised_y_range
    l0, l1 = cfg.led_lowered_y_range
    if r0 <= led_y <= r1:
        return "RAISED"
    if l0 <= led_y <= l1:
        return "LOWERED"
    return "TRANSITIONING"  # in between -- bollard is moving, or needs recalibration


def evaluate_frame(frame_bgr: np.ndarray, bollards: list[BollardConfig]) -> dict:
    night = is_night_frame(frame_bgr)
    results = {}
    for cfg in bollards:
        x, y, w, h = cfg.roi
        roi = frame_bgr[y:y + h, x:x + w]
        led_y = find_led_centroid_y(roi, cfg, night)
        state = classify_state(led_y, cfg)
        results[cfg.name] = {
            "state": state,
            "led_y_in_roi": led_y,
            "night_mode": night,
        }
    return results


def smooth_states(states: dict, trackers: dict, required_agreement: int, window: int) -> dict:
    stable = {}
    for name, result in states.items():
        tracker = trackers.setdefault(name, collections.deque(maxlen=window))
        tracker.append(result["state"])
        counts = collections.Counter(tracker)
        top_state, top_count = counts.most_common(1)[0]
        if top_count >= required_agreement:
            stable[name] = top_state
        else:
            stable[name] = "UNCERTAIN"
    return stable


def publish_states(stable_states: dict):
    """
    Hook for downstream integration. Uncomment/adapt to publish to MQTT
    (e.g. for Home Assistant) instead of just printing.
    """
    # import paho.mqtt.publish as publish
    # for name, state in stable_states.items():
    #     publish.single(f"home/driveway/{name}/state", state,
    #                     hostname="homeassistant.local")
    for name, state in stable_states.items():
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {name:16s} -> {state}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--snapshot", help="Save a single frame to this path and exit (for calibration)")
    parser.add_argument("--once", action="store_true", help="Evaluate a single frame and print raw results")
    parser.add_argument("--loop", action="store_true", help="Continuously poll and print smoothed states")
    args = parser.parse_args()

    raw_cfg, bollards = load_config(args.config)
    cap = open_stream(raw_cfg["rtsp_url"])

    try:
        if args.snapshot:
            frame = grab_frame(cap)
            for cfg in bollards:
                x, y, w, h = cfg.roi
                # ---------------------------------------------------------
                # Draw the main ROI.
                # ---------------------------------------------------------
                cv2.rectangle(
                    frame,
                    (x, y),
                    (x + w, y + h),
                    (0, 255, 0),
                    2
                )
                # Bollard name.
                cv2.putText(
                    frame,
                    cfg.name,
                    (x, max(20, y - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 0),
                    2,
                    cv2.LINE_AA
                )
                # ---------------------------------------------------------
                # Draw LED raised/lowered Y ranges.
                #
                # These Y values are relative to the ROI, so convert them
                # to full-frame coordinates by adding the ROI's Y position.
                # ---------------------------------------------------------
                raised_y0, raised_y1 = cfg.led_raised_y_range
                lowered_y0, lowered_y1 = cfg.led_lowered_y_range
                def draw_led_range(y0, y1, label, thickness=2):
                    full_y0 = y + y0
                    full_y1 = y + y1
                    # Only draw the portion that falls inside the image.
                    frame_h, frame_w = frame.shape[:2]
                    clipped_y0 = max(0, min(frame_h - 1, full_y0))
                    clipped_y1 = max(0, min(frame_h - 1, full_y1))
                    # Draw horizontal boundaries.
                    cv2.line(
                        frame,
                        (x, clipped_y0),
                        (x + w, clipped_y0),
                        (255, 255, 0),
                        thickness
                    )
                    cv2.line(
                        frame,
                        (x, clipped_y1),
                        (x + w, clipped_y1),
                        (255, 255, 0),
                        thickness
                    )
                    # Draw a translucent-ish band using an overlay.
                    overlay = frame.copy()
                    cv2.rectangle(
                        overlay,
                        (x, clipped_y0),
                        (x + w, clipped_y1),
                        (255, 255, 0),
                        -1
                    )
                    cv2.addWeighted(overlay, 0.12, frame, 0.88, 0, frame)
                    # Label.
                    text_y = max(20, clipped_y0 - 5)
                    cv2.putText(
                        frame,
                        f"{label}: ROI y={y0}..{y1}",
                        (x + w + 8, text_y),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (255, 255, 0),
                        1,
                        cv2.LINE_AA
                    )
                draw_led_range(
                    raised_y0,
                    raised_y1,
                    "RAISED"
                )
                draw_led_range(
                    lowered_y0,
                    lowered_y1,
                    "LOWERED"
                )
                # ---------------------------------------------------------
                # Draw the centre of each configured LED range.
                # ---------------------------------------------------------
                raised_centre = y + (raised_y0 + raised_y1) / 2
                lowered_centre = y + (lowered_y0 + lowered_y1) / 2
                cv2.circle(
                    frame,
                    (x + w // 2, int(raised_centre)),
                    5,
                    (0, 255, 255),
                    -1
                )
                cv2.circle(
                    frame,
                    (x + w // 2, int(lowered_centre)),
                    5,
                    (0, 255, 255),
                    -1
                )
                # ---------------------------------------------------------
                # Draw configuration text to the right of the ROI.
                # ---------------------------------------------------------
                text_x = x + w + 12
                text_y = y + 20
                line_height = 20
                config_lines = [
                    f"ROI: [{x}, {y}, {w}, {h}]",
                    f"Raised Y: {raised_y0}..{raised_y1}",
                    f"Lowered Y: {lowered_y0}..{lowered_y1}",
                    f"Hue: {cfg.led_hue_range[0]}..{cfg.led_hue_range[1]}",
                    f"Min Sat: {cfg.led_min_saturation}",
                    f"Day Min V: {cfg.led_min_value_day}",
                    f"Night Min V: {cfg.led_min_value_night}",
                ]
                for i, text in enumerate(config_lines):
                    cv2.putText(
                        frame,
                        text,
                        (text_x, text_y + i * line_height),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (255, 255, 255),
                        1,
                        cv2.LINE_AA
                    )
            cv2.imwrite(args.snapshot, frame)
            print(
                f"Saved snapshot to {args.snapshot} "
                f"({frame.shape[1]}x{frame.shape[0]}) with calibration overlays"
            )
            return
        if args.once:
            frame = grab_frame(cap)
            results = evaluate_frame(frame, bollards)
            print(json.dumps(results, indent=2, default=str))
            return

        if args.loop:
            interval = raw_cfg.get("snapshot_interval_seconds", 2)
            window = raw_cfg.get("smoothing_window", 5)
            agreement = raw_cfg.get("smoothing_required_agreement", 4)
            trackers = {}
            print("Starting bollard monitoring loop. Ctrl+C to stop.")
            while True:
                try:
                    frame = grab_frame(cap)
                    raw_states = evaluate_frame(frame, bollards)
                    stable = smooth_states(raw_states, trackers, agreement, window)
                    publish_states(stable)
                except RuntimeError as e:
                    print(f"Frame read error, reconnecting: {e}", file=sys.stderr)
                    cap.release()
                    time.sleep(2)
                    cap = open_stream(raw_cfg["rtsp_url"])
                time.sleep(interval)
            return

        parser.print_help()

    finally:
        cap.release()


if __name__ == "__main__":
    main()
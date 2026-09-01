#!/usr/bin/env python3
"""
bollard_state.py -- command-line tool for one-off snapshots / checks.
For the always-on service with web UI + MQTT, see app.py instead.
"""

import argparse
import json
import sys
import time

import cv2
from . import detector


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--snapshot", help="Save a single frame to this path and exit")
    parser.add_argument(
        "--once", action="store_true", help="Evaluate a single frame and print results"
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Continuously poll and print smoothed states",
    )
    args = parser.parse_args()

    raw_cfg, bollards = detector.load_config(args.config)
    cap = detector.open_stream(raw_cfg["rtsp_url"])

    try:
        if args.snapshot:
            frame = detector.grab_frame(cap)
            for cfg in bollards:
                x, y, w, h = cfg.roi
                # ---------------------------------------------------------
                # Draw the main ROI.
                # ---------------------------------------------------------
                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                # Bollard name.
                cv2.putText(
                    frame,
                    cfg.name,
                    (x, max(20, y - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 0),
                    2,
                    cv2.LINE_AA,
                )
                # ---------------------------------------------------------
                # Draw LED raised/lowered Y ranges.
                #
                # These Y values are relative to the ROI, so convert them
                # to full-frame coordinates by adding the ROI's Y position.
                # ---------------------------------------------------------
                raised_y0, raised_y1 = cfg.led_raised_y_range
                lowered_y0, lowered_y1 = cfg.led_lowered_y_range

                def draw_led_range(y0, y1, label, thickness=2, *, x=x, y=y, w=w):
                    full_y0 = y + y0
                    full_y1 = y + y1
                    # Only draw the portion that falls inside the image.
                    frame_h, _ = frame.shape[:2]
                    clipped_y0 = max(0, min(frame_h - 1, full_y0))
                    clipped_y1 = max(0, min(frame_h - 1, full_y1))
                    # Draw horizontal boundaries.
                    cv2.line(
                        frame,
                        (x, clipped_y0),
                        (x + w, clipped_y0),
                        (255, 255, 0),
                        thickness,
                    )
                    cv2.line(
                        frame,
                        (x, clipped_y1),
                        (x + w, clipped_y1),
                        (255, 255, 0),
                        thickness,
                    )
                    # Draw a translucent-ish band using an overlay.
                    overlay = frame.copy()
                    cv2.rectangle(
                        overlay, (x, clipped_y0), (x + w, clipped_y1), (255, 255, 0), -1
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
                        cv2.LINE_AA,
                    )

                draw_led_range(raised_y0, raised_y1, "RAISED")
                draw_led_range(lowered_y0, lowered_y1, "LOWERED")
                # ---------------------------------------------------------
                # Draw the centre of each configured LED range.
                # ---------------------------------------------------------
                raised_centre = y + (raised_y0 + raised_y1) / 2
                lowered_centre = y + (lowered_y0 + lowered_y1) / 2
                cv2.circle(
                    frame, (x + w // 2, int(raised_centre)), 5, (0, 255, 255), -1
                )
                cv2.circle(
                    frame, (x + w // 2, int(lowered_centre)), 5, (0, 255, 255), -1
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
                        cv2.LINE_AA,
                    )
            cv2.imwrite(args.snapshot, frame)
            print(
                f"Saved snapshot to {args.snapshot} "
                f"({frame.shape[1]}x{frame.shape[0]}) with calibration overlays"
            )
            return

        if args.once:
            frame = detector.grab_frame(cap)
            results = detector.evaluate_frame(frame, bollards)
            print(json.dumps(results, indent=2, default=str))
            return

        if args.loop:
            interval = raw_cfg.get("snapshot_interval_seconds", 2)
            smooth = detector.make_smoother(
                raw_cfg.get("smoothing_window", 5),
                raw_cfg.get("smoothing_required_agreement", 4),
            )
            print("Starting bollard monitoring loop. Ctrl+C to stop.")
            while True:
                try:
                    frame = detector.grab_frame(cap)
                    raw_states = detector.evaluate_frame(frame, bollards)
                    stable = smooth(raw_states)
                    ts = time.strftime("%Y-%m-%d %H:%M:%S")
                    for name, state in stable.items():
                        print(f"{ts}  {name:16s} -> {state}")
                except RuntimeError as e:
                    print(f"Frame read error, reconnecting: {e}", file=sys.stderr)
                    cap.release()
                    time.sleep(2)
                    cap = detector.open_stream(raw_cfg["rtsp_url"])
                time.sleep(interval)
            return

        parser.print_help()

    finally:
        cap.release()


if __name__ == "__main__":
    main()

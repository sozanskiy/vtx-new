from __future__ import annotations

import argparse
import json
import signal
import sys
import time

import numpy as np
import cv2
import zmq


def run(freq_hz: int, endpoint: str, topic: str, fps: int = 10) -> None:
    ctx = zmq.Context.instance()
    pub = ctx.socket(zmq.PUB)
    pub.bind(endpoint)
    topic_b = topic.encode()
    width, height = 320, 240
    period = 1.0 / max(1, fps)
    stop = False

    def _sigint(_sig, _frm):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sigint)
    signal.signal(signal.SIGTERM, _sigint)

    t0 = time.time()
    frame_idx = 0
    try:
        while not stop:
            img = np.zeros((height, width), dtype=np.uint8)
            x = int(((time.time() - t0) * 40) % width)
            img[:, max(0, x - 2) : min(width, x + 2)] = 200
            cv2.putText(img, f"Mock {frame_idx} @ {freq_hz/1e6:.1f} MHz", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, 255, 2)
            payload = img.tobytes()
            meta = {"width": width, "height": height, "format": "gray8", "ts": time.time(), "freq_hz": freq_hz}
            pub.send_multipart([topic_b, json.dumps(meta).encode("utf-8"), payload])
            frame_idx += 1
            time.sleep(period)
    finally:
        try:
            pub.close(0)
        except Exception:
            pass


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Mock demod publisher (ZMQ frames)")
    ap.add_argument("--freq", dest="freq_hz", type=int, required=True)
    ap.add_argument("--endpoint", default="tcp://127.0.0.1:5556")
    ap.add_argument("--topic", default="frames")
    ap.add_argument("--fps", type=int, default=10)
    args = ap.parse_args(argv)
    run(args.freq_hz, args.endpoint, args.topic, args.fps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


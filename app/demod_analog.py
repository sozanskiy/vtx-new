from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from typing import Optional

import numpy as np
import zmq

from .hw_capture import get_sampler


def build_frame_from_envelope(
    envelope: np.ndarray,
    sample_rate_hz: float,
    width: int,
    height: int,
) -> np.ndarray:
    # Downsample to target pixel count by simple striding/mean pooling
    target = width * height
    if envelope.size < target:
        # Pad by repeating to reach target length
        reps = int(np.ceil(target / max(1, envelope.size)))
        envelope = np.tile(envelope, reps)
    # Normalize contrast using percentiles to mitigate wild swings
    v = envelope[: target].astype(np.float32)
    p5 = float(np.percentile(v, 5.0))
    p95 = float(np.percentile(v, 95.0))
    denom = max(1e-6, (p95 - p5))
    v = np.clip((v - p5) / denom, 0.0, 1.0)
    img = (v * 255.0).astype(np.uint8).reshape((height, width))
    return img


def run(freq_hz: int, endpoint: str, topic: str, sample_rate_hz: float, width: int, height: int, fps: int) -> None:
    sampler = get_sampler()
    ctx = zmq.Context.instance()
    pub = ctx.socket(zmq.PUB)
    pub.bind(endpoint)
    topic_b = topic.encode()

    samples_per_frame = max(width * height, int(sample_rate_hz / max(1, fps)))
    frame_period = 1.0 / max(1, fps)
    stop = False

    def _sig(_sig, _frm):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    try:
        while not stop:
            # Capture enough IQ for one frame (single burst capture)
            iq = sampler.capture(freq_hz, sample_rate_hz, samples_per_frame)
            # Envelope demod (magnitude)
            env = np.abs(iq).astype(np.float32)
            # Light smoothing by moving average over small window
            if env.size >= 64:
                k = 64
                kernel = np.ones(k, dtype=np.float32) / k
                env = np.convolve(env, kernel, mode='same')
            img = build_frame_from_envelope(env, sample_rate_hz, width, height)
            meta = {"width": width, "height": height, "format": "gray8", "ts": time.time(), "freq_hz": freq_hz}
            pub.send_multipart([topic_b, json.dumps(meta).encode("utf-8"), img.tobytes()])
            time.sleep(frame_period * 0.5)
    finally:
        try:
            pub.close(0)
        except Exception:
            pass


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Minimal analog envelope demod publisher (ZMQ frames)")
    ap.add_argument("--freq", dest="freq_hz", type=int, required=True)
    ap.add_argument("--sample-rate", dest="sample_rate", type=float, default=8e6)
    ap.add_argument("--endpoint", default=os.environ.get("RER_FRAMES_ZMQ", "tcp://127.0.0.1:5556"))
    ap.add_argument("--topic", default=os.environ.get("RER_FRAMES_TOPIC", "frames"))
    ap.add_argument("--width", type=int, default=320)
    ap.add_argument("--height", type=int, default=240)
    ap.add_argument("--fps", type=int, default=10)
    args = ap.parse_args(argv)
    run(args.freq_hz, args.endpoint, args.topic, args.sample_rate, args.width, args.height, args.fps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


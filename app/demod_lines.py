from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from typing import Tuple

import numpy as np
import zmq

from .hw_capture import get_sampler


def fm_discriminator(iq: np.ndarray) -> np.ndarray:
    if iq.size < 2:
        return np.zeros(0, dtype=np.float32)
    d = iq[1:] * np.conj(iq[:-1])
    return np.angle(d).astype(np.float32)


def one_pole_dc_block(x: np.ndarray, alpha: float = 0.001) -> np.ndarray:
    """Remove DC/very low-frequency drift by subtracting one-pole low-pass."""
    if x.size == 0:
        return x.astype(np.float32)
    y = np.empty_like(x, dtype=np.float32)
    lp = float(x[0])
    a = float(alpha)
    for i in range(x.size):
        lp = (1.0 - a) * lp + a * float(x[i])
        y[i] = float(x[i]) - lp
    return y


def lowpass_ma(x: np.ndarray, taps: int) -> np.ndarray:
    if taps <= 1:
        return x
    k = int(max(2, taps))
    kernel = np.ones(k, dtype=np.float32) / k
    return np.convolve(x, kernel, mode="same")


def estimate_line_len_samples(envelope: np.ndarray, sample_rate_hz: float, prefer_ntsc: bool = True) -> int:
    # Search near typical line rates
    f_nom = 15734.0 if prefer_ntsc else 15625.0
    # Use autocorrelation to estimate periodicity
    n = len(envelope)
    win = envelope - float(np.mean(envelope))
    # Limit lag search window to plausible range
    min_len = int(sample_rate_hz / (f_nom * 1.15))
    max_len = int(sample_rate_hz / (f_nom * 0.85))
    # Compute FFT-based autocorr for efficiency
    m = 1
    while m < (2 * n):
        m <<= 1
    X = np.fft.rfft(win, m)
    r = np.fft.irfft(X * np.conj(X))[: n]
    r[: min_len] = -np.inf
    if max_len < len(r):
        r[max_len:] = -np.inf
    lag = int(np.argmax(r))
    lag = max(min_len, min(max_len, lag))
    return lag


def build_frame_from_raster(env: np.ndarray, line_len: int, width: int, height: int) -> Tuple[np.ndarray, np.ndarray]:
    # Take the latest chunk sufficient for a frame
    needed = line_len * height
    if env.size < needed:
        pad = needed - env.size
        env = np.pad(env, (pad, 0), mode="edge")
    buf = env[-needed:]
    # Reshape into lines and downsample to width
    lines = buf.reshape((height, line_len))
    # Simple horizontal decimation
    idx = np.linspace(0, line_len - 1, num=width, dtype=np.int32)
    img = lines[:, idx]
    # Normalize per-frame for visibility
    p5 = float(np.percentile(img, 5.0))
    p95 = float(np.percentile(img, 95.0))
    denom = max(1e-6, (p95 - p5))
    img_n = np.clip((img - p5) / denom, 0.0, 1.0)
    return img_n.astype(np.float32), img.astype(np.float32)


def run(freq_hz: int, endpoint: str, topic: str, sample_rate_hz: float, width: int, height: int, fps: int, prefer_ntsc: bool) -> None:
    sampler = get_sampler()
    ctx = zmq.Context.instance()
    pub = ctx.socket(zmq.PUB)
    pub.bind(endpoint)
    topic_b = topic.encode()

    # Capture window ~ 2 frames worth for line estimation and raster buffer
    target_lines = height
    line_len_guess = int(sample_rate_hz / (15734.0 if prefer_ntsc else 15625.0))
    samples_per_iter = max(line_len_guess * target_lines * 2, int(sample_rate_hz / max(5, fps)))
    stop = False

    def _sig(_sig, _frm):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    # Rolling buffer for envelope
    buf = np.zeros(samples_per_iter * 2, dtype=np.float32)
    last_estimate_t = 0.0
    line_len = line_len_guess
    try:
        while not stop:
            iq = sampler.capture(freq_hz, sample_rate_hz, samples_per_iter)
            # Recover baseband video using FM discriminator
            env = fm_discriminator(iq)
            # Remove slow drift, then light smoothing to preserve line timing
            env = one_pole_dc_block(env, alpha=0.001)
            env = lowpass_ma(env, taps=32)
            # Append to rolling buffer
            if env.size >= buf.size:
                buf = env[-buf.size :]
            else:
                buf = np.concatenate([buf[env.size :], env])
            now = time.time()
            # Re-estimate line length at ~1 Hz
            if now - last_estimate_t > 1.0:
                line_len = estimate_line_len_samples(buf, sample_rate_hz, prefer_ntsc=prefer_ntsc)
                last_estimate_t = now
            img_n, _raw = build_frame_from_raster(buf, line_len, width, height)
            img_u8 = (img_n * 255.0).astype(np.uint8)
            meta = {"width": width, "height": height, "format": "gray8", "ts": now, "freq_hz": freq_hz}
            pub.send_multipart([topic_b, json.dumps(meta).encode("utf-8"), img_u8.tobytes()])
    finally:
        try:
            pub.close(0)
        except Exception:
            pass


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Line-timed analog demod publisher (ZMQ frames)")
    ap.add_argument("--freq", dest="freq_hz", type=int, required=True)
    ap.add_argument("--sample-rate", dest="sample_rate", type=float, default=8e6)
    ap.add_argument("--endpoint", default=os.environ.get("RER_FRAMES_ZMQ", "tcp://127.0.0.1:5556"))
    ap.add_argument("--topic", default=os.environ.get("RER_FRAMES_TOPIC", "frames"))
    ap.add_argument("--width", type=int, default=320)
    ap.add_argument("--height", type=int, default=180)
    ap.add_argument("--fps", type=int, default=10)
    ap.add_argument("--ntsc", action="store_true", help="Prefer NTSC line rate (~15.734 kHz); default PAL (~15.625 kHz)")
    args = ap.parse_args(argv)
    run(args.freq_hz, args.endpoint, args.topic, args.sample_rate, args.width, args.height, args.fps, prefer_ntsc=args.ntsc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


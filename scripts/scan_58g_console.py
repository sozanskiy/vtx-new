#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Tuple


def _load_plan(cfg_path: Path) -> dict:
    try:
        return json.loads(cfg_path.read_text())
    except Exception:
        return {}


def _default_freqs() -> List[int]:
    # Raceband default
    return [5658000000, 5695000000, 5732000000, 5769000000, 5806000000, 5843000000, 5880000000, 5917000000]


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Simple 5.8 GHz scanner to console (HackRF via SoapySDR or synthetic)")
    ap.add_argument("--hw", action="store_true", help="Use hardware (HackRF via SoapySDR)")
    ap.add_argument("--plan", type=str, default=str(Path(__file__).resolve().parents[1] / "app" / "config" / "channels.json"))
    ap.add_argument("--sr", type=float, default=8e6, help="Sample rate (Hz)")
    ap.add_argument("--dwell-ms", type=int, default=15, help="Dwell per channel (ms)")
    ap.add_argument("--bw", type=float, default=8e6, help="Channel bandwidth for metrics (Hz)")
    ap.add_argument("--dc-guard", type=float, default=50e3, help="DC guard (Hz)")
    ap.add_argument("--loops", type=int, default=0, help="Number of full sweeps (0 = infinite)")
    args = ap.parse_args(argv)

    if args.hw:
        os.environ["RER_USE_HW"] = "1"

    # Import after env var for sampler selection
    from app.hw_capture import get_sampler
    from app.scanner import band_metrics

    cfg_path = Path(args.plan)
    cfg = _load_plan(cfg_path) if cfg_path.exists() else {}
    freqs: List[int] = []
    for band in cfg.get("bands", []):
        for f in band.get("channels", []):
            try:
                freqs.append(int(f))
            except Exception:
                continue
    if not freqs:
        freqs = _default_freqs()

    sample_rate = float(cfg.get("sample_rate", args.sr))
    dwell_ms = int(cfg.get("dwell_ms", args.dwell_ms))
    channel_bw_hz = float(cfg.get("channel_bw_hz", args.bw))
    dc_guard_hz = float(cfg.get("dc_guard_hz", args.dc_guard))

    sampler = get_sampler()

    print(f"Scanner starting: {len(freqs)} freqs, sr={sample_rate:.0f} Hz, dwell={dwell_ms} ms, bw={channel_bw_hz:.0f} Hz, hw={'yes' if args.hw else 'no'}")
    loop_idx = 0
    try:
        while True:
            t0 = time.time()
            results: List[Tuple[int, float, float]] = []  # (freq, power_db, snr_db)
            for f in freqs:
                num_samples = max(1024, int(sample_rate * (dwell_ms / 1000.0)))
                iq = sampler.capture(f, sample_rate, num_samples)
                p_db, snr_db = band_metrics(iq, sample_rate, channel_bw_hz, dc_guard_hz=dc_guard_hz)
                results.append((f, float(p_db), float(snr_db)))
            dt = time.time() - t0
            # Sort by SNR desc and print top 8
            results.sort(key=lambda r: r[2], reverse=True)
            top = results[:8]
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] sweep {loop_idx} time={dt*1000:.0f} ms  top by SNR:")
            for (f, p_db, snr_db) in top:
                print(f"  {f/1e6:8.1f} MHz  SNR={snr_db:6.2f} dB  Power={p_db:7.2f} dB")
            loop_idx += 1
            if args.loops and loop_idx >= args.loops:
                break
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


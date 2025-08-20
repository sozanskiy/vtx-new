from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from typing import Optional, Tuple, List

import numpy as np
import zmq


def _try_import_soapy() -> Optional[object]:
    try:
        import SoapySDR  # type: ignore

        return SoapySDR
    except Exception:
        return None


class HackRFStream:
    """Minimal SoapySDR-based RX helper with persistent stream and adjustable gains.

    Falls back to None if SoapySDR/HackRF are unavailable.
    """

    def __init__(self, sample_rate_hz: float) -> None:
        self.SoapySDR = _try_import_soapy()
        self.sample_rate_hz = float(sample_rate_hz)
        self.device = None
        self.stream = None
        self.rx_chan = 0
        self.format = "CS16"
        self.lna_gain = 28
        self.vga_gain = 16
        self.amp_enabled = True
        if self.SoapySDR is None:
            return
        try:
            self.device = self.SoapySDR.Device({"driver": "hackrf"})
        except Exception:
            self.device = None
            return
        try:
            self.device.setSampleRate(self.SoapySDR.SOAPY_SDR_RX, self.rx_chan, self.sample_rate_hz)
            self.device.setBandwidth(self.SoapySDR.SOAPY_SDR_RX, self.rx_chan, self.sample_rate_hz)
            # Safe default gains
            try:
                self.device.setGain(self.SoapySDR.SOAPY_SDR_RX, self.rx_chan, "AMP", 1)  # type: ignore[arg-type]
            except Exception:
                pass
            try:
                self.device.setGain(self.SoapySDR.SOAPY_SDR_RX, self.rx_chan, "LNA", self.lna_gain)
            except Exception:
                pass
            try:
                self.device.setGain(self.SoapySDR.SOAPY_SDR_RX, self.rx_chan, "VGA", self.vga_gain)
            except Exception:
                pass
            try:
                self.stream = self.device.setupStream(self.SoapySDR.SOAPY_SDR_RX, self.format, [self.rx_chan])
            except Exception:
                self.format = "CS8"
                self.stream = self.device.setupStream(self.SoapySDR.SOAPY_SDR_RX, self.format, [self.rx_chan])
            self.device.activateStream(self.stream)
        except Exception:
            self.close()

    def is_ready(self) -> bool:
        return self.device is not None and self.stream is not None

    def set_center_frequency(self, freq_hz: float) -> None:
        if not self.is_ready():
            return
        try:
            self.device.setFrequency(self.SoapySDR.SOAPY_SDR_RX, self.rx_chan, float(freq_hz))
        except Exception:
            pass

    def set_gains(self, *, lna: Optional[int] = None, vga: Optional[int] = None, amp: Optional[bool] = None) -> None:
        if not self.is_ready():
            return
        try:
            if amp is not None:
                try:
                    self.device.setGain(self.SoapySDR.SOAPY_SDR_RX, self.rx_chan, "AMP", 1 if amp else 0)  # type: ignore[arg-type]
                    self.amp_enabled = bool(amp)
                except Exception:
                    pass
            if lna is not None:
                try:
                    self.lna_gain = int(max(0, min(40, lna)))
                    self.device.setGain(self.SoapySDR.SOAPY_SDR_RX, self.rx_chan, "LNA", self.lna_gain)
                except Exception:
                    pass
            if vga is not None:
                try:
                    self.vga_gain = int(max(0, min(62, vga)))
                    self.device.setGain(self.SoapySDR.SOAPY_SDR_RX, self.rx_chan, "VGA", self.vga_gain)
                except Exception:
                    pass
        except Exception:
            pass

    def read_samples(self, num_samples: int) -> np.ndarray:
        if not self.is_ready():
            return np.zeros(num_samples, dtype=np.complex64)
        out = np.empty(num_samples, dtype=np.complex64)
        total = 0
        tmp_i16 = np.empty(num_samples * 2, dtype=np.int16)
        tmp_i8 = np.empty(num_samples * 2, dtype=np.int8)
        try:
            while total < num_samples:
                need = min(4096, num_samples - total)
                if self.format == "CS16":
                    sr = self.device.readStream(self.stream, [tmp_i16[: need * 2]], need)
                    if sr.ret > 0:
                        vec = tmp_i16[: sr.ret * 2].astype(np.float32) / 32768.0
                        out[total : total + sr.ret] = vec[0::2] + 1j * vec[1::2]
                        total += sr.ret
                        continue
                    else:
                        # Try fallback
                        self.format = "CS8"
                sr = self.device.readStream(self.stream, [tmp_i8[: need * 2]], need)
                if sr.ret > 0:
                    vec8 = tmp_i8[: sr.ret * 2].astype(np.float32) / 128.0
                    out[total : total + sr.ret] = vec8[0::2] + 1j * vec8[1::2]
                    total += sr.ret
            return out
        except Exception:
            return np.zeros(num_samples, dtype=np.complex64)

    def read_samples_with_stats(self, num_samples: int) -> Tuple[np.ndarray, float, float]:
        """Return IQ along with (rms, clip_fraction)."""
        if not self.is_ready():
            iq = np.zeros(num_samples, dtype=np.complex64)
            return iq, 0.0, 0.0
        out = np.empty(num_samples, dtype=np.complex64)
        total = 0
        tmp_i16 = np.empty(num_samples * 2, dtype=np.int16)
        tmp_i8 = np.empty(num_samples * 2, dtype=np.int8)
        clips = 0
        raw = 0
        try:
            while total < num_samples:
                need = min(4096, num_samples - total)
                if self.format == "CS16":
                    sr = self.device.readStream(self.stream, [tmp_i16[: need * 2]], need)
                    if sr.ret > 0:
                        sl = tmp_i16[: sr.ret * 2]
                        # Count raw values near rails as clip
                        clips += int(np.count_nonzero((sl >= 32760) | (sl <= -32760)))
                        raw += int(sl.size)
                        vec = sl.astype(np.float32) / 32768.0
                        out[total : total + sr.ret] = vec[0::2] + 1j * vec[1::2]
                        total += sr.ret
                        continue
                    else:
                        self.format = "CS8"
                sr = self.device.readStream(self.stream, [tmp_i8[: need * 2]], need)
                if sr.ret > 0:
                    sl8 = tmp_i8[: sr.ret * 2]
                    clips += int(np.count_nonzero((sl8 >= 127) | (sl8 <= -127)))
                    raw += int(sl8.size)
                    vec8 = sl8.astype(np.float32) / 128.0
                    out[total : total + sr.ret] = vec8[0::2] + 1j * vec8[1::2]
                    total += sr.ret
            rms = float(np.sqrt(np.mean(np.abs(out[:total]) ** 2) + 1e-12))
            clip_frac = float(clips) / float(max(1, raw))
            return out[:total], rms, clip_frac
        except Exception:
            iq = np.zeros(total or num_samples, dtype=np.complex64)
            return iq, 0.0, 0.0

    def measure_rms(self, num_samples: int = 32768) -> float:
        iq = self.read_samples(num_samples)
        return float(np.sqrt(np.mean(np.abs(iq) ** 2) + 1e-12))

    def close(self) -> None:
        try:
            if self.device is not None and self.stream is not None:
                try:
                    self.device.deactivateStream(self.stream)
                except Exception:
                    pass
                try:
                    self.device.closeStream(self.stream)
                except Exception:
                    pass
        finally:
            self.stream = None
            self.device = None


def fm_discriminator(iq: np.ndarray) -> np.ndarray:
    if iq.size < 2:
        return np.zeros(0, dtype=np.float32)
    d = iq[1:] * np.conj(iq[:-1])
    return np.angle(d).astype(np.float32)


def dc_block(x: np.ndarray, alpha: float = 0.001) -> np.ndarray:
    if x.size == 0:
        return x.astype(np.float32)
    y = np.empty_like(x, dtype=np.float32)
    lp = float(x[0])
    a = float(alpha)
    for i in range(x.size):
        lp = (1.0 - a) * lp + a * float(x[i])
        y[i] = float(x[i]) - lp
    return y


def moving_average(x: np.ndarray, taps: int) -> np.ndarray:
    if taps <= 1:
        return x.astype(np.float32)
    k = int(max(2, taps))
    kernel = np.ones(k, dtype=np.float32) / k
    return np.convolve(x.astype(np.float32), kernel, mode="same")


def estimate_line_len(envelope: np.ndarray, sample_rate_hz: float, prefer_ntsc: Optional[bool]) -> Tuple[int, float]:
    """Estimate samples-per-line and return (line_len, confidence).

    If prefer_ntsc is None, search both NTSC and PAL windows and return best.
    Confidence is derived from normalized autocorrelation peak prominence.
    """
    def _peak(lr_nom: float) -> Tuple[int, float]:
        n = int(envelope.size)
        if n < 4096:
            return int(sample_rate_hz / lr_nom), 0.0
        win = envelope.astype(np.float32) - float(np.mean(envelope))
        min_len = int(sample_rate_hz / (lr_nom * 1.15))
        max_len = int(sample_rate_hz / (lr_nom * 0.85))
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
        # Confidence proxy: peak relative to local neighborhood
        left = max(min_len, lag - 64)
        right = min(max_len, lag + 64)
        nb = np.copy(r[left:right])
        nb[lag - left] = -np.inf
        neigh = float(np.nanmax(nb)) if nb.size > 0 else -np.inf
        pk = float(r[lag])
        conf = float(0.0 if not np.isfinite(neigh) else np.tanh(max(0.0, (pk - neigh)) / (abs(neigh) + 1e-6)))
        return lag, conf

    if prefer_ntsc is None:
        cand = []
        for lr in (15734.0, 15625.0):
            ln, cf = _peak(lr)
            cand.append((ln, cf))
        cand.sort(key=lambda x: x[1], reverse=True)
        return cand[0]
    else:
        return _peak(15734.0 if prefer_ntsc else 15625.0)


def frame_from_raster(env: np.ndarray, line_len: int, width: int, height: int) -> Tuple[np.ndarray, float]:
    needed = line_len * height
    if env.size < needed:
        pad = needed - env.size
        env = np.pad(env, (pad, 0), mode="edge")
    buf = env[-needed:]
    lines = buf.reshape((height, line_len))
    idx = np.linspace(0, line_len - 1, num=width, dtype=np.int32)
    img = lines[:, idx].astype(np.float32)
    # Quality metric: average correlation between adjacent lines (structure repeatability)
    if height >= 3:
        corrs = []
        for r in range(1, height):
            a = img[r - 1]
            b = img[r]
            a = (a - float(np.mean(a))) / (float(np.std(a)) + 1e-6)
            b = (b - float(np.mean(b))) / (float(np.std(b)) + 1e-6)
            corrs.append(float(np.mean(a * b)))
        quality = float(np.clip(np.mean(corrs), -1.0, 1.0))
    else:
        quality = 0.0
    # Normalize image for output
    p5 = float(np.percentile(img, 5.0))
    p95 = float(np.percentile(img, 95.0))
    denom = max(1e-6, (p95 - p5))
    img_n = np.clip((img - p5) / denom, 0.0, 1.0)
    img_u8 = (img_n * 255.0).astype(np.uint8)
    return img_u8, quality


def quality_metric(iq: np.ndarray, sample_rate_hz: float, prefer_ntsc: Optional[bool], width: int, height: int) -> Tuple[float, int, np.ndarray]:
    env = fm_discriminator(iq)
    env = dc_block(env, alpha=0.001)
    env = moving_average(env, 32)
    line_len, _conf = estimate_line_len(env, sample_rate_hz, prefer_ntsc)
    img_u8, q = frame_from_raster(env, line_len, width, height)
    return q, line_len, img_u8


def initial_lock(stream: HackRFStream, base_freq_hz: int, sample_rate_hz: float, prefer_ntsc: Optional[bool], width: int, height: int) -> Tuple[int, int, float]:
    """Search LO offsets to maximize quality metric. Returns (best_freq, best_line_len, best_q)."""
    offsets = list(range(-2_000_000, 2_000_001, 250_000))
    best_q = -1e9
    best_f = base_freq_hz
    best_line = int(sample_rate_hz / 15680.0)
    for off in offsets:
        stream.set_center_frequency(base_freq_hz + off)
        iq = stream.read_samples(max(65536, int(sample_rate_hz * 0.03)))
        q, line_len, _img = quality_metric(iq, sample_rate_hz, prefer_ntsc, width, height)
        if q > best_q:
            best_q = q
            best_f = base_freq_hz + off
            best_line = line_len
    # If nothing stands out, widen search once
    if best_q < 0.05:
        offsets = list(range(-5_000_000, 5_000_001, 500_000))
        for off in offsets:
            stream.set_center_frequency(base_freq_hz + off)
            iq = stream.read_samples(max(65536, int(sample_rate_hz * 0.03)))
            q, line_len, _img = quality_metric(iq, sample_rate_hz, prefer_ntsc, width, height)
            if q > best_q:
                best_q = q
                best_f = base_freq_hz + off
                best_line = line_len
    # Refine around the best
    fine_offsets = list(range(-100_000, 100_001, 10_000))
    center = best_f
    for off in fine_offsets:
        stream.set_center_frequency(center + off)
        iq = stream.read_samples(max(65536, int(sample_rate_hz * 0.03)))
        q, line_len, _img = quality_metric(iq, sample_rate_hz, prefer_ntsc, width, height)
        if q > best_q:
            best_q = q
            best_f = center + off
            best_line = line_len
    return int(best_f), int(best_line), float(best_q)


def auto_gain(stream: HackRFStream, target_rms: float = 0.25, max_clip: float = 0.01) -> None:
    """Crude AGC for HackRF front-end using LNA/VGA to steer RMS near target with clip avoidance."""
    if not stream.is_ready():
        return
    # Probe current RMS/clip
    _iq, rms, clip = stream.read_samples_with_stats(16384)
    # Iterate a few adjustments
    try:
        for _ in range(8):
            err = target_rms - rms
            # Avoid clipping aggressively
            if clip > max_clip:
                # Reduce VGA first
                new_vga = max(0, stream.vga_gain - 6)
                stream.set_gains(vga=new_vga)
            elif abs(err) < 0.03:
                break
            # Read current gains is not available; nudge VGA primarily, LNA secondarily
            # Heuristic: 6 dB per 6 steps approximately for VGA/LNA
            step = 6 if err > 0 else -6
            # Try VGA first
            new_vga = min(62, max(0, stream.vga_gain + step))
            stream.set_gains(vga=new_vga)
            # If still far, adjust LNA moderately
            _iq, rms, clip = stream.read_samples_with_stats(16384)
            if (err > 0 and rms < target_rms * 0.8) or (err < 0 and rms > target_rms * 1.2):
                new_lna = min(40, max(0, stream.lna_gain + step))
                stream.set_gains(lna=new_lna)
            _iq, rms, clip = stream.read_samples_with_stats(16384)
    except Exception:
        pass


def run(freq_hz: int, endpoint: str, topic: str, sample_rate_hz: float, width: int, height: int, fps: int, prefer_ntsc: bool) -> None:
    ctx = zmq.Context.instance()
    pub = ctx.socket(zmq.PUB)
    try:
        pub.bind(endpoint)
        print(f"[autotune] ZMQ publisher bound at {endpoint} topic={topic}", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[autotune] ERROR: failed to bind ZMQ at {endpoint}: {e}", file=sys.stderr, flush=True)
        raise
    topic_b = topic.encode()

    stop = False

    def _sig(_sig, _frm):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    use_hw = os.environ.get("RER_USE_HW", "0") == "1"
    print(f"[autotune] RER_USE_HW={use_hw}", file=sys.stderr, flush=True)
    stream = HackRFStream(sample_rate_hz) if use_hw else None

    try:
        if stream is not None and stream.is_ready():
            # Hardware path with auto gain / tuning
            print("[autotune] Using HackRF hardware", file=sys.stderr, flush=True)
            auto_gain(stream)
            tuned_f, line_len, q = initial_lock(stream, int(freq_hz), sample_rate_hz, prefer_ntsc, width, height)
            stream.set_center_frequency(tuned_f)
            print(f"[autotune] Initial lock at {tuned_f/1e6:.3f} MHz, q={q:.3f}, line_len={line_len}", file=sys.stderr, flush=True)
            # Main loop
            last_quality = q
            last_relock = time.time()
            # Buffer ~ 2 frames worth
            samples_per_iter = max(line_len * height * 2, int(sample_rate_hz / max(5, fps)))
            while not stop:
                # Periodically adjust gains in-run
                if int(time.time() * 2) % 10 == 0:  # ~5 Hz check window; cheap heuristic
                    _iq_agc, rms_agc, clip_agc = stream.read_samples_with_stats(8192)
                    if (rms_agc < 0.18) or (rms_agc > 0.35) or (clip_agc > 0.01):
                        auto_gain(stream)
                iq = stream.read_samples(samples_per_iter)
                env = fm_discriminator(iq)
                env = dc_block(env, alpha=0.001)
                env = moving_average(env, 32)
                # Periodically re-estimate line length
                if (time.time() - last_relock) > 1.0:
                    line_len, _ = estimate_line_len(env, sample_rate_hz, prefer_ntsc)
                frame, q = frame_from_raster(env, line_len, width, height)
                # Adaptive re-lock if quality collapses
                if q < (last_quality - 0.15) or q < 0.05:
                    tuned_f, line_len, q2 = initial_lock(stream, tuned_f, sample_rate_hz, prefer_ntsc, width, height)
                    stream.set_center_frequency(tuned_f)
                    last_relock = time.time()
                    last_quality = q2
                else:
                    # Light AFC: probe small deltas and hill-climb if better
                    deltas = (-50_000, -25_000, 0, 25_000, 50_000)
                    best_df = 0
                    best_q_local = q
                    for df in deltas:
                        if df == 0:
                            continue
                        stream.set_center_frequency(tuned_f + df)
                        iq_s = stream.read_samples(max(32768, int(sample_rate_hz * 0.01)))
                        q_s, _, _ = quality_metric(iq_s, sample_rate_hz, prefer_ntsc, width, height)
                        if q_s > best_q_local + 0.02:
                            best_q_local = q_s
                            best_df = df
                    if best_df != 0:
                        tuned_f += best_df
                        stream.set_center_frequency(tuned_f)
                    last_quality = 0.8 * last_quality + 0.2 * q
                meta = {"width": width, "height": height, "format": "gray8", "ts": time.time(), "freq_hz": int(tuned_f)}
                pub.send_multipart([topic_b, json.dumps(meta).encode("utf-8"), frame.tobytes()])
        else:
            # Fallback synthetic path: emulate frames without hardware
            if not use_hw:
                print("[autotune] Hardware mode disabled (RER_USE_HW!=1). Using synthetic frames.", file=sys.stderr, flush=True)
            else:
                print("[autotune] SoapySDR/HackRF unavailable; using synthetic frames.", file=sys.stderr, flush=True)
            t0 = time.time()
            frame_idx = 0
            period = 1.0 / max(1, fps)
            while not stop:
                h, w = height, width
                img = np.zeros((h, w), dtype=np.uint8)
                x = int(((time.time() - t0) * 40) % w)
                img[:, max(0, x - 2) : min(w, x + 2)] = 200
                img[::16, :] = 50
                meta = {"width": width, "height": height, "format": "gray8", "ts": time.time(), "freq_hz": int(freq_hz)}
                pub.send_multipart([topic_b, json.dumps(meta).encode("utf-8"), img.tobytes()])
                frame_idx += 1
                time.sleep(period)
    finally:
        try:
            if stream is not None:
                stream.close()
        except Exception:
            pass
        try:
            pub.close(0)
        except Exception:
            pass


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Auto-tuning analog demod publisher (ZMQ frames)")
    ap.add_argument("--freq", dest="freq_hz", type=int, required=True)
    ap.add_argument("--sample-rate", dest="sample_rate", type=float, default=8e6)
    ap.add_argument("--endpoint", default=os.environ.get("RER_FRAMES_ZMQ", "tcp://127.0.0.1:5556"))
    ap.add_argument("--topic", default=os.environ.get("RER_FRAMES_TOPIC", "frames"))
    ap.add_argument("--width", type=int, default=320)
    ap.add_argument("--height", type=int, default=240)
    ap.add_argument("--fps", type=int, default=10)
    ap.add_argument("--ntsc", action="store_true", help="Prefer NTSC line rate (~15.734 kHz); default PAL (~15.625 kHz)")
    args = ap.parse_args(argv)
    run(args.freq_hz, args.endpoint, args.topic, args.sample_rate, args.width, args.height, args.fps, prefer_ntsc=args.ntsc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


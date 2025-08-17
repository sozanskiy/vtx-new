"""Scanner utilities.

Contains signal metrics helpers to support the scanner loop. Hardware capture will
be implemented in a later step, but the metrics are provided here for reuse.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Deque, Dict, List, Optional, Tuple
import asyncio
import time
from collections import defaultdict, deque

import numpy as np
from .storage import list_top_candidates, upsert_candidate
from .hw_capture import get_sampler, IQSampler


def band_metrics(iq: np.ndarray, sample_rate_hz: float, channel_bw_hz: float, dc_guard_hz: float = 150e3) -> Tuple[float, float]:
    """Compute band power and SNR for complex baseband IQ.

    - In-band region: clamp to ≤ 0.75×Nyquist to leave room for a noise ring
    - Noise region: high‑frequency ring near Nyquist (e.g., 0.875–0.98×Nyquist)
    Returns (band_power_db, snr_db) in relative dB (not absolute dBm).
    """
    if iq.dtype != np.complex64 and iq.dtype != np.complex128:
        iq = iq.astype(np.complex64)
    # 1) mean removal
    iq = iq - np.mean(iq)
    # 2) window + complex FFT power
    n = len(iq)
    if n <= 0:
        return -120.0, 0.0
    win = np.hanning(n)
    X = np.fft.fft(iq * win)
    psd = (np.abs(X) ** 2) / (np.sum(win ** 2))
    freqs = np.fft.fftfreq(n, d=1.0 / sample_rate_hz)
    # In-band mask (±bw/2), clamped to 0.7×Nyquist (leave margin)
    nyq = sample_rate_hz / 2.0
    half_bw = min(channel_bw_hz / 2.0, nyq * 0.70)
    abs_freqs = np.abs(freqs)
    m_band = (abs_freqs <= half_bw)
    # DC guard mask
    m_guard = (abs_freqs < dc_guard_hz)
    m_use = m_band & (~m_guard)
    # Power as mean per bin to compare consistently with noise mean/median
    band_lin = float(np.mean(psd[m_use])) if np.any(m_use) else 1e-12
    # Noise as near-band ring just outside the in-band window
    m_noise = (abs_freqs >= half_bw * 1.05) & (abs_freqs <= min(nyq * 0.98, half_bw * 1.30))
    noise_bins = psd[m_noise]
    noise_lin = float(np.median(noise_bins)) if noise_bins.size else 1e-12
    band_power_db = 10.0 * np.log10(band_lin + 1e-20)
    snr_db = 10.0 * np.log10((band_lin + 1e-20) / (noise_lin + 1e-20))
    return float(band_power_db), float(snr_db)


def band_metrics_both(iq: np.ndarray, sample_rate_hz: float, channel_bw_hz: float, dc_guard_hz: float = 150e3) -> Tuple[float, float, float]:
    if iq.dtype != np.complex64 and iq.dtype != np.complex128:
        iq = iq.astype(np.complex64)
    iq = iq - np.mean(iq)
    n = len(iq)
    if n <= 0:
        return -120.0, 0.0, 0.0
    win = np.hanning(n)
    X = np.fft.fft(iq * win)
    psd = (np.abs(X) ** 2) / (np.sum(win ** 2))
    freqs = np.fft.fftfreq(n, d=1.0 / sample_rate_hz)
    nyq = sample_rate_hz / 2.0
    half_bw = min(channel_bw_hz / 2.0, nyq * 0.70)
    abs_freqs = np.abs(freqs)
    m_band = (abs_freqs <= half_bw)
    m_guard = (abs_freqs < dc_guard_hz)
    m_use = m_band & (~m_guard)
    band_bins = psd[m_use]
    band_lin = float(np.mean(band_bins)) if band_bins.size else 1e-12
    peak_lin = float(np.max(band_bins)) if band_bins.size else 1e-12
    m_noise = (abs_freqs >= half_bw * 1.05) & (abs_freqs <= min(nyq * 0.98, half_bw * 1.30))
    noise_bins = psd[m_noise]
    noise_lin = float(np.median(noise_bins)) if noise_bins.size else 1e-12
    band_power_db = 10.0 * np.log10(band_lin + 1e-20)
    snr_mean_db = 10.0 * np.log10((band_lin + 1e-20) / (noise_lin + 1e-20))
    snr_peak_db = 10.0 * np.log10((peak_lin + 1e-20) / (noise_lin + 1e-20))
    return float(band_power_db), float(snr_mean_db), float(snr_peak_db)


@dataclass
class ScannerConfig:
    sample_rate_hz: float = 8e6
    dwell_ms: int = 15
    channel_bw_hz: float = 8e6
    dc_guard_hz: float = 50e3
    min_snr_db: float = 6.0


def not_implemented_yet() -> None:
    raise NotImplementedError("Real scanner is implemented in a later step.")


class Scanner:
    """Synthetic scanner with EMA and N-of-M debounce.

    Replace synthetic measurement with real HackRF capture later.
    """

    def __init__(
        self,
        *,
        freqs_hz: List[int],
        sample_rate_hz: float = 8e6,
        dwell_ms: int = 15,
        channel_bw_hz: float = 8e6,
        min_snr_db: float = 6.0,
        alert_hits: int = 3,
        alert_window: int = 5,
        ema_alpha: float = 0.1,
        broadcast: Callable[[Dict[str, object]], Awaitable[None]],
    ) -> None:
        self.freqs_hz = [int(f) for f in freqs_hz]
        self.sample_rate_hz = float(sample_rate_hz)
        self.dwell_ms = int(dwell_ms)
        self.channel_bw_hz = float(channel_bw_hz)
        self.min_snr_db = float(min_snr_db)
        self.alert_hits = int(alert_hits)
        self.alert_window = int(alert_window)
        self.ema_alpha = float(ema_alpha)
        self.broadcast = broadcast

        self.ema_power_dbm: Dict[int, float] = {f: -60.0 for f in self.freqs_hz}
        self.ema_snr_db: Dict[int, float] = {f: 0.0 for f in self.freqs_hz}
        self.activity_windows: Dict[int, Deque[bool]] = defaultdict(lambda: deque(maxlen=self.alert_window))

    async def run(self, stop_event: asyncio.Event, sampler: Optional[IQSampler] = None) -> None:
        last_push = 0.0
        sampler = sampler or get_sampler()
        try:
            while not stop_event.is_set():
                for f in self.freqs_hz:
                    if stop_event.is_set():
                        break
                    # Measure: if sampler is synthetic, result will emulate; if HW, this captures real IQ
                    num_samples = max(1024, int(self.sample_rate_hz * (self.dwell_ms / 1000.0)))
                    iq = sampler.capture(f, self.sample_rate_hz, num_samples)
                    # Compute metrics
                    band_power_db, snr = band_metrics(
                        iq,
                        sample_rate_hz=self.sample_rate_hz,
                        channel_bw_hz=self.channel_bw_hz,
                        dc_guard_hz=self.channel_bw_hz * 0.00625,  # ~50 kHz at 8 MHz
                    )
                    # EMA for power and SNR
                    self.ema_power_dbm[f] = (1.0 - self.ema_alpha) * self.ema_power_dbm[f] + self.ema_alpha * band_power_db
                    self.ema_snr_db[f] = (1.0 - self.ema_alpha) * self.ema_snr_db[f] + self.ema_alpha * snr
                    # Debounce window
                    is_candidate = self.ema_snr_db[f] >= self.min_snr_db
                    win = self.activity_windows[f]
                    win.append(is_candidate)
                    hits = sum(1 for v in win if v)
                    status = "active" if (hits >= self.alert_hits and len(win) >= min(self.alert_window, self.alert_hits)) else ("new" if is_candidate else "lost")
                    # Upsert DB
                    upsert_candidate(
                        freq_hz=f,
                        snr_db=round(float(self.ema_snr_db[f]), 2),
                        power_dbm=round(float(self.ema_power_dbm[f]), 2),
                        status=status,
                        ema_alpha=self.ema_alpha,
                    )
                    # Broadcast at ~5 Hz
                    now = time.time()
                    if now - last_push >= 0.2:
                        rows = list_top_candidates(10)
                        await self.broadcast({"type": "candidates", "items": rows})
                        last_push = now
                    await asyncio.sleep(max(0.0, self.dwell_ms / 1000.0))
        finally:
            stop_event.clear()


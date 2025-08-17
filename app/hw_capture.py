from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class IQSampler:
    """Abstract sampler interface."""

    def capture(self, freq_hz: int, sample_rate_hz: float, num_samples: int) -> np.ndarray:
        raise NotImplementedError


class SyntheticSampler(IQSampler):
    def __init__(self, hot_freq_hz: int = 5806000000) -> None:
        self.hot = int(hot_freq_hz)

    def capture(self, freq_hz: int, sample_rate_hz: float, num_samples: int) -> np.ndarray:
        # Complex Gaussian noise + optional tone to mimic signal energy when near the hot freq
        noise = (np.random.normal(scale=0.2, size=num_samples) + 1j * np.random.normal(scale=0.2, size=num_samples)).astype(
            np.complex64
        )
        if int(freq_hz) == self.hot:
            t = np.arange(num_samples, dtype=np.float32) / float(sample_rate_hz)
            tone = np.exp(2j * np.pi * 10e3 * t).astype(np.complex64) * 0.8
            return noise + tone
        return noise


class SoapyHackRFSampler(IQSampler):
    def __init__(self) -> None:
        import SoapySDR  # type: ignore

        self.SoapySDR = SoapySDR
        self.sdr = SoapySDR.Device({"driver": "hackrf"})
        self.rx_chan = 0
        # Set per-component gains for better sensitivity (safe fallbacks)
        try:
            # Enable on-board RF amp if available (boolean on HackRF)
            try:
                self.sdr.setGain(self.SoapySDR.SOAPY_SDR_RX, self.rx_chan, "AMP", 1)  # type: ignore[arg-type]
            except Exception:
                pass
            # LNA (front-end) and VGA (baseband) gains; moderate to avoid overload
            try:
                self.sdr.setGain(self.SoapySDR.SOAPY_SDR_RX, self.rx_chan, "LNA", 28)
            except Exception:
                pass
            try:
                self.sdr.setGain(self.SoapySDR.SOAPY_SDR_RX, self.rx_chan, "VGA", 16)
            except Exception:
                pass
        except Exception:
            pass
        self.stream = None
        self.current_sr = None

    def _ensure_stream(self, sample_rate_hz: float) -> None:
        if self.stream is not None and self.current_sr == sample_rate_hz:
            return
        if self.stream is not None:
            try:
                self.sdr.deactivateStream(self.stream)
            except Exception:
                pass
            try:
                self.sdr.closeStream(self.stream)
            except Exception:
                pass
        self.sdr.setSampleRate(self.SoapySDR.SOAPY_SDR_RX, self.rx_chan, float(sample_rate_hz))
        self.sdr.setBandwidth(self.SoapySDR.SOAPY_SDR_RX, self.rx_chan, float(sample_rate_hz))
        # Request CS16 (int16) if available; fall back to CS8
        fmt = "CS16"
        try:
            self.stream = self.sdr.setupStream(self.SoapySDR.SOAPY_SDR_RX, fmt, [self.rx_chan])
        except Exception:
            fmt = "CS8"
            self.stream = self.sdr.setupStream(self.SoapySDR.SOAPY_SDR_RX, fmt, [self.rx_chan])
        self.current_sr = sample_rate_hz

    def capture(self, freq_hz: int, sample_rate_hz: float, num_samples: int) -> np.ndarray:
        SoapySDR = self.SoapySDR
        self._ensure_stream(sample_rate_hz)
        self.sdr.setFrequency(SoapySDR.SOAPY_SDR_RX, self.rx_chan, float(freq_hz))
        self.sdr.activateStream(self.stream)
        # Buffer sizes
        # For CS16: 2 * int16 per complex sample
        # For CS8:  2 * int8  per complex sample
        # Read into numpy; convert to complex64
        total = 0
        out = np.empty(num_samples, dtype=np.complex64)
        tmp_i16 = np.empty(num_samples * 2, dtype=np.int16)
        tmp_i8 = np.empty(num_samples * 2, dtype=np.int8)
        use_i16 = True
        try:
            while total < num_samples:
                # Try 4096 frames per read
                need = min(4096, num_samples - total)
                # Probe stream format by trying int16 first
                try:
                    sr = self.sdr.readStream(self.stream, [tmp_i16[: need * 2]], need)
                    if sr.ret > 0:
                        vec = tmp_i16[: sr.ret * 2].astype(np.float32) / 32768.0
                        out[total : total + sr.ret] = vec[0::2] + 1j * vec[1::2]
                        total += sr.ret
                        use_i16 = True
                        continue
                except Exception:
                    use_i16 = False
                # Fallback to int8
                sr = self.sdr.readStream(self.stream, [tmp_i8[: need * 2]], need)
                if sr.ret > 0:
                    vec8 = tmp_i8[: sr.ret * 2].astype(np.float32) / 128.0
                    out[total : total + sr.ret] = vec8[0::2] + 1j * vec8[1::2]
                    total += sr.ret
            return out
        finally:
            try:
                self.sdr.deactivateStream(self.stream)
            except Exception:
                pass
            # Also close the stream to avoid leaving the device busy between operations
            try:
                if self.stream is not None:
                    self.sdr.closeStream(self.stream)
            except Exception:
                pass
            self.stream = None
            self.current_sr = None

    def clear(self) -> None:
        """Ensure no active streams remain (safe to call anytime)."""
        try:
            if self.stream is not None:
                try:
                    self.sdr.deactivateStream(self.stream)
                except Exception:
                    pass
                try:
                    self.sdr.closeStream(self.stream)
                except Exception:
                    pass
        finally:
            self.stream = None
            self.current_sr = None

    def close(self) -> None:
        """Release device resources."""
        self.clear()
        try:
            del self.sdr
        except Exception:
            pass


def get_sampler() -> IQSampler:
    use_hw = os.environ.get("RER_USE_HW", "0") == "1"
    if use_hw:
        try:
            return SoapyHackRFSampler()
        except Exception:
            pass
    return SyntheticSampler()


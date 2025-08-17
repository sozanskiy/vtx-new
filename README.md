# Goal

An MVP that runs on a Raspberry Pi–class mini‑PC with a HackRF One to **scan for active FPV VTX signals** (primarily 5.8 GHz analog bands, optional others), present a **simple list of strongest candidates/alerts**, and allow the user to **focus on one candidate** to attempt **basic analog video demod/preview** and/or record IQ for later analysis.

> Scope is usability-first, not max performance. Avoid charts; keep UI to a simple table + focus buttons.

---

## Assumptions & Constraints

* **Hardware**: Raspberry Pi 5 (64‑bit), HackRF One (TX disabled for MVP), passive/active cooling. Optional: 5.8 GHz bandpass filter + LNA for sensitivity.
* **Targets**: Analog FPV VTX on standard 5.8 GHz channel plans (Raceband, A/B/E/F/FS, etc.). Digital (DJI/OcuSync, HDZero) **not decoded**; treat as “wideband activity” candidates only.
* **Operating limits**: Stable HackRF sample rates on Pi ≈ 8–10 MS/s continuous without drops (conservative default **8 MS/s**). Channel BW assumption for analog ≈ **6–8 MHz**.
* **Latency budget**:

  * Scan sweep (48–72 preset channels): **≤1.5 s** per full pass with 10–20 ms dwell.
  * Focused demod pipeline: render preview ≤250 ms end‑to‑end.

---

## High-Level Architecture

### Processes (micro‑services on one box)

1. **Scanner** (Python, asyncio)

   * Tunes through a **channel plan** (JSON) or **coarse sweep** windows.
   * For each center frequency: captures short IQ slice → computes **band power**, **noise floor**, optional **quick signature test** → updates a **Candidate Store**.
   * Emits **events** for “new/changed candidate” via ZeroMQ pub.

2. **Candidate Tracker & Store** (SQLite + JSONL logs)

   * Keeps EMA per frequency: `power_dbm_ema`, `snr_est`, `last_seen`, `hit_count`.
   * Debounce thresholds and N‑of‑M logic for alerts.

3. **Demodulator** (GNU Radio 3.10 flowgraph, launched on demand)

   * When **focus** is requested, starts a per‑frequency flowgraph:

     * IQ (HackRF) → DC removal → AGC → **Envelope/AM‑like video** demod (for analog FPV) → clamp/scale → frame rate estimator (approx. 25/30fps) → **ZMQ PUB** (gr‑zeromq) of grayscale frames.
     * Optional **audio subcarrier** demod to ALSA.
     * Optional **IQ recorder** to file (`.cfile`/`.cs16`).

4. **API Gateway** (FastAPI + Uvicorn)

   * REST/WS endpoints for control & data:

     * `GET /candidates` → list top N.
     * `POST /focus` `{freq}` → start/stop demod; returns stream URL.
     * `POST /scan/start|stop`, `GET /scan/status`.
     * `POST /record` `{freq, iq|video}`.
     * `GET /config` / `PUT /config` for channel plans.
     * `GET /health`.

5. **Video Bridge**

   * **Option A (simplest)**: small Python HTTP server that consumes ZMQ frames and serves **MJPEG** at `/video.mjpeg` (multipart/x‑mixed‑replace).
   * **Option B**: pipe ZMQ → `ffmpeg` → **HLS** (for browser/video tag tolerance), served by the API Gateway.

6. **UI** (two options)

   * **A. Minimal HTML** served by FastAPI: one table, “Focus” buttons, a `<img src="/video.mjpeg">` when focused, and a basic settings view.
   * **B. Your existing Nuxt 3 kiosk**: same endpoints; a single page that renders candidates and shows MJPEG on focus.

7. **Supervisor**

   * `systemd` or `supervisord` units per service to auto‑start and restart on failure.

---

## Data Flow

1. Scanner tunes each channel → grabs \~**10–20 ms** @ **8 MS/s** → computes **PSD** over ±4 MHz.
2. Integrates in‑band power; estimates local noise from edges; calculates SNR.
3. Updates Candidate Store; publishes event if threshold crossing or rank changed.
4. UI polls `/candidates` or subscribes via WS; shows top list.
5. User clicks **Focus** → API launches/attaches Demodulator → Video Bridge exposes MJPEG URL → UI shows preview.
6. User can **Record IQ**/video for a focused session.

---

## Channel Plan & Config

* **config/channels.json** (example schema):

```json
{
  "bands": [
    {
      "name": "Raceband",
      "channels": [5658e6, 5695e6, 5732e6, 5769e6, 5806e6, 5843e6, 5880e6, 5917e6]
    },
    { "name": "Band A", "channels": [5865e6, 5845e6, 5825e6, 5805e6, 5785e6, 5765e6, 5745e6, 5725e6] }
  ],
  "dwell_ms": 15,
  "sample_rate": 8e6,
  "channel_bw_hz": 8e6,
  "min_snr_db": 6,
  "alert_persistence": {"hits": 3, "window": 5}
}
```

* You can maintain multiple plans (5.8 GHz default; optionally 2.4/1.2 GHz sets).

---

## Scanner Algorithm (MVP)

* **Tune** HackRF to `f_center` with `sr=8e6`, `rf_bw≈8e6`.
* **Capture** `N = sr * dwell_ms/1000` complex samples.
* **Compute** PSD (Welch, 1024‑point FFT, Hann) and integrate bins over in‑band region.
* **Noise estimate**: median of edge bins (±(3.5–4) MHz from center) → `noise_dbm`.
* **Score**: `snr_db = (band_power_dbm - noise_dbm)`.
* **Candidate** if `snr_db ≥ min_snr_db`.
* **Debounce** with N‑of‑M across repeated sweeps to avoid flicker.
* **Optional quick signature** (cheap):

  * Run **envelope** of IQ (|x|) and check if baseband energy shows strong low‑frequency content and faint \~line‑rate (\~15.6 kHz PAL / \~15.734 kHz NTSC) spikes in a short FFT → boosts confidence.

---

## Candidate Model (SQLite)

```sql
CREATE TABLE candidates (
  freq_hz INTEGER PRIMARY KEY,
  power_dbm REAL,
  snr_db REAL,
  ema_power REAL,
  ema_snr REAL,
  first_seen TIMESTAMP,
  last_seen TIMESTAMP,
  hits INTEGER,
  status TEXT CHECK(status IN ('new','active','lost'))
);
```

**API response** (top N):

```json
[
  {"freq_hz": 5806000000, "snr_db": 18.2, "power_dbm": -42.1, "last_seen": "2025-08-17T15:23:11Z", "status": "active"},
  {"freq_hz": 5769000000, "snr_db": 12.7, "power_dbm": -49.5, "last_seen": "2025-08-17T15:23:10Z", "status": "active"}
]
```

---

## Focused Demod (Analog Preview)

**GNU Radio chain (conceptual):**

```
HackRF Source (8 MS/s) → DC Block → AGC → Lowpass (~4 MHz) → Envelope Det.
→ De‑emphasis/Clamp → Resampler to ~6–8 MHz pix clock → Frame rate gate (25/30 fps)
→ Gray scale image frames (8‑bit) → ZMQ PUB (topic=frames)
```

* **Preview**: Python subscriber converts frames to JPEGs and serves MJPEG stream.
* **Audio (optional)**: band‑pass around expected subcarrier (e.g., \~6.0–6.5 MHz), FM/AM demod → ALSA.
* **Recording**: IQ sink to file + optional encoded MP4 of frames via `ffmpeg`.

> Note: Analog FPV modulation variants exist; MVP sticks to envelope‑style demod sufficient for many analog VTX feeds. Digital systems are surfaced as “wideband activity”; no decode.

---

## REST/WS API (FastAPI)

* `GET /candidates?limit=10` → list as above (sorted by `ema_snr DESC`).
* `POST /scan/start` `{ "plan":"58g_default" }` → 200.
* `POST /scan/stop` → 200.
* `GET /scan/status` → `{ state:"running", sweep_ms:1200, last_pass:"..." }`.
* `POST /focus` `{ "freq_hz": 5806000000 }` → `{ "video_url":"/video.mjpeg" }`.
* `DELETE /focus` → stops demod.
* `POST /record` `{ "type":"iq|video", "enable":true }` → `{ "path":"/data/session_..." }`.
* `GET /config` / `PUT /config` (upload new `channels.json`).
* `WS /events` → candidate/alert push (JSON events): `{type:"alert", freq_hz, snr_db}`.

---

## Minimal UI (HTML served by FastAPI)

* **Page**: `/` →

  * Table: `#`, `Freq (MHz)`, `SNR (dB)`, `Power (dBm)`, `Last Seen`, `Action` (Focus/Stop).
  * When focused: show `<img src="/video.mjpeg">` under the table.
  * Settings accordion: upload channel plan JSON; toggles for record IQ/video.

---

## Deployment & Services

**Packages** (apt + pip):

* `hackrf`, `gnuradio`, `gr‑osmo`, `python3‑fastapi`, `uvicorn`, `python3‑pyzmq`, `ffmpeg`, `python3‑numpy`, `python3‑scipy`, `python3‑opencv`.

**systemd units** (examples):

* `vtx-scanner.service` → runs `python -m app.scanner`.
* `vtx-api.service` → runs `uvicorn app.api:app --host 0.0.0.0 --port 8080`.
* `vtx-demod@.service` → templated, one per focused session.
* `vtx-video-bridge.service` → serves MJPEG from ZMQ.

**Directory layout**

```
/app
  /config/channels.json
  /app/scanner.py
  /app/api.py
  /app/demod_flowgraph.grc (and generated .py)
  /app/video_bridge.py
  /app/storage.db (sqlite)
  /logs/*.jsonl
```

---

## Installation (quick path)

1. Raspberry Pi OS 64‑bit, update/upgrade.
2. `sudo apt install hackrf gnuradio gnuradio-dev gr-osmosdr ffmpeg python3-pip python3-numpy python3-scipy python3-opencv python3-zmq`.
3. `pip install fastapi uvicorn pydantic`.
4. `hackrf_info` to verify device; `hackrf_transfer -r /dev/null -s 8000000 -f 5806000000` sanity test.
5. Place app folder, set `channels.json`, enable `systemd` units, reboot.

---

## Tuning & Defaults

* **Sample rate**: 8 MS/s scanning & focus (raise to 10 MS/s if CPU allows).
* **Dwell**: 15 ms/channel (start), 10 ms if stable.
* **Alert**: `min_snr_db=6`, `hits≥3 of last 5`.
* **Top N**: 10.
* **Recording**: off by default; ring buffer length 60 s IQ when on.

---

## Limitations (MVP)

* Analog demod quality depends on local conditions, front‑end filtering, and exact VTX modulation; results may be noisy/unstable without proper RF chain.
* Digital VTX systems are **not** decoded; only detected as activity.
* Single HackRF → **no** simultaneous full‑band scan and high‑quality demod; focus suspends scanning or reduces its duty cycle.

---

## Roadmap Ideas (post‑MVP)

* **Dual‑device** mode (second SDR for continuous scanning while one demods).
* **Adaptive dwell**: prioritize bins with recent activity.
* **Classifier**: tiny CNN/RF features on baseband to separate analog/digital quickly.
* **Geiger mode** alerting: haptic/beep on strong discovery.
* **WebRTC** low‑latency video instead of MJPEG.
* **Headless CLI** for scripting + HTTP hooks.
* **Auto‑band selection** using power survey.

---

## Acceptance Criteria

* Device boots → services up via systemd.
* Visiting `/` shows empty table; starting scan populates candidates within 2 sweeps.
* Focusing on a strong analog VTX shows MJPEG preview (even noisy) within \~2 s.
* Stopping focus returns to full scanning.
* IQ recording toggles and produces files.

---

## Test Plan

* RF sanity: inject known analog signal (signal generator or VTX on bench) at 5806 MHz.
* Verify candidate appears with SNR ≥ threshold and persists.
* Focus → preview shows moving test pattern.
* Record IQ → replay with GNU Radio/inspectrum.

---

## Notes on Safety & Legality

* Receive‑only; ensure TX is disabled in libhackrf (or leave TX hardware unpowered).
* Follow local spectrum regulations. Use bandpass filtering to minimize out‑of‑band pickup.

---

## Nuxt 3 UI Integration (MVP)

**Why Nuxt:** you already run a kiosk; Nuxt 3 gives a clean SPA/SSR with minimal code and easy proxying to the FastAPI backend.

**Directory sketch**

```
/frontend
  /components
    CandidateList.vue
    FocusViewer.vue
    SettingsPane.vue
  /composables
    useApi.ts        # REST calls to FastAPI
    useEvents.ts     # SSE subscription for candidate alerts
  /pages/index.vue   # single-page UI (table + focus panel)
  /assets/styles.css
  nuxt.config.ts
```

**UI Behavior**

* Landing page lists top candidates from `GET /candidates` (poll every 1–2 s **or** subscribe via SSE to `/events`).
* “Focus” button calls `POST /focus { freq_hz }` and reveals `<img :src="/video.mjpeg">`.
* Settings pane: upload/replace channel plan JSON, toggle IQ/video recording.

**Proxy (Nitro routeRules) – simplest local setup**

```ts
// nuxt.config.ts
export default defineNuxtConfig({
  nitro: {
    routeRules: {
      '/api/**': { proxy: 'http://127.0.0.1:8080/**' },
      '/events': { proxy: 'http://127.0.0.1:8080/events' },   // SSE
      '/video.mjpeg': { proxy: 'http://127.0.0.1:8080/video.mjpeg' }
    }
  }
})
```

> If you prefer Nginx/Caddy, serve Nuxt at `/` and reverse‑proxy `/api`, `/events`, `/video.mjpeg` to FastAPI.

**SSE composable**

```ts
// /composables/useEvents.ts
export function useEvents(onEvent: (e: any) => void) {
  const evt = new EventSource('/events');
  evt.onmessage = (m) => onEvent(JSON.parse(m.data));
  return () => evt.close();
}
```

**Minimal index page**

```vue
<script setup lang="ts">
const { data: list, refresh } = await useFetch('/api/candidates?limit=10');
const videoUrl = ref<string | null>(null);
function focus(freq_hz:number){
  $fetch('/api/focus',{method:'POST', body:{freq_hz}}).then((r:any)=>{ videoUrl.value = r.video_url })
}
useEvents((e)=>{ if(e.type==='alert') refresh() });
</script>
<template>
  <main class="p-4 grid gap-4">
    <CandidateList :items="list || []" @focus="focus" />
    <FocusViewer v-if="videoUrl" :src="videoUrl" />
    <SettingsPane />
  </main>
</template>
```

**Focus viewer**

```vue
<template>
  <section class="rounded-xl shadow p-2">
    <img :src="src" alt="VTX preview" class="w-full" />
  </section>
</template>
<script setup lang="ts">defineProps<{src:string}>()</script>
```

---

## Scanner DC Mitigation (added)

HackRF exhibits a baseband **DC spike/LO leakage**. To keep candidate scoring robust, the Scanner applies **three layers**:

1. **Mean subtraction** per capture window: `x = x - mean(x)`.
2. **DC notch in PSD integration**: exclude a ±guard around 0 Hz when summing in‑band power.
3. **Optional IIR DC blocker** (very low‑cut high‑pass) or **LO dither** (±100–300 kHz) between sweeps to avoid fixed‑bin bias.

**MVP band‑power routine (Python)**

```python
import numpy as np
from numpy.fft import rfft

def band_metrics(iq: np.ndarray, sr: float, bw_hz: float, guard_hz: float = 50e3):
    # 1) mean removal
    iq = iq - np.mean(iq)
    # 2) window + FFT power
    n = len(iq)
    win = np.hanning(n)
    X = rfft(iq * win)
    psd = (np.abs(X)**2) / (np.sum(win**2))
    freqs = np.fft.rfftfreq(n, d=1/sr)
    # in-band mask (±bw/2)
    half_bw = bw_hz/2
    m_band = (np.abs(freqs) <= half_bw)
    # DC guard mask
    m_guard = (np.abs(freqs) < guard_hz)
    m_use = m_band & (~m_guard)
    band_power = 10*np.log10(np.sum(psd[m_use]) + 1e-20)
    # noise from edges just outside band
    edge_lo = (freqs > half_bw) & (freqs <= half_bw*1.25)
    edge_hi = (freqs < -half_bw) & (freqs >= -half_bw*1.25)  # not used with rfft; kept for clarity
    noise_bins = psd[edge_lo]
    noise_dbm = 10*np.log10(np.median(noise_bins) * max(1, len(noise_bins)) + 1e-20)
    snr_db = band_power - noise_dbm
    return band_power, snr_db
```

**Notes**

* For **8 MS/s** and **NFFT=len(iq)=8192**, bin ≈ **976 Hz**. A **±50 kHz** DC guard ≈ **±51 bins**—ample to null the spike.
* If you center exactly at the channel, DC guard removes negligible in‑band energy.
* If residual bias remains, enable **LO dither**: per channel, tune `f_center + δ` (e.g., +200 kHz) and account for it when defining the in‑band window.

**Scanner defaults updated**

* `dc_guard_hz: 50e3`
* `dc_blocker: false` (off by default; mean removal + notch suffice)
* `lo_dither_hz: 0` (set to 200e3 if you observe bias)

**Impact**

* Candidate SNR becomes stable vs. temperature/gain changes.
* False positives at exactly‑center bins are suppressed.

---

## Service Wiring (Nuxt + FastAPI on Pi)

* **Ports**: Nuxt on `:3000` (dev) or `:8081` (prod Nitro); FastAPI on `:8080`.
* **systemd**

```
[Unit]
Description=Nuxt Kiosk
After=network.target
[Service]
WorkingDirectory=/opt/vtx/frontend
ExecStart=/usr/bin/node .output/server/index.mjs
Restart=always
[Install]
WantedBy=multi-user.target
```

* Kiosk Chromium points to `http://localhost:8081`.

---

## Next Steps (Nuxt path)

1. Scaffold Nuxt 3 app with the files above.
2. Add routeRules proxy; verify `/api/health` passthrough.
3. Implement CandidateList + SSE refresh.
4. Wire Focus viewer to `/video.mjpeg`.
5. Smoke test on bench VTX; tune `dc_guard_hz` if needed.

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from dataclasses import dataclass, field
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

import orjson
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from pydantic import BaseModel
from .storage import init_db, list_top_candidates, upsert_candidate
from .video_bridge import mjpeg_stream as video_mjpeg_stream
from .scanner import Scanner
from .hw_capture import SoapyHackRFSampler
import subprocess
import sys

# In-memory mock state for MVP scaffolding


DATA_DIR = Path(os.environ.get("RER_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


class Candidate(BaseModel):
    freq_hz: int
    snr_db: float
    power_dbm: float
    last_seen: str
    status: str


class ScanConfig(BaseModel):
    plan: str = "58g_default"


class FocusRequest(BaseModel):
    freq_hz: int


class RecordToggle(BaseModel):
    type: str
    enable: bool


@dataclass
class AppState:
    scanning: bool = False
    focused_freq_hz: Optional[int] = None
    candidates: Dict[int, Candidate] = field(default_factory=dict)
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    subscribers: Set[asyncio.Queue[bytes]] = field(default_factory=set)
    scanner_task: Optional[asyncio.Task] = None
    demod_proc: Optional[subprocess.Popen] = None
    was_scanning_before_focus: bool = False
    config_path: Path = Path(__file__).resolve().parent / "config" / "channels.json"
    config: Dict[str, Any] = field(default_factory=dict)


state = AppState()

app = FastAPI(title="RER-Kilo API", version="0.1.0")
@app.on_event("startup")
async def _on_startup() -> None:
    # Ensure DB schema exists and preload config
    try:
        init_db()
    except Exception:
        pass
    try:
        cfg = await _load_config(state.config_path)
        state.config = cfg
    except Exception:
        pass



@app.get("/health")
async def health() -> Dict[str, str]:
    # Attempt to clear any dangling HackRF streams on startup/health check
    try:
        sampler = SoapyHackRFSampler()
        sampler.clear()
    except Exception:
        pass
    return {"status": "ok"}


@app.get("/candidates")
async def get_candidates(limit: int = 10) -> List[Candidate]:
    try:
        rows = list_top_candidates(limit)
    except sqlite3.OperationalError:
        # Initialize DB on demand if table is missing
        init_db()
        rows = []
    return [Candidate(
        freq_hz=int(r["freq_hz"]),
        snr_db=float(r["snr_db"]),
        power_dbm=float(r["power_dbm"]),
        last_seen=str(r["last_seen"]),
        status=str(r["status"]),
    ) for r in rows]


@app.post("/scan/start")
async def scan_start(cfg: ScanConfig) -> Dict[str, Any]:
    if state.scanning:
        return {"state": "running"}
    # Ensure DB exists
    init_db()
    state.scanning = True
    state.stop_event.clear()
    if state.scanner_task is None or state.scanner_task.done():
        state.scanner_task = asyncio.create_task(_scanner_loop(state))
    await _broadcast({"type": "scan_state", "state": "running"})
    return {"state": "running", "plan": cfg.plan}


@app.post("/scan/stop")
async def scan_stop() -> Dict[str, str]:
    state.scanning = False
    state.stop_event.set()
    await _broadcast({"type": "scan_state", "state": "stopped"})
    return {"state": "stopped"}


@app.get("/scan/status")
async def scan_status() -> Dict[str, Any]:
    return {
        "state": "running" if state.scanning else "stopped",
        "focused_freq_hz": state.focused_freq_hz,
        "last_update": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/focus")
async def focus(req: FocusRequest) -> Dict[str, str]:
    # Pause scanning during focus for MVP
    state.was_scanning_before_focus = state.scanning
    state.scanning = False
    state.stop_event.set()
    state.focused_freq_hz = req.freq_hz
    await _broadcast({"type": "scan_state", "state": "stopped"})
    await _broadcast({"type": "focus_state", "focused": True, "freq_hz": req.freq_hz})
    # Start mock demod publisher (replace with GNU Radio launcher on Pi)
    await _start_demod(req.freq_hz)
    return {"video_url": "/video.mjpeg"}


@app.delete("/focus")
async def focus_stop() -> Dict[str, str]:
    state.focused_freq_hz = None
    await _broadcast({"type": "focus_state", "focused": False})
    await _stop_demod()
    # Auto-resume scanning if it was running before focus
    if state.was_scanning_before_focus and not state.scanning:
        state.was_scanning_before_focus = False
        state.stop_event.clear()
        state.scanning = True
        if state.scanner_task is None or state.scanner_task.done():
            state.scanner_task = asyncio.create_task(_scanner_loop(state))
        await _broadcast({"type": "scan_state", "state": "running"})
    return {"status": "stopped"}


# Fallback for clients that prefer POST over DELETE
@app.post("/focus/stop")
async def focus_stop_post() -> Dict[str, str]:
    return await focus_stop()


@app.post("/record")
async def record_toggle(toggle: RecordToggle) -> Dict[str, Any]:
    # Placeholder; wire to demod/recorder later
    return {"ok": True, "type": toggle.type, "enable": toggle.enable}


@app.get("/events")
async def events(request: Request) -> StreamingResponse:
    async def event_stream() -> AsyncGenerator[bytes, None]:
        q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        state.subscribers.add(q)
        # Send initial state snapshot
        init_scan = {"type": "scan_state", "state": "running" if state.scanning else "stopped"}
        yield f"data: {json.dumps(init_scan)}\n\n".encode()
        init_focus = {"type": "focus_state", "focused": state.focused_freq_hz is not None, "freq_hz": state.focused_freq_hz}
        yield f"data: {json.dumps(init_focus)}\n\n".encode()
        # Initial candidates snapshot
        rows = list_top_candidates(10)
        if rows:
            init_candidates = {"type": "candidates", "items": rows}
            yield f"data: {json.dumps(init_candidates)}\n\n".encode()
        try:
            last_heartbeat = 0.0
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=1.0)
                    yield item
                except asyncio.TimeoutError:
                    now = time.time()
                    if now - last_heartbeat >= 1.0:
                        payload = {"type": "heartbeat", "ts": now}
                        yield f"data: {json.dumps(payload)}\n\n".encode()
                        last_heartbeat = now
        finally:
            state.subscribers.discard(q)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/")
async def root_page() -> HTMLResponse:
    # Minimal placeholder page indicating that Nuxt frontend will live at / in production.
    html = """
<!doctype html>
<html>
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>RER-Kilo API</title>
    <style>body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,\"Helvetica Neue\",Arial;max-width:720px;margin:2rem auto;padding:0 1rem}</style>
  </head>
  <body>
    <h1>RER-Kilo API</h1>
    <p>Backend is running. Nuxt UI will consume these endpoints:</p>
    <ul>
      <li>GET /candidates</li>
      <li>POST /scan/start, /scan/stop, GET /scan/status</li>
      <li>POST /focus, DELETE /focus</li>
      <li>GET /events (SSE)</li>
      <li>GET /video.mjpeg</li>
    </ul>
  </body>
</html>
"""
    return HTMLResponse(html)


@app.get("/video.mjpeg")
async def video_mjpeg() -> StreamingResponse:
    boundary = "frame"
    headers = {"Content-Type": f"multipart/x-mixed-replace; boundary={boundary}"}
    return StreamingResponse(video_mjpeg_stream(boundary), headers=headers)


async def _scanner_loop(app_state: AppState) -> None:
    # Load frequencies and parameters from config, fallback to defaults
    cfg = app_state.config or await _load_config(app_state.config_path)
    bands = cfg.get("bands") or []
    freqs: List[int] = []
    for b in bands:
        freqs.extend([int(x) for x in b.get("channels", [])])
    if not freqs:
        freqs = [5658000000, 5695000000, 5732000000, 5769000000, 5806000000, 5843000000, 5880000000, 5917000000]
    dwell_ms = int(cfg.get("dwell_ms", 15))
    sample_rate = float(cfg.get("sample_rate", 8_000_000))
    channel_bw_hz = float(cfg.get("channel_bw_hz", 8_000_000))
    min_snr_db = float(cfg.get("min_snr_db", 6))
    alert = cfg.get("alert_persistence", {"hits": 3, "window": 5})
    alert_hits = int(alert.get("hits", 3))
    alert_window = int(alert.get("window", 5))
    scanner = Scanner(
        freqs_hz=freqs,
        sample_rate_hz=sample_rate,
        dwell_ms=dwell_ms,
        channel_bw_hz=channel_bw_hz,
        min_snr_db=min_snr_db,
        alert_hits=alert_hits,
        alert_window=alert_window,
        ema_alpha=0.1,
        broadcast=_broadcast,
    )
    await scanner.run(app_state.stop_event)


@app.get("/config")
async def get_config() -> Dict[str, Any]:
    cfg = await _load_config(state.config_path)
    state.config = cfg
    return cfg


@app.put("/config")
async def put_config(request: Request) -> Dict[str, Any]:
    try:
        body = await request.body()
        cfg = json.loads(body.decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
    # Basic validation
    if not isinstance(cfg, dict) or "bands" not in cfg:
        raise HTTPException(status_code=400, detail="Config must contain 'bands'")
    state.config = cfg
    state.config_path.parent.mkdir(parents=True, exist_ok=True)
    state.config_path.write_text(json.dumps(cfg, indent=2))
    return {"ok": True}


async def _load_config(path: Path) -> Dict[str, Any]:
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    # Fallback minimal config
    return {
        "bands": [
            {"name": "Raceband", "channels": [5658000000, 5695000000, 5732000000, 5769000000, 5806000000, 5843000000, 5880000000, 5917000000]}
        ],
        "dwell_ms": 15,
        "sample_rate": 8000000,
        "channel_bw_hz": 8000000,
        "min_snr_db": 6,
        "alert_persistence": {"hits": 3, "window": 5},
        "dc_guard_hz": 50000,
    }


async def _broadcast(event: Dict[str, Any]) -> None:
    if not state.subscribers:
        return
    data = f"data: {json.dumps(event)}\n\n".encode()
    dead: List[asyncio.Queue[bytes]] = []
    for q in list(state.subscribers):
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        state.subscribers.discard(q)


async def _start_demod(freq_hz: int) -> None:
    # If already running, stop first
    await _stop_demod()
    try:
        env = os.environ.copy()
        endpoint = env.get("RER_FRAMES_ZMQ", "tcp://127.0.0.1:5556")
        topic = env.get("RER_FRAMES_TOPIC", "frames")
        # Prefer auto-tuning demod; fall back to line-timed, then simple envelope, then mock
        try:
            sr = os.environ.get("RER_FOCUS_SAMPLE_RATE", "12000000")
            w = os.environ.get("RER_FOCUS_WIDTH", "320")
            h = os.environ.get("RER_FOCUS_HEIGHT", "180")
            auto_args = [
                sys.executable, "-m", "app.demod_autotune", "--freq", str(freq_hz), "--sample-rate", sr,
                "--endpoint", endpoint, "--topic", topic, "--fps", "10", "--width", w, "--height", h
            ]
            if os.environ.get("RER_FOCUS_NTSC", "0") == "1":
                auto_args.append("--ntsc")
            state.demod_proc = subprocess.Popen(auto_args)
        except Exception:
            try:
                line_args = [
                    sys.executable, "-m", "app.demod_lines", "--freq", str(freq_hz), "--sample-rate", sr,
                    "--endpoint", endpoint, "--topic", topic, "--fps", "10", "--width", w, "--height", h
                ]
                if os.environ.get("RER_FOCUS_NTSC", "0") == "1":
                    line_args.append("--ntsc")
                state.demod_proc = subprocess.Popen(line_args)
            except Exception:
                try:
                    state.demod_proc = subprocess.Popen([
                        sys.executable, "-m", "app.demod_analog", "--freq", str(freq_hz), "--sample-rate", sr,
                        "--endpoint", endpoint, "--topic", topic, "--fps", "10", "--width", w, "--height", h
                    ])
                except Exception:
                    state.demod_proc = subprocess.Popen([
                        sys.executable, "-m", "app.demod_mock", "--freq", str(freq_hz), "--endpoint", endpoint, "--topic", topic, "--fps", "10"
                    ])
    except Exception:
        state.demod_proc = None


async def _stop_demod() -> None:
    if state.demod_proc is None:
        return
    try:
        state.demod_proc.terminate()
        try:
            state.demod_proc.wait(timeout=3)
        except Exception:
            state.demod_proc.kill()
    finally:
        state.demod_proc = None

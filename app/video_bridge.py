"""Video bridge for MJPEG streaming.

Provides an async generator that yields MJPEG multipart chunks suitable for
FastAPI StreamingResponse. It prefers consuming grayscale frames from a ZMQ
publisher (e.g., GNU Radio flowgraph), and falls back to a synthetic generator
if no frames arrive.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import AsyncGenerator, Optional

try:
    import zmq
    import zmq.asyncio
except Exception:  # pragma: no cover
    zmq = None  # type: ignore


ZMQ_ENDPOINT = os.environ.get("RER_FRAMES_ZMQ", "tcp://127.0.0.1:5556")
ZMQ_TOPIC = os.environ.get("RER_FRAMES_TOPIC", "frames").encode()


async def _create_zmq_stream(timeout_first_frame_s: float = 1.0):
    """Return an async generator for ZMQ frames, or None if unavailable/timed out.

    Important: We synchronously wait for the first frame here so that callers can
    decide to fall back if no producer is active, instead of returning a generator
    that might never yield.
    """
    if zmq is None:
        return None
    ctx = zmq.asyncio.Context.instance()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.SUBSCRIBE, ZMQ_TOPIC)
    sub.connect(ZMQ_ENDPOINT)

    # Try to receive the first frame within the timeout
    first_payload = None
    first_meta = None
    first_deadline = time.time() + timeout_first_frame_s
    try:
        while True:
            poller = zmq.asyncio.Poller()
            poller.register(sub, zmq.POLLIN)
            remaining = max(0.0, first_deadline - time.time())
            events = dict(await poller.poll(timeout=int(remaining * 1000)))
            if sub in events and events[sub] & zmq.POLLIN:
                parts = await sub.recv_multipart()
                if len(parts) >= 3:
                    _, meta_raw, payload = parts[0], parts[1], parts[2]
                    try:
                        meta = json.loads(meta_raw.decode("utf-8"))
                        width = int(meta.get("width", 320))
                        height = int(meta.get("height", 240))
                        fmt = str(meta.get("format", "gray8"))
                        first_payload = payload
                        first_meta = (width, height, fmt)
                        break
                    except Exception:
                        continue
            if time.time() >= first_deadline:
                # Timed out waiting for the first frame
                try:
                    sub.close(0)
                except Exception:
                    pass
                return None

        width, height, fmt = first_meta  # type: ignore

        async def gen():
            try:
                # Yield the first frame that we already received
                yield (first_payload, width, height, fmt)  # type: ignore
                # Then continue yielding subsequent frames
                while True:
                    parts = await sub.recv_multipart()
                    if len(parts) < 3:
                        continue
                    _, meta_raw, payload = parts[0], parts[1], parts[2]
                    try:
                        meta = json.loads(meta_raw.decode("utf-8"))
                        w = int(meta.get("width", 320))
                        h = int(meta.get("height", 240))
                        f = str(meta.get("format", "gray8"))
                        yield (payload, w, h, f)
                    except Exception:
                        continue
            finally:
                try:
                    sub.close(0)
                except Exception:
                    pass

        return gen()
    except Exception:
        try:
            sub.close(0)
        except Exception:
            pass
        return None


def _encode_jpeg_from_frame(payload: bytes, width: int, height: int, fmt: str) -> Optional[bytes]:
    import numpy as np  # lazy import to avoid startup failures if numpy unavailable
    import cv2  # lazy import to avoid startup failures if OpenCV unavailable
    if fmt == "gray8":
        arr = np.frombuffer(payload, dtype=np.uint8)
        if arr.size != width * height:
            return None
        img = arr.reshape((height, width))
        img_bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif fmt == "bgr24":
        arr = np.frombuffer(payload, dtype=np.uint8)
        if arr.size != width * height * 3:
            return None
        img_bgr = arr.reshape((height, width, 3))
    else:
        return None
    ok, buf = cv2.imencode('.jpg', img_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
    if not ok:
        return None
    return buf.tobytes()


async def synthetic_mjpeg_stream(boundary: str = "frame") -> AsyncGenerator[bytes, None]:
    import numpy as np  # lazy import
    import cv2  # lazy import
    t0 = time.time()
    frame_idx = 0
    while True:
        height, width = 240, 320
        img = np.zeros((height, width, 3), dtype=np.uint8)
        x = int(((time.time() - t0) * 40) % width)
        cv2.rectangle(img, (x, 0), (min(x + 20, width - 1), height - 1), (0, 255, 0), -1)
        cv2.putText(img, f"Mock {frame_idx}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        ok, buf = cv2.imencode('.jpg', img, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
        if not ok:
            await asyncio.sleep(0.2)
            continue
        jpg = buf.tobytes()
        chunk = (
            f"--{boundary}\r\n"
            f"Content-Type: image/jpeg\r\n"
            f"Content-Length: {len(jpg)}\r\n\r\n"
        ).encode() + jpg + b"\r\n"
        yield chunk
        frame_idx += 1
        await asyncio.sleep(0.2)


async def mjpeg_stream(boundary: str = "frame") -> AsyncGenerator[bytes, None]:
    stream = await _create_zmq_stream(timeout_first_frame_s=1.0)
    if stream is None:
        async for chunk in synthetic_mjpeg_stream(boundary):
            yield chunk
        return
    # ZMQ-backed loop
    async for payload, width, height, fmt in stream:  # type: ignore
        jpg = _encode_jpeg_from_frame(payload, width, height, fmt)
        if jpg is None:
            continue
        chunk = (
            f"--{boundary}\r\n"
            f"Content-Type: image/jpeg\r\n"
            f"Content-Length: {len(jpg)}\r\n\r\n"
        ).encode() + jpg + b"\r\n"
        yield chunk



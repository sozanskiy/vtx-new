from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


DB_PATH = Path(os.environ.get("RER_DB_PATH", Path(__file__).resolve().parent / "storage.db"))


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_db() -> None:
    conn = _get_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS candidates (
              freq_hz INTEGER PRIMARY KEY,
              power_dbm REAL NOT NULL,
              snr_db REAL NOT NULL,
              ema_power REAL NOT NULL,
              ema_snr REAL NOT NULL,
              first_seen TEXT NOT NULL,
              last_seen TEXT NOT NULL,
              hits INTEGER NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('new','active','lost'))
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def upsert_candidate(
    *,
    freq_hz: int,
    power_dbm: float,
    snr_db: float,
    status: str,
    ema_alpha: float = 0.1,
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT ema_power, ema_snr, hits, first_seen FROM candidates WHERE freq_hz=?", (freq_hz,))
        row = cur.fetchone()
        if row is None:
            ema_power = power_dbm
            ema_snr = snr_db
            first_seen = now_iso
            hits = 1 if snr_db >= 0 else 0
            conn.execute(
                """
                INSERT INTO candidates (freq_hz, power_dbm, snr_db, ema_power, ema_snr, first_seen, last_seen, hits, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (freq_hz, power_dbm, snr_db, ema_power, ema_snr, first_seen, now_iso, hits, status),
            )
        else:
            prev_ema_power = float(row["ema_power"]) if row["ema_power"] is not None else power_dbm
            prev_ema_snr = float(row["ema_snr"]) if row["ema_snr"] is not None else snr_db
            ema_power = (1.0 - ema_alpha) * prev_ema_power + ema_alpha * power_dbm
            ema_snr = (1.0 - ema_alpha) * prev_ema_snr + ema_alpha * snr_db
            first_seen = str(row["first_seen"]) if row["first_seen"] else now_iso
            hits = int(row["hits"] or 0) + (1 if snr_db >= 0 else 0)
            conn.execute(
                """
                UPDATE candidates
                SET power_dbm=?, snr_db=?, ema_power=?, ema_snr=?, last_seen=?, hits=?, status=?
                WHERE freq_hz=?
                """,
                (power_dbm, snr_db, ema_power, ema_snr, now_iso, hits, status, freq_hz),
            )
        conn.commit()
    finally:
        conn.close()


def list_top_candidates(limit: int = 10) -> List[Dict[str, Any]]:
    conn = _get_conn()
    try:
        cur = conn.execute(
            """
            SELECT freq_hz, power_dbm, snr_db, ema_power, ema_snr, first_seen, last_seen, hits, status
            FROM candidates
            ORDER BY ema_snr DESC
            LIMIT ?
            """,
            (int(limit),),
        )
        rows = cur.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


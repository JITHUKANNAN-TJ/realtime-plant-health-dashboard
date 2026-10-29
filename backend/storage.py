"""SQLite storage layer (standard library only)."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    device TEXT NOT NULL,
    temp_c REAL,
    humidity_pct REAL,
    light_mv INTEGER,
    light_pct REAL,
    soil_mv INTEGER,
    soil_pct REAL,
    gps_fix INTEGER DEFAULT 0,
    lat REAL,
    lon REAL,
    rssi INTEGER,
    uptime_s INTEGER,
    quality TEXT DEFAULT 'OK',
    seq INTEGER,
    created_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_readings_device_ts ON readings(device, ts);

CREATE TABLE IF NOT EXISTS rollup_1h (
    bucket_ts INTEGER NOT NULL,
    device TEXT NOT NULL,
    temp_avg REAL, temp_min REAL, temp_max REAL,
    hum_avg REAL,
    soil_avg REAL, soil_min REAL, soil_max REAL,
    light_avg REAL,
    n INTEGER,
    PRIMARY KEY (bucket_ts, device)
);

CREATE TABLE IF NOT EXISTS rollup_1d (
    bucket_ts INTEGER NOT NULL,
    device TEXT NOT NULL,
    temp_avg REAL, temp_min REAL, temp_max REAL,
    hum_avg REAL,
    soil_avg REAL, soil_min REAL, soil_max REAL,
    light_avg REAL,
    n INTEGER,
    PRIMARY KEY (bucket_ts, device)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    device TEXT NOT NULL,
    type TEXT NOT NULL,
    note TEXT,
    amount REAL,
    meta_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_device_ts ON events(device, ts);

CREATE TABLE IF NOT EXISTS alert_state (
    device TEXT NOT NULL,
    rule TEXT NOT NULL,
    state TEXT NOT NULL,
    since_ts INTEGER,
    last_notified_ts INTEGER,
    snooze_until INTEGER,
    PRIMARY KEY (device, rule)
);

CREATE TABLE IF NOT EXISTS app_config (
    key TEXT PRIMARY KEY,
    value_json TEXT
);
"""

RAW_RETENTION_S = 7 * 86400
HOUR_RETENTION_S = 90 * 86400

READING_FIELDS = (
    "ts", "device", "temp_c", "humidity_pct", "light_mv", "light_pct",
    "soil_mv", "soil_pct", "gps_fix", "lat", "lon", "rssi", "uptime_s",
    "quality", "seq", "created_at",
)


class Storage:
    def __init__(self, db_path):
        directory = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(directory, exist_ok=True)
        self.db_path = db_path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    # -- readings ---------------------------------------------------------
    def insert_readings(self, rows):
        if not rows:
            return 0
        placeholders = ",".join("?" for _ in READING_FIELDS)
        sql = f"INSERT INTO readings ({','.join(READING_FIELDS)}) VALUES ({placeholders})"
        values = [tuple(row.get(field) for field in READING_FIELDS) for row in rows]
        with self._lock:
            self.conn.executemany(sql, values)
            self.conn.commit()
        return len(rows)

    def latest(self, device=None):
        sql = "SELECT * FROM readings"
        args = []
        if device:
            sql += " WHERE device = ?"
            args.append(device)
        sql += " ORDER BY ts DESC, id DESC LIMIT 1"
        with self._lock:
            row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def recent(self, device, since_ts, limit=2000):
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM (SELECT * FROM readings WHERE device = ? AND ts >= ? "
                "ORDER BY ts DESC LIMIT ?) ORDER BY ts ASC",
                (device, since_ts, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def history(self, device, range_s, now=None):
        now = now or int(time.time())
        start = now - range_s
        if range_s <= 8 * 86400:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT ts, temp_c AS temp, humidity_pct AS humidity, "
                    "light_pct AS light, soil_pct AS soil, quality "
                    "FROM readings WHERE device = ? AND ts >= ? ORDER BY ts ASC",
                    (device, start),
                ).fetchall()
            return [dict(r) for r in rows]

        table = "rollup_1h" if range_s <= 100 * 86400 else "rollup_1d"
        with self._lock:
            rows = self.conn.execute(
                f"SELECT bucket_ts AS ts, temp_avg AS temp, hum_avg AS humidity, "
                f"light_avg AS light, soil_avg AS soil FROM {table} "
                f"WHERE device = ? AND bucket_ts >= ? ORDER BY bucket_ts ASC",
                (device, start),
            ).fetchall()
        return [dict(r) for r in rows]

    def devices(self):
        with self._lock:
            rows = self.conn.execute(
                "SELECT device, MAX(ts) AS last_ts, COUNT(*) AS n FROM readings GROUP BY device"
            ).fetchall()
        return [dict(r) for r in rows]

    # -- rollups ----------------------------------------------------------
    def aggregate(self, now=None):
        now = now or int(time.time())
        since = now - 2 * 86400
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO rollup_1h "
                "SELECT (ts/3600)*3600, device, AVG(temp_c), MIN(temp_c), MAX(temp_c), "
                "AVG(humidity_pct), AVG(soil_pct), MIN(soil_pct), MAX(soil_pct), "
                "AVG(light_pct), COUNT(*) FROM readings WHERE ts >= ? "
                "GROUP BY (ts/3600)*3600, device",
                (since,),
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO rollup_1d "
                "SELECT (ts/86400)*86400, device, AVG(temp_c), MIN(temp_c), MAX(temp_c), "
                "AVG(humidity_pct), AVG(soil_pct), MIN(soil_pct), MAX(soil_pct), "
                "AVG(light_pct), COUNT(*) FROM readings WHERE ts >= ? "
                "GROUP BY (ts/86400)*86400, device",
                (since,),
            )
            self.conn.commit()

    def prune(self, now=None):
        now = now or int(time.time())
        with self._lock:
            self.conn.execute("DELETE FROM readings WHERE ts < ?", (now - RAW_RETENTION_S,))
            self.conn.execute(
                "DELETE FROM rollup_1h WHERE bucket_ts < ?", (now - HOUR_RETENTION_S,)
            )
            self.conn.commit()

    # -- events -----------------------------------------------------------
    def add_event(self, device, event_type, note="", amount=None, meta=None, ts=None):
        ts = ts or int(time.time())
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO events (ts, device, type, note, amount, meta_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts, device, event_type, note, amount, json.dumps(meta or {})),
            )
            self.conn.commit()
            return cur.lastrowid

    def list_events(self, device=None, since_ts=None, limit=500):
        sql = "SELECT * FROM events"
        clauses, args = [], []
        if device:
            clauses.append("device = ?")
            args.append(device)
        if since_ts:
            clauses.append("ts >= ?")
            args.append(since_ts)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self.conn.execute(sql, args).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            try:
                item["meta"] = json.loads(item.pop("meta_json") or "{}")
            except ValueError:
                item["meta"] = {}
            out.append(item)
        return out

    # -- alert state ------------------------------------------------------
    def load_alert_states(self, device):
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM alert_state WHERE device = ?", (device,)
            ).fetchall()
        return {r["rule"]: dict(r) for r in rows}

    def save_alert_state(self, device, rule, state, since_ts, last_notified_ts, snooze_until):
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO alert_state "
                "(device, rule, state, since_ts, last_notified_ts, snooze_until) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (device, rule, state, since_ts, last_notified_ts, snooze_until),
            )
            self.conn.commit()

    # -- app config -------------------------------------------------------
    def get_config_value(self, key, default=None):
        with self._lock:
            row = self.conn.execute(
                "SELECT value_json FROM app_config WHERE key = ?", (key,)
            ).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value_json"])
        except ValueError:
            return default

    def set_config_value(self, key, value):
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO app_config (key, value_json) VALUES (?, ?)",
                (key, json.dumps(value)),
            )
            self.conn.commit()

    def close(self):
        with self._lock:
            self.conn.close()

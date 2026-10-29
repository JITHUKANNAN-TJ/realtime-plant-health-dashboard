"""Synthetic data generator for running the dashboard without hardware."""

from __future__ import annotations

import math
import random
import threading
import time


class DemoGenerator(threading.Thread):
    def __init__(self, ingest, device="plant-01", interval=1.0, seed=7):
        super().__init__(daemon=True)
        self.ingest = ingest
        self.device = device
        self.interval = interval
        self._stop = threading.Event()
        self.random = random.Random(seed)
        self.soil = 62.0
        self.seq = 0

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.is_set():
            reading = self._sample()
            try:
                self.ingest([reading])
            except Exception:
                pass
            self._stop.wait(self.interval)

    def _sample(self):
        now = time.time()
        local = time.localtime(now)
        hour = local.tm_hour + local.tm_min / 60.0

        temp = 24.0 + 2.6 * math.sin((hour - 14.0) / 24.0 * 2 * math.pi)
        humidity = 58.0 - 9.0 * math.sin((hour - 14.0) / 24.0 * 2 * math.pi)
        daylight = max(0.0, math.sin((hour - 6.0) / 12.0 * math.pi))
        light = min(95.0, daylight * 90.0)

        temp += self.random.uniform(-0.3, 0.3)
        humidity += self.random.uniform(-1.2, 1.2)
        light = max(0.0, light + self.random.uniform(-3, 3))

        self.soil -= 0.06 + self.random.uniform(0, 0.02)
        if self.soil < 32.0:
            self.soil = 63.0

        self.seq += 1
        return {
            "device": self.device,
            "seq": self.seq,
            "ts": int(now),
            "fw": "demo",
            "temp_c": round(temp, 1),
            "humidity_pct": round(humidity, 1),
            "light_mv": int(2500 - light * 24),
            "light_pct": round(light, 1),
            "soil_mv": int(1200 + (100 - self.soil) * 14),
            "soil_pct": round(self.soil, 1),
            "gps": {"fix": False, "lat": None, "lon": None, "sats": 0},
            "rssi": -55,
            "uptime_s": self.seq,
        }

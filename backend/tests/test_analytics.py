import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import (  # noqa: E402
    anomaly,
    health_score,
    linear_fit,
    quality,
    watering_forecast,
)

THRESHOLDS = {
    "temp": {"ideal": [18, 28], "warn": [15, 32]},
    "humidity": {"ideal": [40, 70], "warn": [30, 80]},
    "soil": {"ideal": [30, 70], "warn": [20, 85], "refill": 35},
    "light": {"ideal": [20, 80], "warn": [10, 92]},
}


class HealthScoreTests(unittest.TestCase):
    def test_ideal_reading_scores_100(self):
        reading = {"temp_c": 23, "humidity_pct": 55, "soil_pct": 50, "light_pct": 50}
        result = health_score(reading, THRESHOLDS)
        self.assertEqual(result["score"], 100.0)
        self.assertIsNone(result["top_issue"])

    def test_dry_soil_is_top_issue(self):
        reading = {"temp_c": 23, "humidity_pct": 55, "soil_pct": 10, "light_pct": 50}
        result = health_score(reading, THRESHOLDS)
        self.assertLess(result["score"], 100)
        self.assertEqual(result["top_issue"]["metric"], "soil")
        self.assertEqual(result["top_issue"]["direction"], "low")

    def test_missing_values_do_not_crash(self):
        result = health_score({}, THRESHOLDS)
        self.assertEqual(result["score"], 100.0)


class QualityTests(unittest.TestCase):
    def test_ok(self):
        now = int(time.time())
        reading = {"ts": now, "temp_c": 23, "humidity_pct": 50,
                   "soil_pct": 50, "light_pct": 50}
        self.assertEqual(quality(reading, [], 3, now), "OK")

    def test_out_of_range(self):
        now = int(time.time())
        reading = {"ts": now, "temp_c": 200, "humidity_pct": 50,
                   "soil_pct": 50, "light_pct": 50}
        self.assertEqual(quality(reading, [], 3, now), "OUT_OF_RANGE")

    def test_flatline(self):
        now = int(time.time())
        recent = [{"soil_mv": 2000, "light_mv": 1000} for _ in range(5)]
        reading = {"ts": now, "temp_c": 23, "humidity_pct": 50,
                   "soil_pct": 50, "light_pct": 50, "soil_mv": 2000, "light_mv": 1000}
        self.assertEqual(quality(reading, recent, 3, now), "FLATLINE")


class ForecastTests(unittest.TestCase):
    def test_linear_fit(self):
        fit = linear_fit([0, 1, 2, 3], [1, 3, 5, 7])
        self.assertAlmostEqual(fit[0], 2.0)
        self.assertAlmostEqual(fit[1], 1.0)
        self.assertAlmostEqual(fit[2], 1.0)

    def test_due_now(self):
        now = int(time.time())
        rows = [{"ts": now - 3600, "soil_pct": 40}, {"ts": now, "soil_pct": 30}]
        result = watering_forecast(rows, THRESHOLDS, now=now)
        self.assertEqual(result["status"], "due")

    def test_stable(self):
        now = int(time.time())
        rows = [{"ts": now - i * 360, "soil_pct": 60.0} for i in range(20)]
        result = watering_forecast(rows, THRESHOLDS, now=now)
        self.assertEqual(result["status"], "stable")

    def test_declining(self):
        now = int(time.time())
        rows = [{"ts": now - (19 - i) * 600, "soil_pct": 60 - i * 1.0}
                for i in range(20)]
        result = watering_forecast(rows, THRESHOLDS, now=now)
        self.assertEqual(result["status"], "watering_soon")
        self.assertGreater(result["hours"], 0)


class AnomalyTests(unittest.TestCase):
    def test_no_baseline_no_flags(self):
        self.assertEqual(anomaly({"temp_c": 50}, {"temp": []}), [])

    def test_spike_flagged(self):
        baseline = {"temp": [24, 24.1, 23.9, 24.2, 24.0, 23.8, 24.3, 24.1,
                             23.9, 24.0, 24.2, 23.8, 24.1]}
        flags = anomaly({"temp_c": 40}, baseline)
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]["metric"], "temp")


if __name__ == "__main__":
    unittest.main()

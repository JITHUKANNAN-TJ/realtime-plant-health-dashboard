import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alerts import AlertEngine  # noqa: E402

THRESHOLDS = {
    "temp": {"ideal": [18, 28], "warn": [15, 32], "warn_margin": 1.0, "crit_margin": 3.0},
    "humidity": {"ideal": [40, 70], "warn": [30, 80], "warn_margin": 3.0, "crit_margin": 10.0},
    "soil": {"ideal": [30, 70], "warn": [20, 85], "warn_margin": 3.0, "crit_margin": 8.0},
    "light": {"ideal": [20, 80], "warn": [10, 92], "warn_margin": 3.0, "crit_margin": 8.0},
}


def engine(**settings):
    base = {"cooldown_s": 600, "escalate_after_s": 900}
    base.update(settings)
    return AlertEngine(THRESHOLDS, base)


class AlertEngineTests(unittest.TestCase):
    def test_warn_on_low_soil(self):
        result = engine().evaluate({"soil_pct": 15}, now=1000)
        self.assertEqual(len(result["alerts"]), 1)
        self.assertEqual(result["alerts"][0]["severity"], "WARN")
        self.assertFalse(result["critical"])

    def test_critical_on_very_low_soil(self):
        result = engine().evaluate({"soil_pct": 5}, now=1000)
        self.assertEqual(result["alerts"][0]["severity"], "CRIT")
        self.assertTrue(result["critical"])

    def test_recovery_with_hysteresis(self):
        e = engine()
        e.evaluate({"soil_pct": 15}, now=1000)
        recovered = e.evaluate({"soil_pct": 30}, now=1100)
        self.assertEqual(recovered["alerts"], [])
        self.assertEqual(e.active(), {})

    def test_cooldown_prevents_repeat(self):
        e = engine(cooldown_s=600)
        e.evaluate({"soil_pct": 5}, now=1000)
        second = e.evaluate({"soil_pct": 5}, now=1100)
        self.assertEqual(second["alerts"], [])
        third = e.evaluate({"soil_pct": 5}, now=1700)
        self.assertEqual(len(third["alerts"]), 1)

    def test_escalation_after_time(self):
        e = engine(escalate_after_s=100)
        first = e.evaluate({"soil_pct": 15}, now=1000)
        self.assertEqual(first["alerts"][0]["severity"], "WARN")
        later = e.evaluate({"soil_pct": 15}, now=1200)
        self.assertEqual(later["alerts"][0]["severity"], "CRIT")

    def test_snooze_suppresses_notifications(self):
        e = engine()
        e.snooze(60)
        result = e.evaluate({"soil_pct": 5}, now=1000)
        self.assertEqual(result["alerts"], [])
        self.assertTrue(result["critical"])

    def test_low_light_is_ignored(self):
        result = engine().evaluate({"light_pct": 0}, now=1000)
        self.assertEqual(result["alerts"], [])


if __name__ == "__main__":
    unittest.main()

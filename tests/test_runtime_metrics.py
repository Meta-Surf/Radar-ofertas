import tempfile
import unittest
from pathlib import Path

from runtime_metrics import RuntimeMetrics


class RuntimeMetricsTests(unittest.TestCase):
    def test_summary_reports_count_average_p95_and_max(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.sqlite3"
            metrics = RuntimeMetrics(path, now=lambda: 10_000)
            for value in (10, 20, 30, 40, 50):
                metrics.record("monitor", "capture", value)
            summary = metrics.summary(hours=24)[("monitor", "capture")]
            self.assertEqual(summary["count"], 5)
            self.assertEqual(summary["avg_ms"], 30.0)
            self.assertEqual(summary["p95_ms"], 40.0)
            self.assertEqual(summary["max_ms"], 50.0)
            metrics.close()

    def test_prune_removes_old_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.sqlite3"
            clock = [1_000.0]
            metrics = RuntimeMetrics(path, now=lambda: clock[0])
            metrics.record("publisher", "gate", 12)
            clock[0] += 8 * 86400
            self.assertEqual(metrics.prune(days=7), 1)
            self.assertEqual(metrics.summary(), {})
            metrics.close()


if __name__ == "__main__":
    unittest.main()

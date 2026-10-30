from datetime import datetime, timedelta, timezone
import unittest

from admin_gui.charts import MetricHistory


class HistoryTests(unittest.TestCase):
    def test_bounded_distinct_samples_and_pc_isolation(self):
        history = MetricHistory(limit=3)
        now = datetime.now(timezone.utc)
        for index in range(5):
            devices = {key: {"online": True, "metrics": {
                "timestamp": (now + timedelta(seconds=index * 2)).isoformat(),
                "cpu_percent": value, "upload_mbps": None}}
                for key, value in (("a", index), ("b", 90))}
            history.update(devices)
            history.update(devices)
        self.assertEqual(len(history.samples["a"]), 3)
        self.assertEqual([m["cpu_percent"] for _, m in history.samples["a"]], [2, 3, 4])
        self.assertEqual(history.samples["b"][-1][1]["cpu_percent"], 90)
        self.assertIsNone(history.samples["a"][-1][1]["upload_mbps"])
        history.update({"a": {"online": False}})
        self.assertNotIn("b", history.samples)
        self.assertEqual(len(history.samples["a"]), 3)


if __name__ == "__main__":
    unittest.main()

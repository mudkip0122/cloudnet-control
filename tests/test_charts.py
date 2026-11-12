from datetime import datetime, timedelta, timezone
import unittest
import math

from admin_gui.charts import MetricHistory, series


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

    def test_thirty_minutes_of_three_pc_samples_remain_bounded(self):
        history = MetricHistory()
        now = datetime.now(timezone.utc)
        for index in range(901):
            history.update({pc: {"online": True, "metrics": {
                "timestamp": (now + timedelta(seconds=index * 2)).isoformat(),
                "cpu_percent": value, "ram_percent": 50, "upload_mbps": 0, "download_mbps": None}}
                for pc, value in (("PC-01", 10), ("PC-02", 20), ("PC-03", 30))})
        self.assertEqual([len(points) for points in history.samples.values()], [180] * 3)
        self.assertEqual([points[-1][1]["cpu_percent"] for points in history.samples.values()], [10, 20, 30])

    def test_missing_invalid_and_interrupted_data_break_lines(self):
        history = MetricHistory()
        now = datetime.now(timezone.utc)
        def update(seconds, value):
            history.update({"pc": {"online": True, "metrics": {
                "timestamp": (now + timedelta(seconds=seconds)).isoformat(),
                "cpu_percent": value, "upload_mbps": float('inf')}}})
        update(0, 0)
        update(2, None)
        update(4, 20)
        update(12, 30)
        history.disconnect()
        update(14, 40)
        update(16, 101)
        points = history.samples['pc']
        _, values = series(points, 'cpu_percent')
        self.assertEqual(values[0], 0)
        self.assertEqual(sum(math.isnan(value) for value in values), 4)
        self.assertTrue(all(point[1]['upload_mbps'] is None for point in points))

    def test_invalid_timestamp_and_older_samples_are_not_appended(self):
        history = MetricHistory()
        now = datetime.now(timezone.utc)
        for stamp in (now.isoformat(), now.isoformat(), (now - timedelta(seconds=2)).isoformat(),
                      None, 123, 'invalid', '2026-11-12T10:00:00'):
            history.update({'pc': {'online': True, 'metrics': {'timestamp': stamp}}})
        self.assertEqual(len(history.samples['pc']), 1)


if __name__ == "__main__":
    unittest.main()

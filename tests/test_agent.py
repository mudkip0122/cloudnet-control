from collections import namedtuple
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent.collect import network_rates, samples
from agent.identity import resolve_id
from agent.main import retry_delay

Counters = namedtuple("Counters", "bytes_sent bytes_recv")


class IdentityTests(unittest.TestCase):
    def test_id_persists_and_override_does_not_replace_it(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "id.json"
            first = resolve_id(path=path)
            self.assertEqual(first, resolve_id(path=path))
            self.assertEqual(resolve_id("test-pc", path), "test-pc")
            self.assertEqual(first, resolve_id(path=path))
            other = resolve_id(path=Path(folder) / "other.json")
            self.assertNotEqual(first, other)

    def test_invalid_saved_id_is_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "id.json"
            for content in ("broken", json.dumps({"pc_id": ""})):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    resolve_id(path=path)
                self.assertEqual(path.read_text(encoding="utf-8"), content)


class CollectorTests(unittest.TestCase):
    def test_mbps_uses_actual_elapsed_time(self):
        self.assertEqual(network_rates(Counters(1_000_000, 2_000_000), Counters(0, 0), 2), (4, 8))

    def test_first_reset_and_invalid_interval_are_missing(self):
        for previous, current, elapsed in ((None, Counters(1, 1), 2),
                                           (Counters(10, 10), Counters(1, 20), 2),
                                           (Counters(0, 0), Counters(1, 1), 0)):
            self.assertEqual(network_rates(current, previous, elapsed), (None, None))

    def test_collection_failure_recovers_without_stale_values(self):
        with patch("agent.collect.time.sleep"), \
             patch("agent.collect.time.monotonic", side_effect=[1, 3, 5]), \
             patch("agent.collect.psutil.cpu_percent", side_effect=[0, OSError(), 40, 50]), \
             patch("agent.collect.psutil.virtual_memory", side_effect=[OSError(), type("RAM", (), {"percent": 60})(), type("RAM", (), {"percent": 61})()]), \
             patch("agent.collect.psutil.net_io_counters", side_effect=[Counters(0, 0), Counters(1000000, 2000000), Counters(2000000, 4000000)]):
            stream = samples("pc")
            first, second, third = next(stream), next(stream), next(stream)
            stream.close()
            self.assertIsNone(first["cpu_percent"])
            self.assertIsNone(first["ram_percent"])
            self.assertIsNone(first["upload_mbps"])
            self.assertIsNone(second["cpu_percent"])
            self.assertEqual(second["upload_mbps"], 4)
            self.assertEqual(third["cpu_percent"], 50)

    def test_backoff_is_capped(self):
        self.assertEqual([retry_delay(i) for i in range(6)], [3, 6, 12, 24, 30, 30])

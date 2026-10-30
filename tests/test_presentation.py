from datetime import datetime, timedelta, timezone
import unittest

from admin_gui.presentation import device_view


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.device = {"pc_id": "c-pc", "pc_name": "C", "online": True,
                       "metrics": {"timestamp": self.now.isoformat(), "cpu_percent": 0,
                                   "ram_percent": 50, "upload_mbps": None, "download_mbps": 1.25}}

    def test_zero_and_missing_are_distinct(self):
        view = device_view(self.device, now=self.now)
        self.assertEqual(view["cpu"], "0.0")
        self.assertEqual(view["up"], "—")
        self.assertEqual(view["down"], "1.25")

    def test_unavailable_states_hide_old_values(self):
        for connected, online, age, expected in (
                (False, True, 0, "서버 연결 끊김"),
                (True, False, 0, "오프라인"),
                (True, True, 7, "데이터 지연")):
            with self.subTest(expected=expected):
                self.device["online"] = online
                view = device_view(self.device, connected, self.now + timedelta(seconds=age))
                self.assertEqual(view["state"], expected)
                self.assertEqual(view["ram"], "—")

    def test_empty_and_invalid_timestamp(self):
        self.device["metrics"]["timestamp"] = "invalid"
        self.assertEqual(device_view(self.device)["state"], "측정 시각 오류")
        self.device["metrics"] = None
        self.assertEqual(device_view(self.device)["state"], "데이터 없음")


if __name__ == "__main__":
    unittest.main()

"""C's deterministic three-PC widget check; uses labelled synthetic data."""
from datetime import datetime, timedelta, timezone
import tkinter as tk
from unittest.mock import patch

from admin_gui.main import Dashboard


def main():
    root = tk.Tk()
    root.withdraw()
    with patch.object(Dashboard, "receive", return_value=None):
        dashboard = Dashboard(root, 0)
    try:
        now = datetime.now(timezone.utc)
        devices = [{"pc_id": f"sample-{i}", "pc_name": f"SAMPLE PC {i}", "online": True,
                    "last_received": now.isoformat(),
                    "metrics": {"timestamp": now.isoformat(), "cpu_percent": i * 10,
                                "ram_percent": 50, "upload_mbps": None, "download_mbps": 0}}
                   for i in range(1, 4)]

        def refresh(connected, data):
            dashboard.publish((connected, data))
            root.after_cancel(dashboard.timer)
            dashboard.tick()

        refresh(True, devices)
        assert len(dashboard.table.get_children()) == 3
        for device in devices:
            dashboard.table.selection_set(device["pc_id"])
            dashboard.show_detail()
            assert dashboard.details["cpu"].get() == f'{device["metrics"]["cpu_percent"]:.1f}'
            assert dashboard.charts.points[-1][1]["cpu_percent"] == device["metrics"]["cpu_percent"]
        dashboard.charts.canvas.configure(width=1000, height=210)
        root.update_idletasks()
        # Render against a fixed size even when the test window is withdrawn.
        with patch.object(dashboard.charts.canvas, "winfo_width", return_value=1000), \
                patch.object(dashboard.charts.canvas, "winfo_height", return_value=210):
            dashboard.charts.draw()
        assert any(dashboard.charts.canvas.type(item) == "oval"
                   for item in dashboard.charts.canvas.find_all())
        refresh(True, list(reversed(devices)))
        assert dashboard.table.selection() == ("sample-3",)
        devices[2]["online"] = False
        refresh(True, devices)
        assert dashboard.details["state"].get() == "오프라인"
        assert dashboard.details["cpu"].get() == "—"
        devices[2]["online"] = True
        devices[2]["metrics"]["timestamp"] = (now - timedelta(seconds=10)).isoformat()
        refresh(True, devices)
        assert dashboard.details["state"].get() == "데이터 지연"
        refresh(False, "simulated disconnect")
        assert dashboard.details["state"].get() == "서버 연결 끊김"
        assert dashboard.details["ram"].get() == "—"
        refresh(True, [])
        assert not dashboard.table.get_children()
        assert dashboard.details["name"].get() == "—"
        assert not dashboard.charts.points
        print("PASS: 3 sample PCs, selection persistence, offline, stale, disconnect, empty list")
    finally:
        dashboard.close()


if __name__ == "__main__":
    main()

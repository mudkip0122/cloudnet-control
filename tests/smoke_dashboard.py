"""C's deterministic three-PC widget check; uses labelled synthetic data."""
from datetime import datetime, timedelta, timezone
import tkinter as tk
import customtkinter as ctk
from unittest.mock import patch

from admin_gui.main import Dashboard
from admin_gui.sample import build_sample


def main():
    root = ctk.CTk()
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
        assert "관리자 인증: 미사용" in dashboard.connection_details.get()
        assert "TLS 관리자 인증 연결 필요" in dashboard.process_hint.get()
        assert dashboard.process_button.instate(["disabled"])
        for device in devices:
            dashboard.table.selection_set(device["pc_id"])
            dashboard.show_detail()
            assert dashboard.usage_values["cpu"].get() == f'{device["metrics"]["cpu_percent"]:.1f} %'
            assert dashboard.charts.points[-1][1]["cpu_percent"] == device["metrics"]["cpu_percent"]
        dashboard.charts.canvas.configure(width=1000, height=360)
        root.update_idletasks()
        dashboard.charts.draw()
        assert list(dashboard.charts.lines["cpu_percent"].get_ydata()) == [30]
        assert list(dashboard.charts.lines["ram_percent"].get_ydata()) == [50]
        assert list(dashboard.charts.lines["download_mbps"].get_ydata()) == [0]
        assert len(dashboard.charts.axes) == 2
        refresh(True, list(reversed(devices)))
        assert dashboard.table.selection() == ("sample-3",)
        devices[2]["online"] = False
        refresh(True, devices)
        assert dashboard.details["state"].get() == "오프라인"
        assert dashboard.usage_values["cpu"].get() == "—"
        devices[2]["online"] = True
        devices[2]["metrics"]["timestamp"] = (now - timedelta(seconds=10)).isoformat()
        refresh(True, devices)
        assert dashboard.details["state"].get() == "데이터 지연"
        refresh(False, "simulated disconnect")
        assert dashboard.details["state"].get() == "서버 연결 끊김"
        assert dashboard.usage_values["ram"].get() == "—"
        refresh(True, [])
        assert not dashboard.table.get_children()
        assert dashboard.details["name"].get() == "—"
        assert not dashboard.charts.points
        with patch("admin_gui.main.messagebox.showinfo") as info, \
                patch("admin_gui.main.messagebox.askokcancel", return_value=False) as confirm:
            dashboard.preview_command("종료")
            info.assert_called_once()
            confirm.assert_not_called()
            refresh(True, devices)
            dashboard.table.selection_set("sample-1")
            dashboard.preview_command("재부팅")
            confirm.assert_called_once()
            assert "sample-1" in confirm.call_args.args[1]
            assert "재부팅" in confirm.call_args.args[1]
        dashboard.context, dashboard.token = object(), "sample-admin-token"
        refresh(True, devices)
        assert "관리자 인증: 성공" in dashboard.connection_details.get()
        assert "sample-admin-token" not in dashboard.connection_details.get()
        assert not dashboard.process_button.instate(["disabled"])
        refresh(False, "인증 실패 · 다시 실행하세요")
        assert "관리자 인증: 실패" in dashboard.connection_details.get()
        assert dashboard.process_button.instate(["disabled"])
        print("PASS: 3 sample PCs, selection persistence, offline, stale, disconnect, empty list")
    finally:
        dashboard.close()


def sample_checks():
    # Use a separate process/Tk interpreter for this independent sample check.
    root = ctk.CTk()
    root.withdraw()
    with patch.object(Dashboard, "receive") as receive, patch("admin_gui.main.connect") as connect:
        dashboard = build_sample(root)
        try:
            root.after_cancel(dashboard.timer)
            dashboard.tick()
            assert len(dashboard.table.get_children()) == 3
            assert "SAMPLE DATA" in root.title()
            assert "샘플 데이터" in dashboard.status.get()
            assert "연결하지 않음" in dashboard.connection_details.get()
            assert dashboard.usage_values["cpu"].get() == "23.5 %"
            for pc_id, expected in (("sample-pc-01", "온라인"), ("sample-pc-02", "데이터 없음"),
                                    ("sample-pc-03", "오프라인")):
                dashboard.table.selection_set(pc_id)
                dashboard.show_detail()
                assert dashboard.details["state"].get() == expected
            assert dashboard.process_button.instate(["disabled"])
            receive.assert_not_called()
            connect.assert_not_called()
            print("PASS: shared sample layout, explicit sample mode, three states, no network")
        finally:
            dashboard.close()


if __name__ == "__main__":
    import sys
    if "--sample" in sys.argv:
        sample_checks()
    else:
        main()

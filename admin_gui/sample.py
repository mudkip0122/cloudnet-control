"""Same dashboard layout with labelled sample data and no network worker."""
from datetime import timedelta
from math import sin

import customtkinter as ctk

from admin_gui.main import Dashboard


def build_sample(root):
    dashboard = Dashboard(root, 0, sample=True)
    stamp = dashboard.sample_now.isoformat()
    devices = [
        {"pc_id": "sample-pc-01", "pc_name": "SAMPLE PC 01", "online": True,
         "last_received": stamp, "metrics": {
             "timestamp": stamp, "cpu_percent": 23.5, "ram_percent": 61.2,
             "upload_mbps": 1.25, "download_mbps": 12.8}},
        {"pc_id": "sample-pc-02", "pc_name": "SAMPLE PC 02", "online": True,
         "last_received": stamp, "metrics": None},
        {"pc_id": "sample-pc-03", "pc_name": "SAMPLE PC 03", "online": False,
         "last_received": stamp, "metrics": None},
    ]
    device = devices[0]
    current = device["metrics"]
    for offset in range(120, 0, -1):
        metrics = dict(current, timestamp=(dashboard.sample_now - timedelta(seconds=2 * offset)).isoformat(),
                       cpu_percent=current["cpu_percent"] + 2.1 * sin(offset * .28),
                       ram_percent=current["ram_percent"] + 1.4 * sin(offset * .13),
                       upload_mbps=current["upload_mbps"] + .35 * sin(offset * .52),
                       download_mbps=current["download_mbps"] + .8 * sin(offset * .41))
        dashboard.history.update({device["pc_id"]: dict(device, metrics=metrics)})
    current.update(rtt_status="ok", rtt_timestamp=stamp, rtt_ms=281)
    device["metrics_stale"] = False
    device["exam_mode"] = dict(enabled=True, exam_id="sample-exam", monitor_status="ok",
                               forbidden_processes=["notepad.exe"], poll_interval=1)
    dashboard.exam_store.snapshot(devices)
    events = [
        ("EXAM_TAB_MONITOR_STATUS", dict(status="monitoring", reason="extension_connected")),
        ("EXAM_CAMERA_STATUS", dict(status="on")),
        ("EXAM_TAB_CHANGED", dict(transition="left")),
        ("EXAM_TAB_CHANGED", dict(transition="returned")),
        ("EXAM_TAB_CHANGED", dict(transition="left")),
        ("EXAM_TAB_MONITOR_STATUS", dict(status="unavailable", reason="extension_disconnected")),
        ("EXAM_TAB_MONITOR_STATUS", dict(status="monitoring", reason="extension_connected")),
        ("EXAM_TAB_CHANGED", dict(transition="closed")),
    ]
    for index, (kind, fields) in enumerate(events):
        dashboard.exam_store.accept(dict(type="EXAM_EVENT", version=1, event_id=f"sample-event-{index}",
            pc_id=device["pc_id"], exam_id="sample-exam", kind=kind,
            timestamp=(dashboard.sample_now - timedelta(seconds=30 - index * 3)).isoformat(), **fields))
    dashboard.publish((True, devices))
    return dashboard


def main():
    root = ctk.CTk()
    build_sample(root)
    root.mainloop()


if __name__ == "__main__":
    main()

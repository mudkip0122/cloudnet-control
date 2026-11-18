"""Same dashboard layout with labelled sample data and no network worker."""
from datetime import timedelta

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

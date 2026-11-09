"""Same dashboard layout with labelled sample data and no network worker."""
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
    dashboard.publish((True, devices))
    return dashboard


def main():
    root = ctk.CTk()
    build_sample(root)
    root.mainloop()


if __name__ == "__main__":
    main()

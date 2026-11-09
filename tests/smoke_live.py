"""Real Agent -> server -> dashboard worker and widgets integration check."""
import socket
import subprocess
import sys
import time
import tkinter as tk
from admin_gui.main import Dashboard
from common.protocol import recv_message, send_message


def main():
    processes = []
    root = None
    try:
        server = subprocess.Popen([sys.executable, "-u", "-m", "server.main", "--port", "15002"], stdout=subprocess.PIPE, text=True)
        processes.append(server)
        assert "15002" in server.stdout.readline()
        agent = subprocess.Popen([sys.executable, "-u", "-m", "agent.main", "--port", "15002", "--live", "--pc-id", "live-test"])
        processes.append(agent)
        with socket.create_connection(("127.0.0.1", 15002), timeout=3) as admin:
            send_message(admin, {"type": "REGISTER", "role": "admin"})
            assert recv_message(admin)["ok"]
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                send_message(admin, {"type": "LIST_DEVICES"})
                devices = recv_message(admin)["devices"]
                if devices and (devices[0].get("metrics") or {}).get("upload_mbps") is not None:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("No real metrics received")
            print("Real metrics received:", devices[0]["metrics"])
            root = tk.Tk()
            root.withdraw()
            dashboard = Dashboard(root, 15002)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                root.update()
                children = dashboard.table.get_children()
                if children and dashboard.table.item(children[0], "values")[2] != "—":
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("GUI did not display real metrics")
            print("GUI row:", dashboard.table.item(children[0], "values"))
            dashboard.close()
            root = None
            agent.terminate()
            agent.wait(timeout=5)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                send_message(admin, {"type": "LIST_DEVICES"})
                if not recv_message(admin)["devices"][0]["online"]:
                    print("Offline transition OK")
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("Agent remained online")
    finally:
        if root is not None:
            root.destroy()
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    main()

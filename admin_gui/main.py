"""Live local dashboard: python -m admin_gui.main."""
import argparse
from datetime import datetime, timezone
import queue
import socket
import threading
import tkinter as tk
from tkinter import ttk

from common.protocol import recv_message, send_message


class Dashboard:
    def __init__(self, root, port):
        self.root = root
        self.port = port
        self.stop = threading.Event()
        self.updates = queue.Queue(maxsize=1)
        self.sock = None
        root.title("CloudNet Control — Live local monitoring")
        root.geometry("1000x380")
        self.status = tk.StringVar(value="서버 연결 중…")
        ttk.Label(root, textvariable=self.status, font=("", 13)).pack(pady=15)
        columns = ("name", "state", "cpu", "ram", "up", "down", "time")
        self.table = ttk.Treeview(root, columns=columns, show="headings", height=8)
        for col, title in zip(columns, ("PC 이름 / ID", "상태", "CPU %", "RAM %", "업로드 Mbps", "다운로드 Mbps", "측정 시각 UTC")):
            self.table.heading(col, text=title)
            self.table.column(col, width=120, anchor="center")
        self.table.column("name", width=200)
        self.table.column("time", width=180)
        self.table.pack(fill="both", expand=True, padx=15)
        ttk.Label(root, text="실제 측정값 · Agent 2초 수집 / 화면 1초 조회 · 결측값 — · 로컬 전용").pack(pady=15)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.worker = threading.Thread(target=self.receive, daemon=True)
        self.worker.start()
        self.timer = root.after(100, self.tick)

    def publish(self, value):
        try:
            self.updates.get_nowait()
        except queue.Empty:
            pass
        self.updates.put_nowait(value)

    def receive(self):
        while not self.stop.is_set():
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=5) as conn:
                    self.sock = conn
                    send_message(conn, {"type": "REGISTER", "role": "admin"})
                    response = recv_message(conn)
                    if response.get("type") != "REGISTER_RESULT" or response.get("ok") is not True:
                        raise ValueError(str(response))
                    while not self.stop.is_set():
                        send_message(conn, {"type": "LIST_DEVICES"})
                        message = recv_message(conn)
                        if message.get("type") != "DEVICE_LIST":
                            raise ValueError(str(message))
                        self.publish((True, message["devices"]))
                        self.stop.wait(1)
            except (OSError, EOFError, ValueError) as exc:
                if not self.stop.is_set():
                    self.publish((False, str(exc)))
                    self.stop.wait(3)
            finally:
                self.sock = None

    def tick(self):
        try:
            connected, data = self.updates.get_nowait()
            self.table.delete(*self.table.get_children())
            if not connected:
                self.status.set("서버 연결 끊김 · 3초 후 재시도: " + data)
            else:
                self.status.set(f"서버 연결됨 · 등록 PC {len(data)}대" if data else "서버 연결됨 · Agent 접속 대기")
                for device in data:
                    metrics = device.get("metrics") or {}
                    stale = True
                    if metrics.get("timestamp"):
                        stamp = datetime.fromisoformat(metrics["timestamp"].replace("Z", "+00:00"))
                        stale = (datetime.now(timezone.utc) - stamp).total_seconds() > 6
                    valid = device["online"] and not stale
                    def fmt(field, places):
                        value = metrics.get(field)
                        return f"{value:.{places}f}" if valid and value is not None else "—"
                    state = "오프라인" if not device["online"] else ("데이터 대기 / 지연" if stale else "온라인")
                    self.table.insert("", "end", values=(device["pc_name"] + " / " + device["pc_id"], state,
                        fmt("cpu_percent", 1), fmt("ram_percent", 1), fmt("upload_mbps", 2),
                        fmt("download_mbps", 2), metrics.get("timestamp", "—")))
        except queue.Empty:
            pass
        self.timer = self.root.after(100, self.tick)

    def close(self):
        self.stop.set()
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.root.after_cancel(self.timer)
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    root = tk.Tk()
    Dashboard(root, args.port)
    root.mainloop()


if __name__ == "__main__":
    main()

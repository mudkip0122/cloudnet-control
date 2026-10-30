"""Live local dashboard: python -m admin_gui.main."""
import argparse
import queue
import socket
import threading
import tkinter as tk
from tkinter import ttk

from common.protocol import recv_message, send_message
from admin_gui.presentation import device_view
from admin_gui.charts import LiveCharts, MetricHistory


class Dashboard:
    def __init__(self, root, port):
        self.root = root
        self.port = port
        self.stop = threading.Event()
        self.updates = queue.Queue(maxsize=1)
        self.sock = None
        self.devices = {}
        self.connected = False
        self.history = MetricHistory()
        root.title("CloudNet Control — Live local monitoring")
        root.geometry("1100x880")
        root.minsize(950, 820)
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
        self.table.bind("<<TreeviewSelect>>", self.show_detail)
        detail = ttk.LabelFrame(root, text="선택 PC 상세")
        detail.pack(fill="x", padx=15, pady=10)
        self.details = {}
        for index, (key, label) in enumerate((
                ("name", "PC 이름 / ID"), ("state", "상태"),
                ("cpu", "CPU %"), ("ram", "RAM %"),
                ("up", "업로드 Mbps"), ("down", "다운로드 Mbps"),
                ("time", "측정 시각 UTC"), ("received", "서버 최근 수신 UTC"),
                ("age", "측정 후 경과"))):
            value = tk.StringVar(value="—")
            self.details[key] = value
            ttk.Label(detail, text=label).grid(row=index, column=0, sticky="w", padx=12, pady=2)
            ttk.Label(detail, textvariable=value).grid(row=index, column=1, sticky="w", padx=12)
        usage = ttk.Frame(detail)
        usage.grid(row=0, column=2, rowspan=9, sticky="nsew", padx=24, pady=10)
        detail.columnconfigure(2, weight=1)
        usage.columnconfigure((0, 1), weight=1)
        self.usage_values = {}
        for column, (key, title, color) in enumerate((
                ("cpu", "CPU 사용률", "#2563eb"), ("ram", "RAM 사용률", "#db2777"))):
            card = ttk.LabelFrame(usage, text=title, padding=(18, 16))
            card.grid(row=0, column=column, sticky="nsew", padx=6)
            value = tk.StringVar(value="—")
            self.usage_values[key] = value
            ttk.Label(card, textvariable=value, font=("Segoe UI", 32, "bold"),
                      foreground=color, anchor="center").pack(fill="x", expand=True)
        ttk.Label(usage, text="종료·재부팅: 서버 명령 연동 후 제공").grid(
            row=1, column=0, columnspan=2, pady=(12, 0))
        self.charts = LiveCharts(root)
        self.charts.pack(fill="both", expand=True, padx=15)
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
            self.connected = connected
            if not connected:
                self.status.set("서버 연결 끊김 · 3초 후 재시도: " + data)
            else:
                self.status.set(f"서버 연결됨 · 등록 PC {len(data)}대" if data else "서버 연결됨 · Agent 접속 대기")
                self.devices = {device["pc_id"]: device for device in data}
                self.history.update(self.devices)
        except queue.Empty:
            pass
        # Re-evaluate freshness even while no new server response arrives.
        for pc_id in self.table.get_children():
            if pc_id not in self.devices:
                self.table.delete(pc_id)
        for pc_id, device in self.devices.items():
            view = device_view(device, self.connected)
            values = tuple(view[key] for key in ("name", "state", "cpu", "ram", "up", "down", "time"))
            if self.table.exists(pc_id):
                self.table.item(pc_id, values=values)
            else:
                self.table.insert("", "end", iid=pc_id, values=values)
        if not self.table.selection() and self.devices:
            self.table.selection_set(next(iter(self.devices)))
        self.show_detail()
        self.timer = self.root.after(100, self.tick)

    def show_detail(self, event=None):
        selection = self.table.selection()
        device = self.devices.get(selection[0]) if selection else None
        view = device_view(device, self.connected) if device else {}
        for key, variable in self.details.items():
            variable.set(view.get(key, "—"))
        for key, variable in self.usage_values.items():
            value = view.get(key, "—")
            variable.set(value + " %" if value != "—" else "—")
        points = self.history.samples.get(selection[0], ()) if device else ()
        self.charts.show(points, view.get("state", "PC를 선택하세요"))

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

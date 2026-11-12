"""Live local dashboard: python -m admin_gui.main."""
import argparse
import queue
import socket
import threading
import ssl
from datetime import datetime, timezone
import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk

from common.protocol import recv_message, send_message
from common.security import client_arguments, client_settings, connect
from admin_gui.presentation import device_view
from admin_gui.charts import LiveCharts, MetricHistory
from admin_gui.process_window import ProcessWindow
from admin_gui.exam_window import ExamWindow
from admin_gui.exam_events import ExamEventClient, ExamStore


class Dashboard:
    def __init__(self, root, port, host="127.0.0.1", context=None, token=None, server_name=None, sample=False):
        self.root = root
        self.port = port
        self.host, self.context, self.token = host, context, token
        self.server_name = server_name
        self.sample = sample
        self.sample_now = datetime.now(timezone.utc) if sample else None
        self.auth_state = "확인 전" if context else "미사용"
        self.closed = False
        self.connection_state = "connecting"
        self.stop = threading.Event()
        self.updates = queue.Queue(maxsize=1)
        self.sock = None
        self.devices = {}
        self.connected = False
        self.history = MetricHistory()
        self.snapshot_received = None
        self.process_window = None
        self.exam_window = None
        self.exam_store = ExamStore()
        self.exam_client = None
        self.exam_generation = 0
        self.exam_stream_connected = False
        self.exam_gap = False
        root.title("CloudNet Control — " + ("SAMPLE DATA / OFFLINE" if sample else "PC 모니터링"))
        root.geometry("1100x940")
        root.minsize(950, 900)
        self.status = tk.StringVar(value="샘플 데이터 · 서버 연결 없음" if sample else "서버 연결 중…")
        ctk.CTkLabel(root, textvariable=self.status, font=("", 16)).pack(pady=15)
        self.connection_details = tk.StringVar()
        ctk.CTkLabel(root, textvariable=self.connection_details).pack()
        self.update_connection_details()
        self.event_status = tk.StringVar(value="초기 전체 목록 대기")
        ctk.CTkLabel(root, textvariable=self.event_status).pack()
        self.exam_stream = tk.StringVar(value="시험 이벤트: TLS 관리자 연결 필요" if not sample else "샘플 · 시험 이벤트 연결 없음")
        ttk.Label(root, textvariable=self.exam_stream).pack()
        self.exam_warning = tk.StringVar(value="금지 프로그램 감지 이력 없음 · 감시 정상/시험 준수를 뜻하지 않음")
        ttk.Label(root, textvariable=self.exam_warning, foreground="#b91c1c", wraplength=1050).pack(padx=15)
        self.foreground_warning = tk.StringVar(value="Chrome 이탈·복귀 감지 이력 없음 · 활성 창 감시 상태는 선택 PC 상세에서 확인")
        self.foreground_notice = ttk.Label(root, textvariable=self.foreground_warning, foreground="#64748b", wraplength=1050)
        self.foreground_notice.pack(padx=15)
        columns = ("name", "state", "cpu", "ram", "up", "down", "rtt", "time")
        self.table = ttk.Treeview(root, columns=columns, show="headings", height=5)
        for col, title in zip(columns, ("PC 이름 / ID", "상태", "CPU %", "RAM %", "업로드 Mbps", "다운로드 Mbps", "서버 RTT(ms)", "측정 시각 UTC")):
            self.table.heading(col, text=title)
            self.table.column(col, width=120, anchor="center")
        self.table.column("name", width=200)
        self.table.column("time", width=180)
        self.table.pack(fill="both", expand=True, padx=15)
        self.table.bind("<<TreeviewSelect>>", self.show_detail)
        detail = ttk.LabelFrame(root, text="선택 PC 상세")
        detail.pack(fill="x", padx=15, pady=10)
        detail.columnconfigure(1, weight=1)
        self.details = {}
        for index, (key, label) in enumerate((
                ("name", "PC 이름 / ID"), ("state", "상태"),
                ("up", "업로드 Mbps"), ("down", "다운로드 Mbps"),
                ("time", "측정 시각 UTC"), ("received", "서버 측정 수신 UTC"),
                ("age", "측정 후 경과"), ("gui_received", "GUI 목록 수신 UTC"),
                ("snapshot_age", "목록 수신 후 경과"), ("rtt", "서버 RTT(ms)"),
                ("foreground", "Chrome 활성 창 감시"))):
            value = tk.StringVar(value="—")
            self.details[key] = value
            ttk.Label(detail, text=label).grid(row=index, column=0, sticky="w", padx=12, pady=2)
            ttk.Label(detail, textvariable=value).grid(row=index, column=1, sticky="w", padx=12)
        actions = ttk.Frame(detail, padding=(4, 4))
        actions.grid(row=0, column=2, rowspan=11, sticky="ne", padx=16, pady=4)
        self.preview_buttons = []
        for row, action in enumerate(("PC 종료", "PC 재부팅")):
            button = ttk.Button(actions, text=action,
                                command=lambda action=action: self.preview_command(action))
            button.grid(row=0, column=row, sticky="ew", padx=4, pady=4)
            self.preview_buttons.append(button)
        self.process_button = ttk.Button(actions, text="프로세스 목록", command=self.open_processes,
                                         state="disabled")
        self.process_button.grid(row=1, column=0, sticky="ew", padx=4, pady=4)
        self.exam_button = ttk.Button(actions, text="시험 모드", command=self.open_exam, state="disabled")
        self.exam_button.grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        ttk.Label(actions, text="PC 종료·재부팅: 확인창 미리보기", foreground="#64748b").grid(
            row=2, column=0, columnspan=2, sticky="w", padx=4, pady=(10, 4))
        self.process_hint = tk.StringVar()
        ttk.Label(actions, textvariable=self.process_hint, wraplength=264, foreground="#64748b").grid(
            row=3, column=0, columnspan=2, sticky="w", padx=4)
        monitoring = ttk.Frame(root)
        monitoring.pack(fill="both", expand=True, padx=15)
        monitoring.columnconfigure(1, weight=1)
        monitoring.rowconfigure(0, weight=1)
        usage = ttk.Frame(monitoring)
        usage.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        usage.columnconfigure(0, weight=1)
        usage.rowconfigure((0, 1), weight=1)
        self.usage_values = {}
        for row, (key, title, color) in enumerate((
                ("cpu", "CPU 사용률", "#2563eb"), ("ram", "RAM 사용률", "#db2777"))):
            card = ttk.LabelFrame(usage, text=title, padding=(18, 16))
            card.grid(row=row, column=0, sticky="nsew", pady=4)
            value = tk.StringVar(value="—")
            self.usage_values[key] = value
            ttk.Label(card, textvariable=value, font=("Segoe UI", 32, "bold"),
                      foreground=color, anchor="center").pack(fill="x", expand=True)
        self.charts = LiveCharts(monitoring)
        self.charts.grid(row=0, column=1, sticky="nsew")
        ttk.Label(root, text=("샘플 데이터 · 예시 값과 시각 고정 · 서버 통신·실제 제어 없음" if sample else
                  "실제 측정값 · Agent 2초 수집 / 화면 1초 조회 · 결측값 —")).pack(pady=15)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.worker = threading.Thread(target=(lambda: None) if sample else self.receive, daemon=True)
        if not sample and context is not None and token:
            self.exam_client = ExamEventClient(host, port, context, token, server_name)
        self.worker.start()
        self.timer = root.after(100, self.tick)

    def update_connection_details(self):
        if self.sample:
            self.connection_details.set("실행 모드: 샘플 · 서버: 연결하지 않음 · 관리자 인증: 미사용")
        else:
            mode = "TLS" if self.context else "로컬 개발 / 평문"
            self.connection_details.set(
                f"서버: {self.host}:{self.port} · 연결 모드: {mode} · 관리자 인증: {self.auth_state}")

    def publish(self, value):
        try:
            self.updates.get_nowait()
        except queue.Empty:
            pass
        self.updates.put_nowait(value)

    def receive(self):
        while not self.stop.is_set():
            try:
                with connect(self.host, self.port, self.context, getattr(self, "server_name", None)) as conn:
                    self.sock = conn
                    if self.stop.is_set():
                        return
                    request = {"type": "REGISTER", "role": "admin"}
                    if self.token:
                        request["token"] = self.token
                    send_message(conn, request)
                    response = recv_message(conn)
                    if response.get("code") == "UNAUTHORIZED" or (
                            response.get("type") == "ERROR" and response.get("message") == "Authentication failed") or (
                            response.get("type") == "REGISTER_RESULT" and response.get("ok") is False):
                        self.publish((False, "인증 실패 · 관리자 토큰을 확인한 뒤 다시 실행하세요"))
                        return
                    if response.get("type") != "REGISTER_RESULT" or response.get("ok") is not True:
                        raise ValueError("등록 응답 형식 오류")
                    while not self.stop.is_set():
                        send_message(conn, {"type": "LIST_DEVICES"})
                        message = recv_message(conn)
                        if message.get("type") != "DEVICE_LIST" or not isinstance(message.get("devices"), list):
                            raise ValueError("목록 응답 형식 오류")
                        ids = set()
                        for device in message["devices"]:
                            if (not isinstance(device, dict) or not isinstance(device.get("pc_id"), str)
                                    or not device["pc_id"] or device["pc_id"] in ids
                                    or not isinstance(device.get("pc_name"), str)
                                    or not isinstance(device.get("online"), bool)
                                    or (device.get("metrics") is not None and not isinstance(device["metrics"], dict))):
                                raise ValueError("PC 목록 필드 오류")
                            ids.add(device["pc_id"])
                        self.publish((True, message["devices"]))
                        self.stop.wait(1)
            except ssl.SSLCertVerificationError:
                self.publish((False, "인증서 오류 · CA·만료일·서버 이름을 확인한 뒤 다시 실행하세요"))
                return
            except ssl.SSLError:
                if not self.stop.is_set():
                    self.publish((False, "TLS 연결 오류 · 3초 후 재시도"))
                    self.stop.wait(3)
            except (OSError, EOFError, ValueError, UnicodeError) as exc:
                if not self.stop.is_set():
                    label = "서버 응답 오류" if isinstance(exc, (ValueError, UnicodeError)) else "서버 연결 끊김"
                    self.publish((False, label + " · 3초 후 재시도"))
                    self.stop.wait(3)
            finally:
                self.sock = None

    def tick(self):
        if self.closed:
            return
        try:
            connected, data = self.updates.get_nowait()
            self.connected = connected
            if not connected:
                self.history.disconnect()
                self.exam_store.invalidate()
                self.connection_state = "error"
                self.status.set(data)
                if self.context:
                    self.auth_state = "실패" if data.startswith("인증 실패") else "연결 미확인"
            else:
                self.snapshot_received = datetime.now(timezone.utc)
                self.connection_state = "normal" if data else "empty"
                self.auth_state = "성공" if self.context and self.token else "미사용"
                if self.sample:
                    self.status.set(f"샘플 데이터 · 예시 PC {len(data)}대 · 실제 서버 연결 없음")
                else:
                    self.status.set(f"서버 연결됨 · 등록 PC {len(data)}대" if data else "서버 연결됨 · Agent 접속 대기")
                changes = []
                for device in data:
                    previous = self.devices.get(device["pc_id"])
                    if previous is None or previous["online"] != device["online"]:
                        changes.append(device["pc_name"] + (" 접속" if device["online"] else " 해제"))
                self.event_status.set("샘플 목록 · 행을 선택해 정상·데이터 없음·오프라인 예시를 확인하세요"
                                      if self.sample else " · ".join(changes) if changes else "전체 목록 동기화 완료")
                self.devices = {device["pc_id"]: device for device in data}
                self.history.update(self.devices)
                self.exam_store.snapshot(data)
        except queue.Empty:
            pass
        self.drain_exam_events()
        self.update_connection_details()
        # Re-evaluate freshness even while no new server response arrives.
        for pc_id in self.table.get_children():
            if pc_id not in self.devices:
                self.table.delete(pc_id)
        for pc_id, device in self.devices.items():
            view = device_view(device, self.connected, now=self.sample_now)
            values = tuple(view[key] for key in ("name", "state", "cpu", "ram", "up", "down", "rtt", "time"))
            if self.table.exists(pc_id):
                self.table.item(pc_id, values=values)
            else:
                self.table.insert("", "end", iid=pc_id, values=values)
        if not self.table.selection() and self.devices:
            self.table.selection_set(next(iter(self.devices)))
        self.show_detail()
        self.timer = self.root.after(100, self.tick)

    def drain_exam_events(self):
        if self.exam_client is None:
            return
        try:
            generation, connected, label = self.exam_client.connection.get_nowait()
            if generation > 1 or self.exam_generation and not connected:
                self.exam_gap = True
                self.exam_store.foreground.clear()
            self.exam_store.foreground_stream_connected = connected
            self.exam_generation, self.exam_stream_connected = generation, connected
            self.exam_stream.set("시험 이벤트: " + label + (" · 이전 수신 공백 가능" if self.exam_gap else ""))
        except queue.Empty:
            pass
        # Limit work per Tk tick while retaining every queued event until processed.
        for _ in range(200):
            try:
                generation, message = self.exam_client.events.get_nowait()
            except queue.Empty:
                break
            kind = message.get('type')
            if kind == 'EXAM_EVENT':
                self.exam_store.accept(message, apply_current=generation == self.exam_generation and self.exam_stream_connected)
            elif generation == self.exam_generation and self.exam_stream_connected and self.connected:
                if kind == 'DEVICE_LIST':
                    self.exam_store.snapshot(message['devices'])
                elif kind == 'STATUS_UPDATE':
                    self.exam_store.status_update(message['device'])
        event = self.exam_store.latest_warning
        if event is not None:
            process = event['process']
            self.exam_warning.set(f"금지 프로그램 실행 감지 이력 · PC {event['pc_id']} · 시험 {event['exam_id']} · "
                f"{process['name']} (PID {process['pid']}) · {event['timestamp']} · 현재 실행 여부 미확인")
        event = self.exam_store.latest_foreground
        if event is not None:
            label = 'Chrome 이탈' if event['transition'] == 'left' else 'Chrome 복귀'
            process = event['process']
            self.foreground_warning.set(f"{label} 감지 이력 · PC {event['pc_id']} · 시험 {event['exam_id']} · "
                f"{event['previous_process']['name']} → {process['name']} (PID {process['pid']}) · {event['timestamp']}")
            self.foreground_notice.configure(foreground='#b91c1c' if event['transition'] == 'left' else '#047857')

    def show_detail(self, event=None):
        selection = self.table.selection()
        device = self.devices.get(selection[0]) if selection else None
        view = device_view(device, self.connected, now=self.sample_now) if device else {}
        if device:
            view['foreground'] = self.exam_store.foreground_state(device['pc_id'], self.connected)
        if device and self.snapshot_received is not None:
            view["gui_received"] = self.snapshot_received.isoformat()
            view["snapshot_age"] = ("샘플 · 시각 고정" if self.sample else
                f"{max(0, (datetime.now(timezone.utc) - self.snapshot_received).total_seconds()):.1f}초")
        for key, variable in self.details.items():
            variable.set(view.get(key, "—"))
        for key, variable in self.usage_values.items():
            value = view.get(key, "—")
            variable.set(value + " %" if value != "—" else "—")
        points = self.history.samples.get(selection[0], ()) if device else ()
        self.charts.show(points, view.get("state", "PC를 선택하세요"), selection[0] if device else None)
        if self.sample:
            hint = "샘플 모드 · 실제 프로세스 조회·종료 불가"
        elif self.context is None or not self.token:
            hint = "프로세스 조회·종료: TLS 관리자 인증 연결 필요"
        elif not self.connected:
            hint = "프로세스 조회·종료: 서버 연결·관리자 인증 대기"
        elif not device or not device.get("online"):
            hint = "프로세스 조회·종료: 온라인 PC를 선택하세요"
        else:
            hint = "Agent의 프로세스 제어가 활성화되어 있어야 합니다"
        self.process_hint.set(hint)
        available = (not self.sample and self.context is not None and bool(self.token)
                     and self.connected and device and view.get("state") == "온라인")
        self.process_button.configure(state="normal" if available else "disabled")
        for button in self.preview_buttons:
            button.configure(state="normal" if device and view.get("state") == "온라인" else "disabled")
        can_view_exam = not self.sample and self.context is not None and bool(self.token) and bool(device or self.exam_store.history)
        self.exam_button.configure(state="normal" if can_view_exam else "disabled")
        if self.process_window is not None and not self.process_window.closed:
            self.process_window.set_target(device, self.connected and view.get("state") == "온라인")
        if self.exam_window is not None and not self.exam_window.closed:
            self.exam_window.set_target(device, self.connected and view.get("state") == "온라인")

    def open_exam(self):
        if self.sample or self.context is None or not self.token:
            return
        if self.exam_window is None or self.exam_window.closed:
            self.exam_window = ExamWindow(self)
        self.show_detail()
        self.exam_window.window.lift()

    def open_processes(self):
        if self.sample or self.context is None or not self.token or not self.connected:
            messagebox.showinfo("프로세스 목록", self.process_hint.get(), parent=self.root)
            return
        if self.process_window is None or self.process_window.closed:
            self.process_window = ProcessWindow(self)
        selection = self.table.selection()
        device = self.devices.get(selection[0]) if selection else None
        self.process_window.set_target(device, self.connected)
        self.process_window.window.lift()

    def preview_command(self, action):
        selection = self.table.selection()
        device = self.devices.get(selection[0]) if selection else None
        if not device or not self.connected or not device.get("online"):
            messagebox.showinfo("대상 확인", "연결된 PC 한 대를 선택하세요", parent=self.root)
            return
        if messagebox.askokcancel("PC 작업 확인 (미연동)",
                                 f"대상: {device['pc_name']}\n고유 ID: {device['pc_id']}\n작업: {action}\n"
                                 "PC 제어 미연동 상태입니다. 실제 명령은 전송하지 않습니다.\n"
                                 "확인 화면을 검토하시겠습니까?", parent=self.root):
            messagebox.showinfo("확인창 검토 완료", "PC 제어 미연동 · 실제 종료·재부팅은 수행하지 않았습니다", parent=self.root)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process_window is not None:
            self.process_window.close()
        if self.exam_window is not None:
            self.exam_window.close()
        if self.exam_client is not None:
            self.exam_client.close()
        self.stop.set()
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.root.after_cancel(self.timer)
        self.finish_close()

    def finish_close(self):
        process_client = self.process_window.client if self.process_window is not None else None
        exam_client = self.exam_window.client if self.exam_window is not None else None
        if (self.worker.is_alive() or (process_client is not None and process_client.worker.is_alive())
                or (exam_client is not None and exam_client.worker.is_alive())):
            self.root.after(50, self.finish_close)
        elif self.exam_client is not None and self.exam_client.worker.is_alive():
            self.root.after(50, self.finish_close)
        else:
            self.worker.join()
            self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5000)
    client_arguments(parser)
    args = parser.parse_args()
    try:
        context, token = client_settings(args)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    root = ctk.CTk()
    Dashboard(root, args.port, args.host, context, token, args.server_name)
    root.mainloop()


if __name__ == "__main__":
    main()

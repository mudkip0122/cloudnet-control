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
from admin_gui.theme import BACKGROUND, FONT, GREEN, MUTED, RED, configure_theme, header, window
from admin_gui.charts import LiveCharts, MetricHistory
from admin_gui.process_window import ProcessWindow
from admin_gui.exam_window import ExamWindow
from admin_gui.history_window import HistoryWindow
from admin_gui.exam_events import ExamEventClient, ExamStore
from admin_gui.power_control import PowerOperation, power_target
from admin_gui.process_control import POWER_ACTIONS, ProcessClient


class Dashboard:
    DETAIL_FIELDS = (
        ("name", "PC 이름 / ID"), ("state", "상태"), ("up", "업로드 Mbps"),
        ("down", "다운로드 Mbps"), ("time", "측정 시각 UTC"), ("received", "서버 측정 수신 UTC"),
        ("age", "측정 후 경과"), ("gui_received", "GUI 목록 수신 UTC"),
        ("snapshot_age", "목록 수신 후 경과"), ("rtt", "서버 RTT"), ("foreground", "Chrome 활성 창 감시"),
    )

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
        self.history_window = None
        self.exam_store = ExamStore()
        self.exam_client = None
        self.exam_generation = 0
        self.exam_stream_connected = False
        self.exam_gap = False
        self.power_client = None
        self.power_operations = {}
        root.title("CloudNet Control — " + ("SAMPLE DATA / OFFLINE" if sample else "PC 모니터링"))
        root.geometry("1240x820")
        root.minsize(960, 690)
        root.configure(fg_color=BACKGROUND)
        configure_theme(root)
        self.status = tk.StringVar(value="샘플 데이터 · 서버 연결 없음" if sample else "서버 연결 중…")
        self.connection_details = tk.StringVar()
        self.event_status = tk.StringVar(value="초기 전체 목록 대기")
        self.update_connection_details()
        self.exam_stream = tk.StringVar(value="시험 이벤트: TLS 관리자 연결 필요" if not sample else "샘플 · 시험 이벤트 연결 없음")
        self.exam_warning = tk.StringVar(value="금지 프로그램 감지 이력 없음 · 감시 정상/시험 준수를 뜻하지 않음")
        self.foreground_warning = tk.StringVar(value="Chrome 이탈·복귀 감지 이력 없음 · 활성 창 감시 상태는 선택 PC 상세에서 확인")
        self.info_windows = {}
        bar = header(root, "CloudNet Control")
        ttk.Button(bar, text="연결 상세", style="Header.TButton",
                   command=self.open_connection_info).pack(side="right")
        self.online_count = tk.StringVar(value="온라인 0/0대")
        ttk.Label(bar, textvariable=self.online_count, style="Header.TLabel").pack(side="right", padx=16)
        self.connection_badge = tk.StringVar(value="● 연결 중" if not sample else "샘플 모드")
        self.connection_label = ttk.Label(bar, textvariable=self.connection_badge, style="Header.TLabel")
        self.connection_label.pack(side="right")

        body = ttk.Frame(root, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        sidebar = ttk.Frame(body, style="Card.TFrame", padding=1)
        sidebar.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        ttk.Label(sidebar, text="PC 목록", style="CardTitle.TLabel", padding=(14, 12)).pack(fill="x")
        self.table = ttk.Treeview(sidebar, columns=("name", "state"), show=("tree", "headings"), style="PC.Treeview",
                                  selectmode="browse", height=4)
        self.table.column("#0", width=118, stretch=False)
        self.table.column("name", width=105, stretch=False)
        self.table.column("state", width=108, stretch=False)
        self.table.heading("#0", text="PC ID", anchor="w")
        self.table.heading("name", text="이름", anchor="w")
        self.table.heading("state", text="상태", anchor="w")
        self.table.tag_configure("online", foreground=GREEN)
        self.table.tag_configure("offline", foreground=MUTED)
        scroll = ttk.Scrollbar(sidebar, orient="vertical", command=self.table.yview)
        scroll.pack(side="right", fill="y")
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(fill="both", expand=True)
        self.table.bind("<<TreeviewSelect>>", self.show_detail)

        monitoring = ttk.Frame(body)
        monitoring.grid(row=0, column=1, sticky="nsew")
        summary = ttk.Frame(monitoring)
        summary.pack(fill="x", pady=(0, 10))
        self.selected_id = tk.StringVar(value="PC를 선택하세요")
        self.selected_name = tk.StringVar()
        ttk.Label(summary, textvariable=self.selected_id, style="Title.TLabel").pack(side="left")
        ttk.Label(summary, textvariable=self.selected_name, style="Muted.TLabel").pack(side="left", padx=12)
        self.details = {key: tk.StringVar(value="—") for key, _ in self.DETAIL_FIELDS}
        self.selected_state = ttk.Label(summary, textvariable=self.details["state"], padding=(8, 4))
        self.selected_state.pack(side="left")
        self.detail_button = ttk.Button(summary, text="상세 정보", command=self.open_device_info, state="disabled")
        self.detail_button.pack(side="right")

        metrics = ttk.Frame(monitoring)
        metrics.pack(fill="x", pady=(0, 10))
        self.usage_values = {}
        for column, (key, title) in enumerate((("cpu", "CPU 사용률"), ("ram", "RAM 사용률"), ("rtt", "서버 RTT"))):
            metrics.columnconfigure(column, weight=1, uniform="metrics")
            card = ttk.Frame(metrics, style="Card.TFrame", padding=(16, 9))
            card.grid(row=0, column=column, sticky="nsew", padx=(0, 10) if column < 2 else 0)
            value = tk.StringVar(value="—")
            self.usage_values[key] = value
            ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w")
            ttk.Label(card, textvariable=value, style="Value.TLabel").pack(anchor="w")
        actions = ttk.Frame(monitoring, style="Card.TFrame", padding=(12, 8))
        actions.pack(side="bottom", fill="x", pady=(10, 0))
        toolbar = ttk.Frame(actions, style="Surface.TFrame")
        toolbar.pack(fill="x")
        self.history_button = ttk.Button(toolbar, text="이력·경고", command=self.open_history,
                                         state="normal" if sample or context is not None and token else "disabled")
        self.history_button.pack(side="left")
        self.process_button = ttk.Button(toolbar, text="프로세스 목록", command=self.open_processes, state="disabled")
        self.process_button.pack(side="left", padx=8)
        self.exam_button = ttk.Button(toolbar, text="시험 모드", command=self.open_exam, state="disabled")
        self.exam_button.pack(side="left")
        self.power_button = ttk.Menubutton(toolbar, text="전원 제어", state="disabled")
        self.power_button.pack(side="right")
        self.power_menu = tk.Menu(self.power_button, tearoff=False, font=(FONT, 10), foreground=RED)
        self.power_button.configure(menu=self.power_menu)
        for action, label in POWER_ACTIONS.items():
            self.power_menu.add_command(label=label, command=lambda action=action: self.confirm_power(action))
        self.power_status = tk.StringVar()
        self.process_hint = tk.StringVar()
        ttk.Label(actions, textvariable=self.power_status, style="Surface.TLabel",
                  foreground=MUTED).pack(anchor="w", pady=(6, 0))
        self.charts = LiveCharts(monitoring)
        self.charts.pack(fill="both", expand=True)
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

    def info_window(self, title, fields):
        child = self.info_windows.get(title)
        if child is not None and child.winfo_exists():
            child.deiconify()
            child.lift()
            return
        detail = len(fields) > 5
        child = window(self.root, title, "660x510" if detail else "660x280",
                       (610, 490) if detail else (610, 260))
        self.info_windows[title] = child
        header(child, title)
        card = ttk.Frame(child, style="Card.TFrame", padding=16)
        card.pack(fill="both", expand=True, padx=14, pady=14)
        card.columnconfigure(1, weight=1)
        for row, (label, variable) in enumerate(fields):
            ttk.Label(card, text=label, style="Surface.TLabel", foreground=MUTED).grid(
                row=row, column=0, sticky="nw", padx=(0, 14), pady=4)
            ttk.Label(card, textvariable=variable, style="Surface.TLabel", wraplength=415).grid(
                row=row, column=1, sticky="w", pady=4)
        child.bind("<Escape>", lambda event: child.destroy())

    def open_connection_info(self):
        self.info_window("연결 상세", (("상태", self.status), ("접속 정보", self.connection_details),
                                     ("목록 동기화", self.event_status), ("시험 이벤트", self.exam_stream)))

    def open_device_info(self):
        self.info_window("PC 상세 정보", tuple((label, self.details[key]) for key, label in self.DETAIL_FIELDS)
                         + (("프로세스 제어", self.process_hint),))

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
                if not self.exam_stream_connected:
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
                if self.history_window is not None and not self.history_window.closed:
                    self.history_window.set_choices(self.devices)
                self.history.update(self.devices)
                if not self.exam_stream_connected:
                    self.exam_store.snapshot(data, track_connections=False)
                    self.observe_power_devices(data)
        except queue.Empty:
            pass
        self.drain_exam_events()
        self.drain_power_results()
        self.update_connection_details()
        # Re-evaluate freshness even while no new server response arrives.
        for pc_id in self.table.get_children():
            if pc_id not in self.devices:
                self.table.delete(pc_id)
        for position, pc_id in enumerate(sorted(self.devices)):
            device = self.devices[pc_id]
            view = device_view(device, self.connected, now=self.sample_now)
            online = view["state"] == "온라인"
            values = (device["pc_name"], f'● {view["state"]}')
            options = dict(text=pc_id, values=values, tags=("online" if online else "offline",))
            if self.table.exists(pc_id):
                if tuple(self.table.item(pc_id, "values")) != values:
                    self.table.item(pc_id, **options)
            else:
                self.table.insert("", "end", iid=pc_id, **options)
            if self.table.index(pc_id) != position:
                self.table.move(pc_id, "", position)
        online = sum(bool(device.get("online")) for device in self.devices.values()) if self.connected else 0
        self.online_count.set(f"온라인 {online}/{len(self.devices)}대")
        badge = "샘플 모드" if self.sample else ("● 서버 연결됨" if self.connected else self.status.get())
        self.connection_badge.set(badge if self.connected or self.sample else badge.split(" · ")[0][:42])
        self.connection_label.configure(foreground="#86efac" if self.connected and not self.sample else "#ffffff")
        if not self.table.selection() and self.devices:
            self.table.selection_set(self.table.get_children()[0])
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
                self.exam_store.chrome_stream_gap()
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
            elif generation == self.exam_generation and self.exam_stream_connected:
                if kind == 'DEVICE_LIST':
                    self.exam_store.snapshot(message['devices'])
                    self.observe_power_devices(message['devices'])
                elif kind == 'STATUS_UPDATE':
                    self.exam_store.status_update(message['device'])
                    self.observe_power_devices([message['device']])
        self.update_exam_warnings()

    def update_exam_warnings(self):
        event = self.exam_store.latest_warning
        if event is not None:
            process = event['process']
            self.exam_warning.set(f"금지 프로그램 실행 감지 이력 · PC {event['pc_id']} · 시험 {event['exam_id']} · "
                f"{process['name']} (PID {process['pid']}) · {event['timestamp']} · 현재 실행 여부 미확인")
        else:
            self.exam_warning.set('표시할 금지 프로그램 감지 이력 없음 · 감시 정상/시험 준수를 뜻하지 않음')
        event = self.exam_store.latest_foreground
        if event is not None:
            label = 'Chrome 이탈' if event['transition'] == 'left' else 'Chrome 복귀'
            process = event['process']
            self.foreground_warning.set(f"{label} 감지 이력 · PC {event['pc_id']} · 시험 {event['exam_id']} · "
                f"{event['previous_process']['name']} → {process['name']} (PID {process['pid']}) · {event['timestamp']}")
        else:
            self.foreground_warning.set('표시할 Chrome 이탈·복귀 이력 없음 · 활성 창 감시 상태는 선택 PC 상세에서 확인')

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
            variable.set(value + " %" if key in ("cpu", "ram") and value != "—" else value)
        self.selected_id.set(device["pc_id"] if device else "PC를 선택하세요")
        self.selected_name.set(device["pc_name"] if device else "")
        self.selected_state.configure(style="Online.TLabel" if view.get("state") == "온라인" else "Muted.TLabel")
        self.detail_button.configure(state="normal" if device else "disabled")
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
        target = self.current_power_target(device)
        operation = self.power_operations.get(device["pc_id"]) if device else None
        self.power_button.configure(state="normal" if target and not (operation and operation.busy) else "disabled")
        self.power_status.set(
            f"{POWER_ACTIONS[operation.request['action']]} · {operation.label()}" if operation else
            "전원 제어 · " + ("모의 실행" if target[0] == "simulate" else "실제 실행") if target else
            "전원 제어 · 지원되는 온라인 PC 필요")
        can_view_exam = self.sample or (self.context is not None and bool(self.token)
                                       and bool(device or self.exam_store.history))
        self.exam_button.configure(state="normal" if can_view_exam else "disabled")
        if self.process_window is not None and not self.process_window.closed:
            self.process_window.set_target(device, self.connected and view.get("state") == "온라인")
        if self.exam_window is not None and not self.exam_window.closed:
            self.exam_window.sync_devices(preferred_id=device['pc_id'] if device else None)

    def open_history(self):
        if not self.sample and (self.context is None or not self.token):
            return
        if self.history_window is None or self.history_window.closed:
            selection = self.table.selection()
            self.history_window = HistoryWindow(self, selection[0] if selection else None)
        self.history_window.window.lift()

    def open_exam(self):
        if not self.sample and (self.context is None or not self.token):
            return
        if self.exam_window is None or self.exam_window.closed:
            self.exam_window = ExamWindow(self)
        selection = self.table.selection()
        self.exam_window.sync_devices(preferred_id=selection[0] if selection else None, force_selection=True)
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

    def current_power_target(self, device):
        if self.connected and (self.sample or self.context is not None and self.token):
            if device and self.exam_stream_connected:
                device = self.exam_store.devices.get(device["pc_id"], device)
            return power_target(device, self.sample)
        return None

    def confirm_power(self, action):
        selection = self.table.selection()
        device = self.devices.get(selection[0]) if selection else None
        target = self.current_power_target(device)
        if not target:
            return
        pc_id = device["pc_id"]
        existing = self.power_operations.get(pc_id)
        if existing and existing.busy:
            return
        mode = "모의 실행" if target[0] == "simulate" else "실제 실행"
        if not messagebox.askokcancel("PC 전원 작업 확인",
                f"대상 PC  {device['pc_name']} ({pc_id})\n작업  {POWER_ACTIONS[action]}\n모드  {mode}",
                parent=self.root):
            return
        if self.closed:
            return
        if (self.current_power_target(self.devices.get(pc_id)) != target
                or self.power_operations.get(pc_id) is not existing):
            messagebox.showinfo("대상 변경", "PC 연결 또는 전원 상태가 바뀌었습니다. 다시 확인해 주세요.",
                                parent=self.root)
            return
        operation = PowerOperation.start(pc_id, action, target)
        self.power_operations[pc_id] = operation
        if self.sample:
            self.root.after(300, operation.result, {"code": "POWER_ACCEPTED"})
            self.root.after(900, operation.result, {"code": "POWER_SIMULATED"})
        else:
            if self.power_client is None:
                self.power_client = ProcessClient(self.host, self.port, self.context, self.token,
                                                  timeout=35, server_name=self.server_name)
            self.power_client.submit(operation.request)
        self.show_detail()

    def observe_power_devices(self, devices):
        now = datetime.now(timezone.utc)
        for device in devices:
            if isinstance(device, dict) and isinstance(device.get("pc_id"), str):
                operation = self.power_operations.get(device["pc_id"])
                if operation:
                    operation.observe(device, now)

    def drain_power_results(self):
        if self.power_client is None:
            return
        while True:
            try:
                request, result = self.power_client.results.get_nowait()
            except queue.Empty:
                break
            operation = self.power_operations.get(request["target_id"])
            if operation and operation.request["command_id"] == request["command_id"]:
                operation.result(result)
        now = datetime.now(timezone.utc)
        for operation in self.power_operations.values():
            operation.observe(None, now)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process_window is not None:
            self.process_window.close()
        if self.exam_window is not None:
            self.exam_window.close()
        if self.history_window is not None and not self.history_window.closed:
            self.history_window.close()
        if self.exam_client is not None:
            self.exam_client.close()
        if self.power_client is not None:
            self.power_client.close()
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
        elif any(client is not None and client.worker.is_alive()
                 for client in (self.exam_client, self.power_client)):
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

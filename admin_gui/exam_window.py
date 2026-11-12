"""Exam commands use the B v1 contract through the authenticated server."""
import math
import queue
import re
import tkinter as tk
from tkinter import ttk, messagebox

from admin_gui.process_control import ProcessClient, command_request
from admin_gui.exam_events import MONITOR_LABELS, exam_mode, event_columns
from admin_gui.presentation import device_view


def exam_request(pc_id, action, exam_id="", names="", interval="1"):
    request = command_request(pc_id, "GET_EXAM_STATUS" if action == "status" else "SET_EXAM_MODE")
    if action == "status":
        return request
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", exam_id):
        raise ValueError("시험 ID는 영문·숫자·_ . - 1~64자입니다.")
    request.update(enabled=action == "on", exam_id=exam_id)
    if action == "on":
        entries = list(dict.fromkeys(name.strip().casefold() for name in names.split(",") if name.strip()))
        if names.strip() and (not 1 <= len(entries) <= 100 or any(
                len(name) > 128 or any(c in name for c in '/\\*?') for name in entries)):
            raise ValueError("금지 실행 파일 이름 1~100개를 쉼표로 구분하세요. 경로·와일드카드는 허용하지 않습니다.")
        value = float(interval)
        if not math.isfinite(value) or not .5 <= value <= 60:
            raise ValueError("검사 간격은 0.5~60초입니다.")
        request['poll_interval'] = value
        if entries:
            request['forbidden_processes'] = entries
    return request


class ExamWindow:
    def __init__(self, dashboard):
        self.dashboard = dashboard
        self.closed, self.pending, self.target = False, None, None
        self.generation = 0
        self.needs_status = False
        self.client = ProcessClient(dashboard.host, dashboard.port, dashboard.context,
                                    dashboard.token, server_name=dashboard.server_name)
        self.window = tk.Toplevel(dashboard.root)
        self.window.title("시험 모드")
        self.window.geometry("1040x840")
        self.window.minsize(850, 740)
        self.history_revision = -1
        self.display_pc_id = None
        self.last_mode_known = False
        self.agent_session_id = None
        self.pc_choice = tk.StringVar()
        self.pc_controls = {}
        self.target_text = tk.StringVar()
        self.status = tk.StringVar(value="상태 미확인")
        ttk.Label(self.window, textvariable=self.target_text).pack(padx=12, pady=8)
        self.monitor_text = tk.StringVar(value="시험 상태 미확인")
        ttk.Label(self.window, textvariable=self.monitor_text).pack(padx=12, pady=4)
        self.foreground_text = tk.StringVar(value='활성 창 감시 미확인')
        ttk.Label(self.window, textvariable=self.foreground_text).pack(padx=12, pady=4)
        ttk.Label(self.window, textvariable=dashboard.exam_stream, wraplength=1000).pack(padx=12)
        ttk.Label(self.window, textvariable=dashboard.exam_warning, foreground="#b91c1c", wraplength=1000).pack(padx=12, pady=4)
        self.foreground_notice = ttk.Label(self.window, textvariable=dashboard.foreground_warning, wraplength=1000)
        self.foreground_notice.pack(padx=12, pady=4)
        self.inputs = []
        for title, default in (("시험 ID", ""), ("금지 실행 파일 (쉼표 구분 · 비우면 기본 목록, 입력하면 대체)", ""), ("검사 간격 (초)", "1")):
            ttk.Label(self.window, text=title).pack(anchor="w", padx=12)
            value = tk.StringVar(value=default)
            ttk.Entry(self.window, textvariable=value, width=65).pack(padx=12, pady=4)
            self.inputs.append(value)
        self.buttons = []
        actions = ttk.Frame(self.window)
        actions.pack(fill='x', padx=12, pady=8)
        actions.columnconfigure(1, weight=1)
        ttk.Label(actions, text='각 행의 PC만 제어 · 위 시험 ID/금지 목록/검사 간격 적용').grid(
            row=0, column=0, columnspan=5, sticky='w', pady=(0, 4))
        for row, pc_id in enumerate(('PC-01', 'PC-02', 'PC-03'), start=1):
            ttk.Radiobutton(actions, text=pc_id, variable=self.pc_choice, value=pc_id,
                            command=self.sync_devices).grid(row=row, column=0, sticky='w', padx=(0, 8))
            state = tk.StringVar(value='상태 미확인')
            ttk.Label(actions, textvariable=state).grid(row=row, column=1, sticky='w', padx=8)
            controls = {'state': state}
            for column, (action, title) in enumerate((('on', '켜기'), ('off', '끄기'), ('status', '조회')), start=2):
                button = ttk.Button(actions, text=title, state='disabled',
                                    command=lambda pc=pc_id, a=action: self.submit_for_pc(pc, a))
                button.grid(row=row, column=column, padx=4, pady=2)
                controls[action] = button
                self.buttons.append(button)
            self.pc_controls[pc_id] = controls
        ttk.Label(self.window, textvariable=self.status, wraplength=520).pack(padx=12, pady=10)
        ttk.Label(self.window, text=(
            "일반 앱은 표시된 창 기준(최소화·트레이 제외), 원격제어 앱은 백그라운드 포함\n"
            "숨김·최소화 후 복원하면 같은 PID도 재감지 가능 · partial: 창·프로세스 일부 확인 불가\n"
            "미분류 사용자 지정 앱은 모든 실행 인스턴스 감지 · Chrome 이탈·복귀 감지 유지\n"
            "탭/URL 감시·자동 차단 없음 · 공백 전후 이탈 시간 계산 안 함"),
            wraplength=1000, justify="center").pack(padx=12, pady=8)
        ttk.Label(self.window, text="전체 PC 감지·감시 상태 이력 · 최신 수신순 / 최근 500개 · 과거 감지는 현재 실행 여부나 부정행위 확정이 아님").pack(padx=12)
        history_actions = ttk.Frame(self.window)
        history_actions.pack(fill='x', padx=12, pady=(4, 0))
        self.delete_selected_button = ttk.Button(history_actions, text='선택 내역 지우기',
                                                 command=self.delete_selected_history, state='disabled')
        self.delete_selected_button.pack(side='left')
        self.delete_all_button = ttk.Button(history_actions, text='전체 내역 지우기',
                                            command=self.delete_all_history, state='disabled')
        self.delete_all_button.pack(side='left', padx=8)
        ttk.Label(history_actions, text='이 GUI의 내역만 지움 · 서버 저장 기록 유지').pack(side='left', padx=8)
        container = ttk.Frame(self.window)
        container.pack(fill="both", expand=True, padx=12, pady=8)
        columns = ('pc', 'exam', 'kind', 'name', 'pid', 'time')
        self.history_table = ttk.Treeview(container, columns=columns, show='headings', height=7)
        for column, title, width in zip(columns,
                ('PC ID', '시험 ID', '종류 / 감시 상태', '프로그램 / 전환 / 조회 불가 수', 'PID', '이벤트 시각'),
                (85, 130, 160, 190, 70, 260)):
            self.history_table.heading(column, text=title)
            self.history_table.column(column, width=width)
        scroll = ttk.Scrollbar(container, orient='vertical', command=self.history_table.yview)
        self.history_table.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.history_table.pack(fill='both', expand=True)
        self.history_table.bind('<<TreeviewSelect>>', lambda event: self.update_history_buttons())
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.timer = self.window.after(100, self.tick)

    def device_ready(self, pc_id):
        device = self.dashboard.devices.get(pc_id)
        return bool(device and self.dashboard.connected and self.dashboard.context is not None
                    and self.dashboard.token and not self.dashboard.sample
                    and device_view(device, True, now=self.dashboard.sample_now)['state'] == '온라인')

    def sync_devices(self, preferred_id=None, force_selection=False, request_status=True):
        # Monitor selection initializes this window; later monitor ticks must not
        # override the PC explicitly chosen inside the exam window.
        if force_selection and preferred_id in self.pc_controls:
            self.pc_choice.set(preferred_id)
        if self.pc_choice.get() not in self.pc_controls:
            choices = [pc for pc in self.pc_controls if pc in self.dashboard.devices]
            self.pc_choice.set(preferred_id if preferred_id in self.pc_controls else (choices[0] if choices else 'PC-01'))
        pc_id = self.pc_choice.get()
        device = self.dashboard.devices.get(pc_id, dict(pc_id=pc_id, pc_name=pc_id, online=False))
        self.set_target(device, self.device_ready(pc_id), request_status=request_status)
        self.update_pc_controls()

    def update_pc_controls(self):
        for pc_id, controls in self.pc_controls.items():
            controls['state'].set(self.dashboard.exam_store.state(pc_id, self.dashboard.connected))
            ready = self.device_ready(pc_id) and self.pending is None and not self.closed
            for action in ('on', 'off', 'status'):
                controls[action].configure(state='normal' if ready else 'disabled')

    def submit_for_pc(self, pc_id, action):
        if self.pending is not None or not self.device_ready(pc_id):
            return
        self.pc_choice.set(pc_id)
        self.sync_devices(request_status=False)
        self.submit(action)

    def set_target(self, device, connected, request_status=True):
        self.display_pc_id = device.get('pc_id') if device else None
        target = device["pc_id"] if connected and device and device.get("online") else None
        session_id = device.get('agent_session_id') if device else None
        known = exam_mode(self.dashboard.exam_store.devices.get(self.display_pc_id, {}).get('exam_mode')) is not None
        if target != self.target:
            self.target = target
            self.generation += 1
            self.status.set("상태 미확인 · 상태를 새로고침하세요" if target else "연결 끊김 · 상태 미확인")
            self.needs_status = bool(target)
        elif target and self.last_mode_known and not known:
            self.needs_status = True
        if target and self.agent_session_id is not None and session_id != self.agent_session_id:
            self.generation += 1
            self.needs_status = True
        self.agent_session_id = session_id
        self.last_mode_known = known
        if request_status and self.needs_status and target and self.pending is None:
            self.needs_status = False
            self.submit("status")
        self.target_text.set(f"선택 PC: {self.display_pc_id}" + ('' if target else ' · 오프라인/데이터 지연'))
        self.refresh_history()

    def refresh_history(self):
        store = self.dashboard.exam_store
        latest = store.latest_foreground
        color = '#64748b' if latest is None else '#b91c1c' if latest['transition'] == 'left' else '#047857'
        self.foreground_notice.configure(foreground=color)
        self.monitor_text.set("현재 시험 상태: " + store.state(self.display_pc_id, self.dashboard.connected))
        self.foreground_text.set('Chrome 활성 창 감시: ' + store.foreground_state(self.display_pc_id, self.dashboard.connected))
        if self.history_revision == store.revision:
            return
        self.history_revision = store.revision
        selected = self.history_table.selection()
        position = self.history_table.yview()[0]
        for item in self.history_table.get_children():
            self.history_table.delete(item)
        for event in reversed(store.history):
            kind, name, pid = event_columns(event)
            self.history_table.insert('', 'end', iid=event['event_id'], values=(event['pc_id'], event['exam_id'], kind,
                name, pid, event['timestamp']))
        for event_id in selected:
            if self.history_table.exists(event_id):
                self.history_table.selection_add(event_id)
        self.history_table.yview_moveto(position)
        self.update_history_buttons()

    def update_history_buttons(self):
        self.delete_selected_button.configure(state='normal' if self.history_table.selection() else 'disabled')
        self.delete_all_button.configure(state='normal' if self.dashboard.exam_store.history else 'disabled')

    def delete_history(self, event_ids):
        self.dashboard.exam_store.remove_history(event_ids)
        self.dashboard.update_exam_warnings()
        self.refresh_history()

    def delete_selected_history(self):
        self.delete_history(self.history_table.selection())

    def delete_all_history(self):
        # Freeze the displayed IDs: events arriving during confirmation remain visible.
        event_ids = self.history_table.get_children()
        if event_ids and messagebox.askokcancel('전체 내역 지우기',
                f'현재 표시된 전체 PC 내역 {len(event_ids)}개를 이 GUI에서 지울까요?\n'
                '서버 저장 기록과 현재 시험·감시 상태는 유지됩니다.\n'
                '확인 중 새로 수신된 내역은 남습니다.', parent=self.window):
            self.delete_history(event_ids)

    def submit(self, action):
        if not self.target or self.pending is not None:
            return
        try:
            values = [value.get() for value in self.inputs]
            current = exam_mode(self.dashboard.exam_store.devices.get(self.target, {}).get('exam_mode'))
            if action == 'off' and current and current.get('exam_id'):
                values[0] = current['exam_id']
            request = exam_request(self.target, action, *values)
        except ValueError as exc:
            messagebox.showinfo("시험 설정 확인", str(exc), parent=self.window)
            return
        self.pending = request, self.generation
        self.needs_status = False
        self.status.set(f"{self.target} · 응답 대기 · 상태 미확인")
        self.client.submit(request)
        for button in self.buttons:
            button.configure(state="disabled")

    def tick(self):
        if self.closed:
            return
        try:
            request, result = self.client.results.get_nowait()
            if self.pending and request["command_id"] == self.pending[0]["command_id"]:
                generation = self.pending[1]
                self.pending = None
                if generation == self.generation and request["target_id"] == self.target:
                    mode = exam_mode(result.get("exam_mode"))
                    expected = ('EXAM_STATUS' if request['action'] == 'GET_EXAM_STATUS' else
                                'EXAM_MODE_ENABLED' if request['enabled'] else 'EXAM_MODE_DISABLED')
                    matches = (mode is not None and (request['action'] == 'GET_EXAM_STATUS' or
                        mode['enabled'] == request['enabled'] and mode['exam_id'] == request['exam_id']))
                    if result.get("ok") is True and result.get('code') == expected and matches:
                        status = MONITOR_LABELS[mode['monitor_status']]
                        self.status.set(f"명령 응답: {request['target_id']} · {mode.get('exam_id')} · {status} · 현재 상태는 서버 목록으로 동기화")
                        if not self.inputs[0].get().strip() and mode.get('exam_id'):
                            self.inputs[0].set(mode['exam_id'])
                    else:
                        self.status.set("결과 미확인 / 거절: " + str(result.get("code")) + " · 상태를 다시 조회하세요")
        except queue.Empty:
            pass
        self.sync_devices()
        self.refresh_history()
        self.timer = self.window.after(100, self.tick)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.window.after_cancel(self.timer)
        self.client.close()
        self.window.destroy()

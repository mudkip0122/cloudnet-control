"""Exam commands use the B v1 contract through the authenticated server."""
import math
import queue
import re
import tkinter as tk
from tkinter import ttk, messagebox

from admin_gui.process_control import ProcessClient, command_request
from admin_gui.exam_events import MONITOR_LABELS, exam_mode, event_columns


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
        self.window.geometry("1040x720")
        self.window.minsize(850, 650)
        self.history_revision = -1
        self.display_pc_id = None
        self.last_mode_known = False
        self.agent_session_id = None
        self.target_text = tk.StringVar()
        self.status = tk.StringVar(value="상태 미확인")
        ttk.Label(self.window, textvariable=self.target_text).pack(padx=12, pady=8)
        self.monitor_text = tk.StringVar(value="시험 상태 미확인")
        ttk.Label(self.window, textvariable=self.monitor_text).pack(padx=12, pady=4)
        self.foreground_text = tk.StringVar(value='활성 창 감시 미확인')
        ttk.Label(self.window, textvariable=self.foreground_text).pack(padx=12, pady=4)
        ttk.Label(self.window, textvariable=dashboard.exam_stream, wraplength=1000).pack(padx=12)
        ttk.Label(self.window, textvariable=dashboard.exam_warning, foreground="#b91c1c", wraplength=1000).pack(padx=12, pady=4)
        ttk.Label(self.window, textvariable=dashboard.foreground_warning, wraplength=1000).pack(padx=12, pady=4)
        self.inputs = []
        for title, default in (("시험 ID", ""), ("금지 실행 파일 (쉼표 구분 · 비우면 기본 목록, 입력하면 대체)", ""), ("검사 간격 (초)", "1")):
            ttk.Label(self.window, text=title).pack(anchor="w", padx=12)
            value = tk.StringVar(value=default)
            ttk.Entry(self.window, textvariable=value, width=65).pack(padx=12, pady=4)
            self.inputs.append(value)
        self.buttons = []
        actions = ttk.Frame(self.window)
        actions.pack(padx=12, pady=8)
        for action, title in (("on", "켜기"), ("off", "끄기"), ("status", "상태 새로고침")):
            button = ttk.Button(actions, text=title, command=lambda a=action: self.submit(a))
            button.pack(side='left', padx=4)
            self.buttons.append(button)
        ttk.Label(self.window, textvariable=self.status, wraplength=520).pack(padx=12, pady=10)
        ttk.Label(self.window, text="프로그램 이름·활성 창 전환 감지 · 브라우저 탭/URL 감시·자동 차단 없음 · 공백 전후 이탈 시간 계산 안 함").pack(padx=12, pady=8)
        ttk.Label(self.window, text="전체 PC 감지·감시 상태 이력 · 최근 500개 · 과거 감지는 현재 실행 여부나 부정행위 확정이 아님").pack(padx=12)
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
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.timer = self.window.after(100, self.tick)

    def set_target(self, device, connected):
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
        if self.needs_status and target and self.pending is None:
            self.needs_status = False
            self.submit("status")
        self.target_text.set(target or "온라인 PC를 선택하세요")
        for button in self.buttons:
            button.configure(state="normal" if target and self.pending is None else "disabled")
        self.refresh_history()

    def refresh_history(self):
        store = self.dashboard.exam_store
        self.monitor_text.set("현재 시험 상태: " + store.state(self.display_pc_id, self.dashboard.connected))
        self.foreground_text.set('Chrome 활성 창 감시: ' + store.foreground_state(self.display_pc_id, self.dashboard.connected))
        if self.history_revision == store.revision:
            return
        self.history_revision = store.revision
        for item in self.history_table.get_children():
            self.history_table.delete(item)
        for event in store.history:
            kind, name, pid = event_columns(event)
            self.history_table.insert('', 'end', values=(event['pc_id'], event['exam_id'], kind,
                name, pid, event['timestamp']))

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
        self.status.set("응답 대기 · 상태 미확인")
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
                        self.status.set(f"명령 응답: {mode.get('exam_id')} · {status} · 현재 상태는 서버 목록으로 동기화")
                        self.inputs[0].set(mode.get("exam_id") or "")
                    else:
                        self.status.set("결과 미확인 / 거절: " + str(result.get("code")) + " · 상태를 다시 조회하세요")
        except queue.Empty:
            pass
        self.refresh_history()
        self.timer = self.window.after(100, self.tick)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.window.after_cancel(self.timer)
        self.client.close()
        self.window.destroy()

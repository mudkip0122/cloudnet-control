"""Exam commands use the B v1 contract through the authenticated server."""
import math
import queue
import re
import tkinter as tk
from tkinter import ttk, messagebox

from admin_gui.process_control import ProcessClient, command_request


def exam_request(pc_id, action, exam_id="", names="", interval="1"):
    request = command_request(pc_id, "GET_EXAM_STATUS" if action == "status" else "SET_EXAM_MODE")
    if action == "status":
        return request
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", exam_id):
        raise ValueError("시험 ID는 영문·숫자·_ . - 1~64자입니다.")
    request.update(enabled=action == "on", exam_id=exam_id)
    if action == "on":
        entries = list(dict.fromkeys(name.strip().casefold() for name in names.split(",") if name.strip()))
        if not 1 <= len(entries) <= 100 or any(len(name) > 128 or any(c in name for c in '/\\*?') for name in entries):
            raise ValueError("금지 실행 파일 이름 1~100개를 쉼표로 구분하세요. 경로·와일드카드는 허용하지 않습니다.")
        value = float(interval)
        if not math.isfinite(value) or not .5 <= value <= 60:
            raise ValueError("검사 간격은 0.5~60초입니다.")
        request.update(forbidden_processes=entries, poll_interval=value)
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
        self.target_text = tk.StringVar()
        self.status = tk.StringVar(value="상태 미확인")
        ttk.Label(self.window, textvariable=self.target_text).pack(padx=12, pady=8)
        self.inputs = []
        for title, default in (("시험 ID", ""), ("금지 실행 파일 (쉼표 구분)", ""), ("검사 간격 (초)", "1")):
            ttk.Label(self.window, text=title).pack(anchor="w", padx=12)
            value = tk.StringVar(value=default)
            ttk.Entry(self.window, textvariable=value, width=65).pack(padx=12, pady=4)
            self.inputs.append(value)
        self.buttons = []
        for action, title in (("on", "켜기"), ("off", "끄기"), ("status", "상태 새로고침")):
            button = ttk.Button(self.window, text=title, command=lambda a=action: self.submit(a))
            button.pack(pady=4)
            self.buttons.append(button)
        ttk.Label(self.window, textvariable=self.status, wraplength=520).pack(padx=12, pady=10)
        ttk.Label(self.window, text="이름 기반 감지 · 브라우저 탭/URL 감시 및 실행 차단 기능 없음").pack(padx=12, pady=8)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.timer = self.window.after(100, self.tick)

    def set_target(self, device, connected):
        target = device["pc_id"] if connected and device and device.get("online") else None
        if target != self.target:
            self.target = target
            self.generation += 1
            self.status.set("상태 미확인 · 상태를 새로고침하세요" if target else "연결 끊김 · 상태 미확인")
            self.needs_status = bool(target)
        if self.needs_status and target and self.pending is None:
            self.needs_status = False
            self.submit("status")
        self.target_text.set(target or "온라인 PC를 선택하세요")
        for button in self.buttons:
            button.configure(state="normal" if target and self.pending is None else "disabled")

    def submit(self, action):
        if not self.target or self.pending is not None:
            return
        try:
            request = exam_request(self.target, action, *(value.get() for value in self.inputs))
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
                    mode = result.get("exam_mode")
                    if (result.get("ok") is True and result.get("code") in (
                            "EXAM_STATUS", "EXAM_MODE_ENABLED", "EXAM_MODE_DISABLED")
                            and isinstance(mode, dict) and type(mode.get("enabled")) is bool
                            and mode.get("monitor_status") in ("starting", "ok", "partial", "unavailable", "off")):
                        status = {"starting": "검사 시작 중", "ok": "정상 검사", "partial": "일부 확인 불가",
                                  "unavailable": "감시 불가", "off": "꺼짐"}.get(mode.get("monitor_status"), "상태 미확인")
                        self.status.set(f"{mode.get('exam_id')} · {status}")
                        self.inputs[0].set(mode.get("exam_id") or "")
                    else:
                        self.status.set("결과 미확인 / 거절: " + str(result.get("code")) + " · 상태를 다시 조회하세요")
        except queue.Empty:
            pass
        self.timer = self.window.after(100, self.tick)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.window.after_cancel(self.timer)
        self.client.close()
        self.window.destroy()

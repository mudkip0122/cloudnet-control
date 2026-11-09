"""Process inventory UI; all Tk operations stay on the GUI thread."""
import queue
import tkinter as tk
from tkinter import ttk, messagebox

from admin_gui.process_control import ProcessClient, command_request

PROTECTION_REASONS = {
    "AGENT_OR_SYSTEM_PID": "Agent·시스템·상위 프로세스",
    "PROTECTED_NAME": "보호된 프로그램 이름",
    "OTHER_USER": "다른 사용자 또는 소유자 확인 불가",
    "SYSTEM_OR_UNKNOWN_EXECUTABLE": "시스템 경로 또는 실행 파일 확인 불가",
    "CRITICAL_OR_UNKNOWN": "필수 프로세스 또는 필수 여부 확인 불가",
    "ACCESS_DENIED_OR_EXITED": "접근 거부 또는 이미 종료됨",
    "UNKNOWN_AGENT_IDENTITY": "Agent 사용자 정보 확인 불가",
    "UNKNOWN_IDENTITY": "생성 시각 확인 불가",
}

# Messages are kept local so server-provided prose cannot imply success.
RESULTS = {
    "TERMINATED": "프로세스 종료를 확인했습니다",
    "TERMINATION_REQUESTED": "종료 요청 전달 · 아직 종료 완료는 확인되지 않았습니다",
    "PROTECTED": "보호된 프로세스여서 종료할 수 없습니다",
    "PID_REUSED": "PID의 생성 시각이 변경되었습니다",
    "NOT_FOUND": "이미 종료된 프로세스입니다",
    "ACCESS_DENIED": "프로세스 접근 권한이 없습니다",
    "PROCESS_CONTROL_DISABLED": "Agent의 프로세스 제어가 비활성화되어 있습니다",
    "TARGET_OFFLINE": "대상 PC가 오프라인입니다",
    "TLS_REQUIRED": "TLS 관리자 인증 연결이 필요합니다",
    "TRANSPORT_ERROR": "연결 또는 응답 오류 · 종료 결과 불명확, 재조회 필요",
    "COMMAND_TIMEOUT": "명령 응답 시간 초과 · 종료 결과 불명확, 재조회 필요",
    "INVALID_PROCESS_RESPONSE": "프로세스 응답 형식 오류 · 전체 목록을 다시 조회하세요",
}


class ProcessWindow:
    def __init__(self, dashboard):
        self.dashboard = dashboard
        self.window = tk.Toplevel(dashboard.root)
        self.window.title("CloudNet Control — 프로세스")
        self.window.geometry("1120x520")
        self.window.minsize(850, 400)
        self.target = None
        self.online = False
        self.rows = {}
        self.complete = False
        self.pending = None
        self.closed = False
        self.client = None
        self.generation = 0
        self.pending_generation = None
        self.target_text = tk.StringVar(value="PC를 선택하세요")
        self.status = tk.StringVar(value="목록을 새로고침하세요")
        self.outcome = tk.StringVar(value="")
        ttk.Label(self.window, textvariable=self.target_text).pack(anchor="w", padx=12, pady=8)
        ttk.Label(self.window, textvariable=self.status).pack(anchor="w", padx=12)
        columns = ("pid", "name", "username", "memory", "can_stop", "protected_reason")
        container = ttk.Frame(self.window)
        container.pack(fill="both", expand=True, padx=12, pady=8)
        self.table = ttk.Treeview(container, columns=columns, show="headings", selectmode="browse")
        for key, title, width in zip(columns,
                ("PID", "이름", "사용자", "메모리 (MiB)", "종료 가능", "보호 이유"),
                (70, 180, 180, 110, 90, 260)):
            self.table.heading(key, text=title)
            self.table.column(key, width=width, minwidth=60)
        scroll = ttk.Scrollbar(container, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.table.pack(fill="both", expand=True)
        self.table.bind("<<TreeviewSelect>>", lambda event: self.update_buttons())
        actions = ttk.Frame(self.window)
        actions.pack(fill="x", padx=12, pady=8)
        self.refresh_button = ttk.Button(actions, text="새로고침", command=self.refresh)
        self.refresh_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="선택 프로세스 종료", command=self.stop_selected)
        self.stop_button.pack(side="left", padx=8)
        ttk.Label(self.window, textvariable=self.outcome, wraplength=1000).pack(anchor="w", padx=12, pady=8)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.timer = self.window.after(100, self.tick)

    def set_target(self, device, connected):
        target = (device["pc_id"], device["pc_name"]) if device else None
        online = bool(connected and device and device.get("online"))
        if target != self.target or online != self.online:
            self.generation += 1
            self.complete = False
            self.rows = {}
            for item in self.table.get_children():
                self.table.delete(item)
            self.status.set("대상 변경 · 전체 목록을 새로고침하세요" if online else "연결된 PC를 선택하세요")
            self.outcome.set("")
        self.target, self.online = target, online
        self.target_text.set(f"대상 PC: {target[1]} / {target[0]}" if target else "PC를 선택하세요")
        self.update_buttons()

    def authorized(self):
        return self.dashboard.context is not None and bool(self.dashboard.token)

    def update_buttons(self):
        ready = self.online and self.authorized() and self.pending is None and not self.closed
        self.refresh_button.configure(state="normal" if ready else "disabled")
        selected = self.table.selection()
        row = self.rows.get(selected[0]) if selected else None
        allowed = ready and self.complete and row and row.get("can_stop") is True
        self.stop_button.configure(state="normal" if allowed else "disabled")
        if not self.authorized():
            self.status.set("프로세스 조회·종료는 TLS 관리자 인증 연결에서 사용할 수 있습니다")

    def submit(self, request):
        if self.client is None:
            d = self.dashboard
            self.client = ProcessClient(d.host, d.port, d.context, d.token)
        self.pending = request
        self.pending_generation = self.generation
        self.client.submit(request)
        self.update_buttons()

    def refresh(self):
        if not self.online or not self.authorized() or self.pending is not None:
            return
        self.complete = False
        self.status.set("전체 프로세스 목록 수신 중 · 모든 페이지를 받은 뒤 표시합니다")
        self.submit(command_request(self.target[0], "LIST_PROCESSES"))

    def stop_selected(self):
        selected = self.table.selection()
        row = self.rows.get(selected[0]) if selected else None
        if (not self.online or not self.authorized() or not self.complete or self.pending is not None
                or not row or row.get("can_stop") is not True):
            return
        target, identity = self.target, dict(row)
        if not messagebox.askokcancel("프로세스 종료 확인",
                f"대상 PC: {target[1]}\nPC ID: {target[0]}\n"
                f"이름: {row.get('name') or '확인 불가'}\nPID: {row['pid']}\n"
                f"사용자: {row.get('username') or '확인 불가'}\n생성 시각: {row['create_time']}\n\n"
                "이 프로세스를 종료하시겠습니까? 저장하지 않은 내용은 사라질 수 있습니다.", parent=self.window):
            return
        # Tk's modal dialog still processes events: recheck target and identity.
        if (self.target != target or not self.online or not self.complete or self.pending is not None
                or self.rows.get(str(identity["pid"])) != identity):
            return
        self.complete = False
        self.status.set("프로세스 종료 결과 대기 중")
        self.submit(command_request(target[0], "STOP_PROCESS", identity))

    def tick(self):
        if self.closed:
            return
        if self.client is not None:
            try:
                request, result = self.client.results.get_nowait()
                self.handle_result(request, result)
            except queue.Empty:
                pass
        self.timer = self.window.after(100, self.tick)

    def handle_result(self, request, result):
        if self.pending is None or request["command_id"] != self.pending["command_id"]:
            return
        stale = self.pending_generation != self.generation
        self.pending = None
        if stale or self.target is None or request["target_id"] != self.target[0] or not self.online:
            self.update_buttons()
            return
        if request["action"] == "LIST_PROCESSES":
            if result.get("ok") is True and result.get("code") == "PROCESS_LIST":
                self.rows = {str(row["pid"]): row for row in result["processes"]}
                for item in self.table.get_children():
                    self.table.delete(item)
                for key, row in self.rows.items():
                    memory = row.get("memory_bytes")
                    self.table.insert("", "end", iid=key, values=(row["pid"], row.get("name") or "확인 불가",
                        row.get("username") or "확인 불가", f"{memory / 1048576:.2f}" if memory is not None else "확인 불가",
                        "가능" if row["can_stop"] else "불가",
                        PROTECTION_REASONS.get(row.get("protected_reason"), row.get("protected_reason") or "—")))
                self.complete = True
                self.status.set(f"전체 목록 수신 완료 · {len(self.rows)}개" if self.rows else "프로세스 데이터 없음")
            else:
                self.complete = False
                self.status.set("목록 수신 실패 · 불완전하거나 이전 목록으로는 종료할 수 없습니다")
                self.outcome.set(RESULTS.get(result.get("code"), "조회 실패: " + str(result.get("code"))))
        else:
            code = result.get("code")
            self.outcome.set(RESULTS.get(code, "종료 결과: " + str(code)))
            # Every terminal stop result triggers a new inventory, even rejection.
            self.refresh()
        self.update_buttons()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.window.after_cancel(self.timer)
        if self.client is not None:
            self.client.close()
        self.finish_close()

    def finish_close(self):
        if self.client is not None and self.client.worker.is_alive():
            self.window.after(50, self.finish_close)
        else:
            self.window.destroy()

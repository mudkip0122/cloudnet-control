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
    "PROCESS_ERROR": "운영체제에서 프로세스를 처리하는 중 오류가 발생했습니다",
    "INVALID_IDENTITY": "PID 또는 생성 시각 정보가 올바르지 않습니다",
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
        self.sort_column = "pid"
        self.sort_descending = False
        self.column_titles = {}
        self.last_stop = None
        self.groups = {}
        self.grouped = tk.BooleanVar(value=False)
        self.target_text = tk.StringVar(value="PC를 선택하세요")
        self.status = tk.StringVar(value="목록을 새로고침하세요")
        self.outcome = tk.StringVar(value="")
        ttk.Label(self.window, textvariable=self.target_text).pack(anchor="w", padx=12, pady=8)
        ttk.Label(self.window, textvariable=self.status).pack(anchor="w", padx=12)
        columns = ("pid", "name", "username", "memory", "can_stop", "protected_reason", "create_time")
        container = ttk.Frame(self.window)
        container.pack(fill="both", expand=True, padx=12, pady=8)
        self.table = ttk.Treeview(container, columns=columns, show="tree headings", selectmode="extended")
        self.table.heading("#0", text="그룹 / 개수")
        self.table.column("#0", width=170)
        for key, title, width in zip(columns,
                ("PID", "이름", "사용자", "메모리 (MiB)", "종료 가능", "보호 이유", "생성 시각 (Unix 초)"),
                (70, 180, 180, 110, 90, 260, 150)):
            self.column_titles[key] = title
            self.table.heading(key, text=title, command=lambda column=key: self.sort_by(column))
            self.table.column(key, width=width, minwidth=60)
        scroll = ttk.Scrollbar(container, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.table.pack(fill="both", expand=True)
        self.table.bind("<<TreeviewSelect>>", lambda event: self.update_buttons())
        ttk.Label(self.window, text="열 제목 클릭: 오름차순 / 내림차순 · 종료는 선택한 PID 하나에만 적용됩니다",
                  wraplength=1000).pack(anchor="w", padx=12)
        actions = ttk.Frame(self.window)
        actions.pack(fill="x", padx=12, pady=8)
        self.refresh_button = ttk.Button(actions, text="새로고침", command=self.refresh)
        self.refresh_button.pack(side="left")
        self.stop_button = ttk.Button(actions, text="선택 프로세스 종료", command=self.stop_selected)
        self.stop_button.pack(side="left", padx=8)
        ttk.Checkbutton(actions, text="같은 이름 그룹", variable=self.grouped,
                        command=self.render_rows).pack(side="left", padx=8)
        self.batch_button = ttk.Button(actions, text="선택 그룹·PID 일괄 종료", command=self.stop_batch)
        self.batch_button.pack(side="left", padx=8)
        self.batch_results = tk.Text(self.window, height=5, state="disabled")
        self.batch_results.pack(fill="x", padx=12)
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
            self.last_stop = None
            self.batch_results.configure(state="normal")
            self.batch_results.delete("1.0", "end")
            self.batch_results.configure(state="disabled")
        self.target, self.online = target, online
        self.target_text.set(f"대상 PC: {target[1]} / {target[0]}" if target else "PC를 선택하세요")
        self.update_buttons()

    def authorized(self):
        return self.dashboard.context is not None and bool(self.dashboard.token)

    def sort_by(self, column):
        if column not in self.column_titles:
            return
        self.sort_descending = not self.sort_descending if self.sort_column == column else False
        self.sort_column = column
        self.apply_sort()

    def apply_sort(self):
        if self.grouped.get():
            return
        def value(key):
            row = self.rows[key]
            if self.sort_column == "memory":
                return row.get("memory_bytes")
            if self.sort_column == "protected_reason":
                reason = row.get("protected_reason")
                return PROTECTION_REASONS.get(reason, reason)
            return row.get(self.sort_column)

        keys = list(self.table.get_children())
        available = [key for key in keys if value(key) is not None and value(key) != ""]
        missing = [key for key in keys if value(key) is None or value(key) == ""]
        # PID is a stable tie breaker; missing values stay at the end in both directions.
        available.sort(key=lambda key: self.rows[key]["pid"])
        available.sort(key=lambda key: value(key).casefold() if isinstance(value(key), str) else value(key),
                       reverse=self.sort_descending)
        missing.sort(key=lambda key: self.rows[key]["pid"])
        for position, key in enumerate(available + missing):
            self.table.move(key, "", position)
        for column, title in self.column_titles.items():
            arrow = (" ▼" if self.sort_descending else " ▲") if column == self.sort_column else ""
            self.table.heading(column, text=title + arrow)

    def update_buttons(self):
        ready = self.online and self.authorized() and self.pending is None and not self.closed
        self.refresh_button.configure(state="normal" if ready else "disabled")
        selected = self.table.selection()
        row = self.rows.get(selected[0]) if selected else None
        allowed = ready and self.complete and len(selected) == 1 and row and row.get("can_stop") is True
        self.stop_button.configure(state="normal" if allowed else "disabled")
        candidates = self.selected_rows()
        self.batch_button.configure(state="normal" if ready and self.complete and any(
            row.get("can_stop") is True for row in candidates) else "disabled")
        if not self.authorized():
            self.status.set("프로세스 조회·종료는 TLS 관리자 인증 연결에서 사용할 수 있습니다")

    def submit(self, request):
        if self.client is None:
            d = self.dashboard
            self.client = ProcessClient(d.host, d.port, d.context, d.token,
                                        server_name=getattr(d, "server_name", None))
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

    def render_rows(self):
        for item in self.table.get_children():
            self.table.delete(item)
        self.groups = {}
        for key, row in self.rows.items():
            parent = ""
            name = row.get("name")
            if self.grouped.get() and name:
                parent = "group:" + name.casefold()
                if parent not in self.groups:
                    self.groups[parent] = []
                    self.table.insert("", "end", iid=parent, text=name)
                self.groups[parent].append(key)
            memory = row.get("memory_bytes")
            self.table.insert(parent, "end", iid=key, values=(row["pid"], name or "확인 불가",
                row.get("username") or "확인 불가", f"{memory / 1048576:.2f}" if memory is not None else "확인 불가",
                "가능" if row["can_stop"] else "불가",
                PROTECTION_REASONS.get(row.get("protected_reason"), row.get("protected_reason") or "—"), row.get("create_time") or "확인 불가"))
        for group, keys in self.groups.items():
            memory = [self.rows[key].get("memory_bytes") for key in keys]
            total = "확인 불가" if any(value is None for value in memory) else f"{sum(memory) / 1048576:.2f}"
            self.table.item(group, text=f"{self.rows[keys[0]]['name']} ({len(keys)}개)", values=("", "", "", total))
        self.apply_sort()
        self.update_buttons()

    def selected_rows(self):
        keys = set()
        for key in self.table.selection():
            keys.update(self.groups.get(key, [key]))
        return [dict(self.rows[key]) for key in sorted(keys) if key in self.rows]

    def stop_batch(self):
        if not self.online or not self.authorized() or not self.complete or self.pending is not None:
            return
        rows, target, generation = self.selected_rows(), self.target, self.generation
        try:
            request = command_request(target[0], "STOP_PROCESSES", rows)
        except ValueError as exc:
            messagebox.showinfo("일괄 종료", str(exc), parent=self.window)
            return
        names = ", ".join(sorted({row.get("name") or "확인 불가" for row in rows}))
        pids = ", ".join(str(item["pid"]) for item in request["targets"])
        if not messagebox.askokcancel("일괄 종료 확인",
                f"PC: {target[1]} / {target[0]}\n그룹: {names}\nPID: {pids}\n"
                f"종료 대상 {len(request['targets'])}개 · 제외 보호 항목 {len(rows) - len(request['targets'])}개\n"
                "같은 이름이어도 경로·사용자·프로세스 트리는 다를 수 있습니다.\n"
                "강제 종료 시 미저장 내용이 사라지고 브라우저 시험 창도 닫힐 수 있습니다.", parent=self.window):
            return
        if (self.target != target or self.generation != generation or not self.online
                or not self.complete or self.pending is not None
                or any(self.rows.get(str(row['pid'])) != row for row in rows)):
            return
        # Only the identities shown in the confirmation are submitted.
        self.complete = False
        self.status.set("일괄 종료 결과 대기 중")
        self.submit(request)

    def stop_selected(self):
        selected = self.table.selection()
        row = self.rows.get(selected[0]) if selected else None
        if (not self.online or not self.authorized() or not self.complete or self.pending is not None
                or len(selected) != 1 or not row or row.get("can_stop") is not True):
            return
        target, identity = self.target, dict(row)
        if not messagebox.askokcancel("프로세스 종료 확인",
                f"대상 PC: {target[1]}\nPC ID: {target[0]}\n"
                f"이름: {row.get('name') or '확인 불가'}\nPID: {row['pid']}\n"
                f"사용자: {row.get('username') or '확인 불가'}\n생성 시각: {row['create_time']}\n\n"
                "선택한 PID 하나를 종료합니다. 같은 프로그램의 다른 프로세스와 창은 남을 수 있습니다.\n"
                "Chrome 같은 프로그램은 보조 프로세스를 다시 실행하거나 오류 창을 표시할 수 있습니다.\n"
                "저장하지 않은 내용은 사라질 수 있습니다. 종료하시겠습니까?", parent=self.window):
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
                selected = self.table.selection()
                previous = self.rows.get(selected[0]) if selected else None
                self.rows = {str(row["pid"]): row for row in result["processes"]}
                for item in self.table.get_children():
                    self.table.delete(item)
                for key, row in self.rows.items():
                    memory = row.get("memory_bytes")
                    self.table.insert("", "end", iid=key, values=(row["pid"], row.get("name") or "확인 불가",
                        row.get("username") or "확인 불가", f"{memory / 1048576:.2f}" if memory is not None else "확인 불가",
                        "가능" if row["can_stop"] else "불가",
                        PROTECTION_REASONS.get(row.get("protected_reason"), row.get("protected_reason") or "—"), row.get("create_time") or "확인 불가"))
                self.apply_sort()
                if self.grouped.get():
                    self.render_rows()
                if previous is not None:
                    current = self.rows.get(str(previous["pid"]))
                    if current is not None and current.get("create_time") == previous.get("create_time"):
                        self.table.selection_set(str(previous["pid"]))
                self.complete = True
                self.status.set(f"전체 목록 수신 완료 · {len(self.rows)}개" if self.rows else "프로세스 데이터 없음")
                self.show_stop_observation()
            else:
                self.complete = False
                self.status.set("목록 수신 실패 · 불완전하거나 이전 목록으로는 종료할 수 없습니다")
                self.outcome.set(RESULTS.get(result.get("code"), "조회 실패: " + str(result.get("code"))))
        elif request["action"] == "STOP_PROCESSES":
            text = "결과 미확인 · " + str(result.get("code"))
            if result.get("code") == "PROCESS_BATCH_RESULT" and result.get("ok") is True:
                items = result.get("results")
                items = items if isinstance(items, list) else []
                expected = [(x['pid'], x['create_time']) for x in request['targets']]
                actual = [(x.get('pid'), x.get('create_time')) for x in items if isinstance(x, dict)]
                if actual == expected and len(items) == len(expected):
                    text = "처리 결과 (전체 종료 성공을 뜻하지 않음): " + str(result.get("counts")) + "\n"
                    text += "\n".join(f"PID {x['pid']} · {x.get('code')} · {RESULTS.get(x.get('code'), x.get('reason', '확인 필요'))}" for x in items)
                else:
                    text = "결과 대상 불일치 · 결과 미확인"
            self.batch_results.configure(state="normal")
            self.batch_results.delete("1.0", "end")
            self.batch_results.insert("1.0", text)
            self.batch_results.configure(state="disabled")
            self.refresh()
        else:
            code = result.get("code")
            self.last_stop = {"request": request, "code": code, "ok": result.get("ok") is True}
            self.outcome.set(f"PID {request['pid']} · {code} · " + RESULTS.get(code, "알 수 없는 종료 결과"))
            # Every terminal stop result triggers a new inventory, even rejection.
            self.refresh()
        self.update_buttons()

    def show_stop_observation(self):
        if self.last_stop is None:
            return
        request, code = self.last_stop["request"], self.last_stop["code"]
        current = self.rows.get(str(request["pid"]))
        text = f"PID {request['pid']} · {code} · " + RESULTS.get(code, "알 수 없는 종료 결과")
        if current is None or current.get("create_time") != request["create_time"]:
            text += " · 재조회: 선택한 프로세스가 목록에 없습니다"
        else:
            text += " · 재조회: 같은 PID·생성 시각이 아직 목록에 있습니다"
        if code in ("TERMINATED", "TERMINATION_REQUESTED"):
            text += " · 프로그램의 다른 프로세스나 창은 남아 있을 수 있습니다"
        self.outcome.set(text)

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

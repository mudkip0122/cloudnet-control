"""Historical metrics and server-produced alert transitions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk

from admin_gui.charts import FIELDS, LiveCharts
from admin_gui.exam_events import event_columns
from admin_gui.history_query import (
    ALERT_LABELS, LOCAL_TIME_FORMAT, history_request, query_audit_history, query_history, sample_history,
)
from admin_gui.process_control import POWER_ACTIONS
from admin_gui.theme import header, window


POWER_STAGES = {
    "request_accepted": "서버 요청 접수", "partial_result": "중간 결과",
    "result": "최종 결과", "timeout": "시간 초과", "boot_changed": "재부팅 확인",
    "server_restart_unconfirmed": "서버 재시작 · 결과 미확인",
}
POWER_RESULTS = {
    "POWER_ACCEPTED": "접수", "POWER_SIMULATED": "모의 실행 완료",
    "POWER_EXECUTION_STARTED": "실행 시작", "POWER_EXECUTION_FAILED": "실행 실패",
    "POWER_EXECUTION_UNKNOWN": "실행 결과 미확인", "COMMAND_TIMEOUT": "시간 초과",
}
RANGE_PRESETS = (("최근 10분", 10), ("최근 1시간", 60), ("직접 지정", None))


class HistoryWindow:
    def __init__(self, dashboard, selected_pc):
        title = "PC 이력 · 경고" + (" · 샘플" if dashboard.sample else "")
        self.window = window(dashboard.root, title, "1000x780", (820, 620))
        header(self.window, title)
        self.closed = False
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.future = None
        self.kind = None
        self.power_rows = {}
        self.source = (dashboard.host, dashboard.port, dashboard.context, dashboard.token, dashboard.server_name)
        self.sample = dashboard.sample
        controls = ttk.Frame(self.window, style="Card.TFrame", padding=10)
        controls.pack(fill="x", padx=12, pady=10)
        self.pc = tk.StringVar(value=selected_pc or "")
        self.pcs = ttk.Combobox(controls, textvariable=self.pc, width=20)
        self.pcs.configure(values=tuple(sorted(dashboard.devices)))
        ttk.Label(controls, text="PC ID", style="Surface.TLabel").pack(side="left")
        self.pcs.pack(side="left", padx=8)
        self.range = ttk.Combobox(controls, values=tuple(label for label, _ in RANGE_PRESETS),
                                  state="readonly", width=12)
        self.range.current(0)
        self.range.pack(side="left", padx=8)
        self.range.bind("<<ComboboxSelected>>", self.range_changed)
        self.pcs.bind("<<ComboboxSelected>>", self.submit)
        self.pcs.bind("<Return>", self.submit)
        ttk.Button(controls, text="조회", style="Primary.TButton", command=self.submit).pack(side="left", padx=8)
        self.search = tk.StringVar()
        self.search_count = tk.StringVar()
        ttk.Label(controls, text="표 검색", style="Surface.TLabel").pack(side="left", padx=(8, 0))
        search_entry = ttk.Entry(controls, textvariable=self.search, width=18)
        search_entry.pack(side="left", fill="x", expand=True, padx=8)
        search_entry.bind("<Escape>", lambda _event: self.search.set(""))
        ttk.Button(controls, text="지우기", command=lambda: self.search.set("")).pack(side="left")
        ttk.Label(controls, textvariable=self.search_count, style="Surface.TLabel").pack(side="left", padx=(8, 0))
        self.custom_range = ttk.Frame(self.window, style="Card.TFrame", padding=8)
        self.start_at, self.end_at = tk.StringVar(), tk.StringVar()
        for label, value in (("시작", self.start_at), ("종료", self.end_at)):
            ttk.Label(self.custom_range, text=label, style="Surface.TLabel").pack(side="left")
            entry = ttk.Entry(self.custom_range, textvariable=value, width=20)
            entry.pack(side="left", padx=(6, 14))
            entry.bind("<Return>", self.submit)
            value.trace_add("write", self.custom_range_edited)
        ttk.Label(self.custom_range, text="이 PC의 현지 시각 · YYYY-MM-DD HH:MM",
                  style="Surface.TLabel").pack(side="left")
        self.status = tk.StringVar(value="PC ID를 선택하거나 입력하세요.")
        self.status_label = ttk.Label(self.window, textvariable=self.status, style="Muted.TLabel")
        self.status_label.pack(anchor="w", padx=16, pady=(0, 5))
        self.notebook = ttk.Notebook(self.window, style="Fixed.TNotebook")
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)
        self.metrics = self.make_table(self.notebook, "측정 기록",
                                       ("시각 (UTC)", "CPU %", "RAM %", "업로드 Mbps", "다운로드 Mbps"))
        self.alerts = self.make_table(self.notebook, "경고 이력", ("시각 (UTC)", "경고 종류", "상태"))
        self.exam = self.make_table(self.notebook, "시험 이력",
                                    ("발생 시각 (UTC)", "서버 수신 (UTC)", "시험 ID", "이벤트", "상세", "PID"))
        self.power = self.make_table(self.notebook, "명령 감사",
                                     ("요청 시각 (UTC)", "대상 PC", "작업", "모드", "결과", "명령 ID"))
        self.power_detail = ttk.Label(self.power.master, justify="left", wraplength=740, style="Muted.TLabel",
                                      text="명령을 선택하면 단계별 시각과 재부팅 확인 시각을 볼 수 있습니다.")
        self.power_detail.pack(side="bottom", fill="x", padx=8, pady=6, before=self.power)
        self.power.bind("<<TreeviewSelect>>", self.show_power_detail)
        self.chart_caption = ttk.Label(self.window, text="측정 이력 · 서버 수신 시각 (UTC)", style="Muted.TLabel")
        self.chart_caption.pack(anchor="w", padx=16)
        self.chart = LiveCharts(self.window, historical=True)
        self.chart.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.tables = {str(table.master): table for table in (self.metrics, self.alerts, self.exam, self.power)}
        self.search_rows = {table: {} for table in self.tables.values()}
        self.search.trace_add("write", self.filter_rows)
        self.notebook.bind("<<NotebookTabChanged>>", self.tab_changed)
        self.pc.trace_add("write", self.pc_changed)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.timer = self.window.after(100, self.tick)
        if selected_pc:
            self.submit()

    @staticmethod
    def make_table(notebook, title, headings):
        frame = ttk.Frame(notebook)
        notebook.add(frame, text=title)
        columns = tuple(f"column_{index}" for index in range(len(headings)))
        table = ttk.Treeview(frame, columns=columns, show="headings", height=6)
        for column, heading in zip(columns, headings):
            table.heading(column, text=heading)
            table.column(column, width=210 if column == columns[0] else 120, anchor="center")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=table.xview)
        table.configure(xscrollcommand=horizontal.set)
        horizontal.pack(side="bottom", fill="x")
        table.pack(fill="both", expand=True)
        table.tag_configure("raised", foreground="#b91c1c")
        table.tag_configure("resolved", foreground="#047857")
        return table

    def reset(self, label, pc_id=None):
        self.status.set(label)
        self.chart.show((), label, pc_id)
        self.power_rows.clear()
        for table, rows in self.search_rows.items():
            table.delete(*rows)
            rows.clear()
        self.show_power_detail()
        self.filter_rows()

    def insert_row(self, table, values, *, tags=(), extra=""):
        item = table.insert("", "end", values=values, tags=tags)
        self.search_rows[table][item] = " ".join([*(str(value) for value in values), extra]).casefold()
        return item

    def filter_rows(self, *_):
        table = self.tables.get(self.notebook.select())
        if table is None:
            return
        terms = self.search.get().casefold().split()
        rows = self.search_rows[table]
        visible = tuple(item for item, text in rows.items() if all(term in text for term in terms))
        selected = set(table.selection()) - set(visible)
        if selected:
            table.selection_remove(*selected)
        table.set_children("", *visible)
        self.search_count.set(f"{len(visible)} / {len(rows)}건")
        if table is self.power:
            self.show_power_detail()

    def invalidate(self):
        if self.future is not None:
            self.future.cancel()
            self.future = None

    def set_choices(self, devices):
        self.pcs.configure(values=tuple(sorted(set(self.pcs.cget("values")) | devices.keys())))

    def pc_changed(self, *_):
        self.invalidate()
        self.reset("PC ID를 선택한 뒤 조회하세요.")

    def tab_changed(self, _event=None):
        kind = {str(self.exam.master): "exam", str(self.power.master): "power"}.get(self.notebook.select())
        if kind == self.kind:
            self.filter_rows()
            return
        self.kind = kind
        if kind is None:
            self.chart_caption.pack(anchor="w", padx=16)
            self.chart.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        else:
            self.chart.pack_forget()
            self.chart_caption.pack_forget()
        self.submit()

    def range_changed(self, _event=None):
        if RANGE_PRESETS[self.range.current()][1] is not None:
            self.custom_range.pack_forget()
            self.submit()
            return
        if not self.start_at.get() and not self.end_at.get():
            now = datetime.now()
            self.start_at.set((now - timedelta(hours=1)).strftime(LOCAL_TIME_FORMAT))
            self.end_at.set(now.strftime(LOCAL_TIME_FORMAT))
        self.custom_range.pack(fill="x", before=self.status_label, padx=12, pady=(0, 8))
        self.custom_range_edited()

    def custom_range_edited(self, *_):
        self.invalidate()
        self.reset("시작·종료 시각을 지정한 뒤 조회하세요.")

    def submit(self, _event=None):
        self.invalidate()
        try:
            minutes = RANGE_PRESETS[self.range.current()][1]
            request = history_request(self.pc.get(), minutes, start_at=self.start_at.get(), end_at=self.end_at.get())
        except ValueError as exc:
            self.reset(str(exc))
            return
        self.reset("조회 중…", request["pc_id"])
        if self.kind is not None:
            request.update(type="GET_AUDIT_HISTORY", kind=self.kind)
            if self.sample:
                self.reset("샘플 모드 · 서버에 저장된 감사 이력은 운영 연결에서 조회합니다.")
            else:
                self.future = self.executor.submit(query_audit_history, *self.source, request)
        elif self.sample:
            self.future = self.executor.submit(sample_history, request)
        else:
            self.future = self.executor.submit(query_history, *self.source, request)

    def tick(self):
        # Only the current Future can update the view, even if a previous
        # request finishes after the PC or range changed.
        future = self.future
        if future is not None and future.done():
            self.future = None
            try:
                result = future.result()
            except ValueError as exc:
                self.reset(f"조회 실패 · {exc}")
            except TimeoutError:
                self.reset("조회 실패 · 서버 응답 시간 초과")
            except (OSError, EOFError, OverflowError, RecursionError):
                self.reset("조회 실패 · 연결 또는 서버 응답 오류")
            else:
                if self.kind:
                    self.render_audit(result)
                else:
                    self.render(*result)
        self.timer = self.window.after(100, self.tick)

    def render(self, metrics, alerts):
        label = f"측정 {len(metrics)}건 · 경고 전환 {len(alerts)}건" if metrics or alerts else "조회 결과 없음"
        self.status.set(label)
        self.chart.show([(stamp.timestamp(), values) for stamp, values in metrics],
                        label if metrics else "측정 기록 없음", self.pc.get().strip())
        for stamp, values in reversed(metrics):
            self.insert_row(self.metrics, (stamp.isoformat(), *(
                "—" if values[key] is None else f"{values[key]:.2f}" for key in FIELDS)))
        for stamp, kind, state in alerts:
            label = "경고 발생" if state == "raised" else "정상 복귀"
            self.insert_row(self.alerts, (stamp.isoformat(), ALERT_LABELS[kind], label), tags=(state,))
        self.filter_rows()

    def render_audit(self, rows):
        self.status.set(f"감사 기록 {len(rows)}건" if rows else "조회 결과 없음")
        if self.kind == "exam":
            for row in reversed(rows):
                event = row["event"]
                kind, detail, pid = event_columns(event)
                self.insert_row(self.exam, (event["timestamp"], row["received_at"].isoformat(),
                                            event["exam_id"], kind, detail, pid))
        else:
            for row in reversed(rows):
                mode = {"simulate": "모의 실행", "execute": "실제 실행"}.get(row["power_control_mode"], "—")
                code = row["result_code"] or row["outcome"]
                result = "재부팅 확인" if row["reboot_confirmed_at"] else POWER_RESULTS.get(code, code)
                codes = " ".join(code for _, _, code in row["stages"] if code)
                item = self.insert_row(self.power, (row["requested_at"].isoformat(), row["pc_id"],
                                       POWER_ACTIONS[row["action"]], mode, result, row["command_id"]), extra=codes)
                self.power_rows[item] = row
        self.filter_rows()

    def show_power_detail(self, _event=None):
        selection = self.power.selection()
        row = self.power_rows.get(selection[0]) if selection else None
        if row is None:
            text = "명령을 선택하면 단계별 시각과 재부팅 확인 시각을 볼 수 있습니다."
        else:
            stages = [f"{stamp.isoformat()}  {POWER_STAGES.get(stage, stage)}" + (f" · {code}" if code else "")
                      for stamp, stage, code in row["stages"]]
            result = row["result_received_at"]
            reboot = row["reboot_confirmed_at"]
            summary = f"최종 결과 수신  {result.isoformat() if result else '—'}   ·   재부팅 확인  {reboot.isoformat() if reboot else '—'}"
            text = "\n".join((summary, *stages)) if stages else summary + "\n저장된 단계별 기록 없음"
        self.power_detail.configure(text=text)

    def close(self):
        self.closed = True
        self.invalidate()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.window.after_cancel(self.timer)
        self.window.destroy()

"""Historical metrics and server-produced alert transitions."""
from concurrent.futures import ThreadPoolExecutor
import tkinter as tk
from tkinter import ttk

from admin_gui.charts import FIELDS, LiveCharts
from admin_gui.history_query import ALERT_LABELS, history_request, query_history, sample_history


class HistoryWindow:
    def __init__(self, dashboard, selected_pc):
        self.window = tk.Toplevel(dashboard.root)
        self.window.title("PC 이력 · 경고" + (" · 샘플" if dashboard.sample else ""))
        self.window.geometry("1000x780")
        self.window.minsize(820, 620)
        self.closed = False
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.future = None
        self.source = (dashboard.host, dashboard.port, dashboard.context, dashboard.token, dashboard.server_name)
        self.sample = dashboard.sample
        controls = ttk.Frame(self.window, padding=10)
        controls.pack(fill="x")
        self.pc = tk.StringVar(value=selected_pc or "")
        self.pcs = ttk.Combobox(controls, textvariable=self.pc, width=20)
        self.pcs.configure(values=tuple(sorted(dashboard.devices)))
        ttk.Label(controls, text="PC ID").pack(side="left")
        self.pcs.pack(side="left", padx=8)
        self.range = ttk.Combobox(controls, values=("최근 10분", "최근 1시간"), state="readonly", width=12)
        self.range.current(0)
        self.range.pack(side="left", padx=8)
        self.range.bind("<<ComboboxSelected>>", self.submit)
        self.pcs.bind("<<ComboboxSelected>>", self.submit)
        self.pcs.bind("<Return>", self.submit)
        ttk.Button(controls, text="조회", command=self.submit).pack(side="left", padx=8)
        self.status = tk.StringVar(value="PC ID를 선택하거나 입력하세요.")
        ttk.Label(controls, textvariable=self.status).pack(side="left", padx=8)
        self.chart = LiveCharts(self.window)
        self.chart.configure(text="측정 이력 · 서버 수신 시각 (UTC)")
        self.chart.pack(fill="both", expand=True, padx=10)
        notebook = ttk.Notebook(self.window)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)
        self.metrics = self.make_table(notebook, "측정 기록", ("시각 (UTC)", "CPU %", "RAM %", "업로드 Mbps", "다운로드 Mbps"))
        self.alerts = self.make_table(notebook, "경고 이력", ("시각 (UTC)", "경고 종류", "상태"))
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
        table.pack(fill="both", expand=True)
        table.tag_configure("raised", foreground="#b91c1c")
        table.tag_configure("resolved", foreground="#047857")
        return table

    def reset(self, label, pc_id=None):
        self.status.set(label)
        self.chart.show((), label, pc_id)
        for table in (self.metrics, self.alerts):
            table.delete(*table.get_children())

    def invalidate(self):
        if self.future is not None:
            self.future.cancel()
            self.future = None

    def set_choices(self, devices):
        self.pcs.configure(values=tuple(sorted(set(self.pcs.cget("values")) | devices.keys())))

    def pc_changed(self, *_):
        self.invalidate()
        self.reset("PC ID를 선택한 뒤 조회하세요.")

    def submit(self, _event=None):
        self.invalidate()
        try:
            request = history_request(self.pc.get(), (10, 60)[self.range.current()])
        except ValueError as exc:
            self.reset(str(exc))
            return
        self.reset("조회 중…", request["pc_id"])
        if self.sample:
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
                metrics, alerts = future.result()
            except ValueError as exc:
                self.reset(f"조회 실패 · {exc}")
            except (OSError, EOFError, OverflowError, RecursionError):
                self.reset("조회 실패 · 연결 또는 서버 응답 오류")
            else:
                self.render(metrics, alerts)
        self.timer = self.window.after(100, self.tick)

    def render(self, metrics, alerts):
        label = f"측정 {len(metrics)}건 · 경고 전환 {len(alerts)}건" if metrics or alerts else "조회 결과 없음"
        self.status.set(label)
        self.chart.show([(stamp.timestamp(), values) for stamp, values in metrics],
                        label if metrics else "측정 기록 없음", self.pc.get().strip())
        for stamp, values in reversed(metrics):
            self.metrics.insert("", "end", values=(stamp.isoformat(), *(
                "—" if values[key] is None else f"{values[key]:.2f}" for key in FIELDS)))
        for stamp, kind, state in alerts:
            label = "경고 발생" if state == "raised" else "정상 복귀"
            self.alerts.insert("", "end", values=(stamp.isoformat(), ALERT_LABELS[kind], label), tags=(state,))

    def close(self):
        self.closed = True
        self.invalidate()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.window.after_cancel(self.timer)
        self.window.destroy()

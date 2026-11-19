"""Live alert subscription state and presentation."""
from collections import OrderedDict, deque
from tkinter import ttk

from admin_gui.charts import metric_value
from admin_gui.history_query import ALERT_LABELS
from admin_gui.presentation import device_view
from admin_gui.theme import header, window


def alert_key(row):
    return row["pc_id"], row["kind"], row["alert_id"]


class LiveAlerts:
    def __init__(self):
        self.open = {}
        self.recent = deque(maxlen=100)
        self.seen = OrderedDict()
        self.state = "연결 대기"

    def restore(self, rows):
        self.open = {alert_key(row): row for row in rows}
        for key in self.open:
            self.remember((*key, "raised"))
        self.state = "실시간 연결됨"

    def remember(self, key):
        self.seen[key] = None
        if len(self.seen) > 4096:
            self.seen.popitem(last=False)

    def accept(self, row):
        key = alert_key(row)
        transition = (*key, row["state"])
        if transition in self.seen:
            return False
        self.remember(transition)
        if row["state"] == "raised":
            self.open[key] = row
        else:
            self.open.pop(key, None)
        self.recent.appendleft(row)
        return True

    def observation(self, row, devices):
        if self.state != "실시간 연결됨":
            return "감시 공백 · 현재 상태 미확인"
        if row["kind"] == "DEVICE_OFFLINE":
            return "오프라인"
        if row["pc_id"] not in devices:
            return "현재 상태 미확인"
        device = devices[row["pc_id"]]
        field = "cpu_percent" if row["kind"] == "CPU_HIGH" else "ram_percent"
        if (device_view(device)["state"] != "온라인"
                or metric_value((device.get("metrics") or {}).get(field), percent=True) is None):
            return "감시 공백 · 현재 상태 미확인"
        return "경고 유지"


class AlertWindow:
    def __init__(self, parent):
        self.window = window(parent, "실시간 경고", "850x520", (690, 420))
        header(self.window, "실시간 경고")
        self.status = ttk.Label(self.window, style="Muted.TLabel")
        self.status.pack(anchor="w", padx=16, pady=8)
        tabs = ttk.Notebook(self.window)
        tabs.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.open_table = self.table(tabs, "열린 경고", ("PC ID", "경고 종류", "현재 상태"))
        self.event_table = self.table(tabs, "실시간 수신 내역", ("PC ID", "경고 종류", "전환", "서버 시각 (UTC)"))
        self.signature = None

    @staticmethod
    def table(tabs, title, columns):
        frame = ttk.Frame(tabs)
        tabs.add(frame, text=title)
        table = ttk.Treeview(frame, columns=columns, show="headings")
        for column in columns:
            table.heading(column, text=column)
            table.column(column, width=170, minwidth=95)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        table.pack(fill="both", expand=True)
        return table

    def refresh(self, store, devices):
        opened = tuple((row["pc_id"], ALERT_LABELS[row["kind"]], store.observation(row, devices))
                       for _, row in sorted(store.open.items()))
        recent = tuple((row["pc_id"], ALERT_LABELS[row["kind"]],
                        "발생" if row["state"] == "raised" else "정상 복귀", row["occurred_at"])
                       for row in store.recent)
        signature = (store.state, opened, recent)
        if signature == self.signature:
            return
        self.signature = signature
        self.status.configure(text=f"{store.state} · 열린 경고 {len(opened)}건")
        for table, rows in ((self.open_table, opened), (self.event_table, recent)):
            position = table.yview()[0]
            table.delete(*table.get_children())
            for row in rows:
                table.insert("", "end", values=row)
            table.yview_moveto(position)

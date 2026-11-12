"""Bounded per-PC history and rate-limited Matplotlib charts on the Tk thread."""
from collections import deque
from datetime import datetime, timezone
import math
import time
from tkinter import ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.dates import DateFormatter, date2num
from matplotlib.figure import Figure

FIELDS = ("cpu_percent", "ram_percent", "upload_mbps", "download_mbps")


def metric_value(value, percent=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return None
    return value if not percent or value <= 100 else None


class MetricHistory:
    def __init__(self, limit=180):
        if type(limit) is not int or limit < 1:
            raise ValueError("History limit must be a positive integer")
        self.limit = limit
        self.samples = {}
        self.interrupted = set()

    def disconnect(self):
        self.interrupted.update(self.samples)

    def update(self, devices):
        self.samples = {key: value for key, value in self.samples.items() if key in devices}
        self.interrupted.intersection_update(devices)
        for pc_id, device in devices.items():
            metrics = device.get("metrics") or {}
            if not device.get("online") or not metrics.get("timestamp"):
                self.interrupted.add(pc_id)
                continue
            try:
                stamp = datetime.fromisoformat(metrics["timestamp"].replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    continue
                seconds = stamp.timestamp()
            except (ValueError, TypeError, AttributeError, OverflowError):
                continue
            points = self.samples.setdefault(pc_id, deque(maxlen=self.limit))
            if points and seconds <= points[-1][0]:
                continue
            values = {field: metric_value(metrics.get(field), field in FIELDS[:2]) for field in FIELDS}
            values["timestamp"] = metrics["timestamp"]
            values["_gap_before"] = pc_id in self.interrupted
            self.interrupted.discard(pc_id)
            points.append((seconds, values))


def series(points, field):
    """NaNs break missing data and intervals over six seconds; zero stays zero."""
    x, y, previous = [], [], None
    for stamp, metrics in points:
        moment = date2num(datetime.fromtimestamp(stamp, timezone.utc))
        if previous is not None and (stamp - previous > 6 or metrics.get("_gap_before")):
            x.append(moment)
            y.append(float("nan"))
        x.append(moment)
        value = metrics.get(field)
        y.append(value if value is not None else float("nan"))
        previous = stamp
    return x, y


class LiveCharts(ttk.LabelFrame):
    def __init__(self, parent):
        super().__init__(parent, text="선택 PC · 최근 180개 측정 · 결측/단절 구간은 연결하지 않음")
        self.figure = Figure(figsize=(6.5, 3.6), dpi=100, layout="constrained")
        self.axes = self.figure.subplots(2, 1, sharex=True)
        self.lines = {}
        for axis, fields, ylabel in (
                (self.axes[0], (("cpu_percent", "CPU", "#2563eb"), ("ram_percent", "RAM", "#db2777")), "Usage (%)"),
                (self.axes[1], (("upload_mbps", "Upload", "#059669"), ("download_mbps", "Download", "#d97706")), "Network (Mbps)")):
            for field, label, color in fields:
                self.lines[field], = axis.plot([], [], label=label, color=color, linewidth=1.5, marker=".", markersize=3)
            axis.set_ylabel(ylabel)
            axis.grid(True, alpha=.25)
            axis.legend(loc="upper left", ncol=2, fontsize=8)
        self.axes[0].set_ylim(0, 100)
        self.axes[1].xaxis.set_major_formatter(DateFormatter("%H:%M:%S", tz=timezone.utc))
        self.axes[1].set_xlabel("Measurement time (UTC)")
        self.notice = ttk.Label(self, text="표시 PC ID: 선택 대기", font=("Segoe UI", 11, "bold"))
        self.notice.pack(side="top", fill="x", padx=8, pady=(4, 2))
        self.plot = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas = self.plot.get_tk_widget()
        self.canvas.pack(fill="both", expand=True)
        self.points, self.state, self.pc_id = (), "PC를 선택하세요", None
        self.signature = None
        self.last_draw = float("-inf")
        self.pending_draw = None
        self.bind("<Destroy>", self.on_destroy, add=True)

    def show(self, points, state, pc_id=None):
        self.points = tuple(points)
        signature = (pc_id, state, tuple((stamp, tuple(metrics.get(k) for k in (*FIELDS, "_gap_before")))
                                         for stamp, metrics in self.points))
        if signature == self.signature:
            return
        immediate = pc_id != self.pc_id or state != self.state
        self.pc_id, self.state, self.signature = pc_id, state, signature
        self.notice.configure(text=(f"표시 PC ID: {pc_id or '선택 대기'} · {state}" +
            (" · 이전 측정 기록" if self.points and state != "온라인" else "")))
        elapsed = time.monotonic() - self.last_draw
        if immediate or elapsed >= 1:
            self.draw()
        elif self.pending_draw is None:
            self.pending_draw = self.after(max(1, int((1 - elapsed) * 1000)), self.draw)

    def draw(self):
        if self.pending_draw is not None:
            self.after_cancel(self.pending_draw)
            self.pending_draw = None
        for field, line in self.lines.items():
            line.set_data(*series(self.points, field))
        if self.points:
            first, last = self.points[0][0], self.points[-1][0]
            if first == last:
                first, last = first - 2, last + 2
            self.axes[1].set_xlim(*[date2num(datetime.fromtimestamp(stamp, timezone.utc)) for stamp in (first, last)])
        else:
            self.axes[1].set_xlim(0, 1)
        traffic = [m[field] for _, m in self.points for field in FIELDS[2:] if m.get(field) is not None]
        self.axes[1].set_ylim(0, max(1, max(traffic, default=0) * 1.15))
        self.last_draw = time.monotonic()
        self.plot.draw_idle()

    def on_destroy(self, event):
        if event.widget is self:
            if self.pending_draw is not None:
                self.after_cancel(self.pending_draw)
                self.pending_draw = None
            idle = getattr(self.plot, "_idle_draw_id", None)
            if idle:
                self.after_cancel(idle)
                self.plot._idle_draw_id = None
            self.figure.clear()
